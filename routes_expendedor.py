import json

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from dispatch_engine import confirm_delivery, evaluate_dispatch_status, get_ready_to_complete_tickets, set_ticket_priority
from ledger import InsufficientStock, reserve_units
from models import MedicalOrder, MedicationPickupTicket, Notification, Pharmacy, ROLE_EXPENDOR, Stock, StockTransferRequest, User, db
from pharmacy_utils import (available_quantity, create_replenishment_alert,
                           ensure_default_pharmacy, estado_de_stock,
                           resolver_alerta_de_punto_de_reposicion,
                           alerta_por_punto_de_reposicion, stock_for_med)
from security import audit, pii_hash, role_required, verify_pickup_hash
from time_utils import colombia_now
import bleach

expendedor_bp = Blueprint('expendedor', __name__)


def _parse_medications(meds_json):
    try:
        raw = json.loads(meds_json or '[]')
    except json.JSONDecodeError:
        return []
    meds = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get('nombre_med') or item.get('medicamento') or item.get('name') or ''
        if not name:
            continue
        try:
            quantity = int(item.get('cantidad') or 1)
        except (TypeError, ValueError):
            quantity = 1
        meds.append({
            'nombre_med': name,
            'cantidad': max(1, quantity),
            'unidad': item.get('unidad') or 'unidad',
            'instrucciones': item.get('instrucciones') or '',
        })
    return meds


def _assigned_pharmacy():
    pharmacy = None
    if current_user.pharmacy_id:
        pharmacy = Pharmacy.query.filter_by(
            id=current_user.pharmacy_id,
            clinic_id=current_user.clinic_id,
            is_active=True,
        ).first()
    if pharmacy:
        return pharmacy
    pharmacy = ensure_default_pharmacy(current_user.clinic)
    current_user.pharmacy_id = pharmacy.id
    db.session.commit()
    return pharmacy


@expendedor_bp.route('/dashboard', methods=['GET', 'POST'])
@login_required
@role_required(ROLE_EXPENDOR)
def dashboard():
    pharmacy = _assigned_pharmacy()
    view_filter = request.args.get('filter', 'active') # active | pending | delivered | ready

    if request.method == 'POST' and request.form.get('action') == 'request_transfer':
        med_name = request.form.get('med_name')
        quantity = request.form.get('quantity', type=int) or 1
        source_id = request.form.get('source_pharmacy_id', type=int)
        source = Pharmacy.query.filter_by(id=source_id, clinic_id=current_user.clinic_id).first()
        if source and med_name:
            req = StockTransferRequest(
                clinic_id=current_user.clinic_id,
                source_pharmacy_id=source.id,
                target_pharmacy_id=pharmacy.id,
                requester_id=current_user.id,
                med_name=med_name,
                quantity=quantity,
                status='solicitada',
            )
            db.session.add(req)
            audit('stock_transfer_requested', details=f'source={source.id}; target={pharmacy.id}; med={med_name}; qty={quantity}')
            db.session.commit()
            flash('Solicitud de transferencia enviada al administrador.')
        return redirect(url_for('expendedor.dashboard'))

    if request.method == 'POST' and request.form.get('action') == 'set_punto_reposicion':
        # El expendedor fija el punto de reposicion de SU sede. Es quien sabe
        # cuanto se consume en ese mostrador; el administrador, desde la
        # cabecera, no lo sabe para cada vereda.
        stock_id = request.form.get('stock_id', type=int)
        minimo = max(0, request.form.get('cantidad_minima', type=int) or 0)
        item = Stock.query.filter_by(
            id=stock_id,
            clinic_id=current_user.clinic_id,
            pharmacy_id=pharmacy.id,
        ).first()
        if item is None:
            flash('Ese medicamento no está en el inventario de esta farmacia.')
        else:
            anterior = item.cantidad_minima or 0
            item.cantidad_minima = minimo
            db.session.flush()
            # Cambiar el umbral cambia el estado: puede abrir una alerta que
            # no existia, o cerrar una que ya no corresponde.
            alerta_por_punto_de_reposicion(item)
            resolver_alerta_de_punto_de_reposicion(item)
            audit('stock_minimo_actualizado',
                  details=f'stock_id={item.id}; antes={anterior}; ahora={minimo}')
            db.session.commit()
            flash(f'Punto de reposición de {item.nombre_med}: {minimo} {item.unidad}.')
        return redirect(url_for('expendedor.dashboard'))

    ticket = None
    meds = []
    query = (request.values.get('q') or '').strip()
    dispatch_status = None
    if query:
        filters = [
            MedicationPickupTicket.pickup_code == query,
            MedicationPickupTicket.pickup_hash == query,
        ]
        if query.isdigit():
            filters.append(MedicationPickupTicket.id == int(query))
        ticket = MedicationPickupTicket.query.filter(
            MedicationPickupTicket.clinic_id == current_user.clinic_id,
            or_(*filters),
        ).first()
        if not ticket:
            flash('Ticket no encontrado en esta clínica.')
        elif ticket.pickup_hash == query and not verify_pickup_hash(ticket, query):
            flash('Hash de entrega inválido.')
            ticket = None
        elif ticket.pharmacy_id and ticket.pharmacy_id != pharmacy.id:
            flash('Este ticket pertenece a otra farmacia. Verifique la sede asignada.')
            ticket = None
        elif ticket.status == 'entregado':
            flash(f'Ticket {ticket.pickup_code} ya fue entregado el {ticket.delivered_at}.')
        elif ticket.status == 'cancelado':
            flash(f'Ticket {ticket.pickup_code} está cancelado.')
        
        if ticket and ticket.status in ('autorizado', 'sin_stock', 'parcial'):
            # Dynamic re-validation of "Remaining - Stock"
            dispatch_status = evaluate_dispatch_status(ticket, pharmacy.id, ignore_commitments=True)
            meds = dispatch_status['deliverable'] + dispatch_status['pending']

    # Filtered lists for the sidebar/main view
    recent_query = MedicationPickupTicket.query.filter(
        MedicationPickupTicket.clinic_id == current_user.clinic_id,
        MedicationPickupTicket.pharmacy_id == pharmacy.id
    )
    
    if view_filter == 'active':
        recent = recent_query.filter(MedicationPickupTicket.status.in_(['autorizado', 'parcial'])).order_by(MedicationPickupTicket.priority.asc(), MedicationPickupTicket.created_at.desc()).limit(15).all()
    elif view_filter == 'pending':
        recent = recent_query.filter(MedicationPickupTicket.status == 'sin_stock').order_by(MedicationPickupTicket.priority.asc(), MedicationPickupTicket.created_at.asc()).limit(20).all()
    elif view_filter == 'delivered':
        recent = recent_query.filter(MedicationPickupTicket.status == 'entregado').order_by(MedicationPickupTicket.delivered_at.desc()).limit(20).all()
    else:
        recent = recent_query.order_by(MedicationPickupTicket.created_at.desc()).limit(15).all()

    stock_items = [
        {'stock': fila,
         'disponible': available_quantity(fila),
         'estado': estado_de_stock(fila)}
        for fila in Stock.query.filter_by(
            clinic_id=current_user.clinic_id, pharmacy_id=pharmacy.id
        ).order_by(Stock.nombre_med.asc()).all()
    ]
    source_stock_items = Stock.query.filter(
        Stock.clinic_id == current_user.clinic_id,
        Stock.pharmacy_id != pharmacy.id,
        Stock.cantidad > Stock.cantidad_comprometida,
    ).order_by(Stock.nombre_med.asc()).all()
    
    transfer_requests = StockTransferRequest.query.filter_by(
        clinic_id=current_user.clinic_id,
        target_pharmacy_id=pharmacy.id,
    ).order_by(StockTransferRequest.created_at.desc()).limit(10).all()
    
    ready_tickets = []
    if view_filter == 'ready':
        ready_tickets = get_ready_to_complete_tickets(current_user.clinic_id, pharmacy.id)
        recent = [item['ticket'] for item in ready_tickets]
        
    today_str = colombia_now().strftime('%Y-%m-%d')
    today_tickets = [t for t in recent if t.pickup_date == today_str and t.status in ('autorizado', 'parcial')]
    
    return render_template(
        'expendedor_dashboard.html',
        ticket=ticket,
        meds=meds,
        dispatch_status=dispatch_status,
        recent=recent,
        today_tickets=today_tickets,
        ready_tickets=ready_tickets,
        view_filter=view_filter,
        query=query,
        pharmacy=pharmacy,
        stock_items=stock_items,
        source_stock_items=source_stock_items,
        transfer_requests=transfer_requests,
    )


@expendedor_bp.route('/confirm/<int:ticket_id>', methods=['POST'])
@login_required
@role_required(ROLE_EXPENDOR)
def confirm(ticket_id):
    """Confirm delivery — handles full, partial, and no-stock cases via dispatch_engine."""
    pharmacy = _assigned_pharmacy()
    ticket = MedicationPickupTicket.query.filter_by(
        id=ticket_id,
        clinic_id=current_user.clinic_id,
    ).first_or_404()

    if ticket.pharmacy_id and ticket.pharmacy_id != pharmacy.id:
        flash('Este ticket pertenece a otra farmacia. Verifique la sede asignada.')
        return redirect(url_for('expendedor.dashboard'))

    # Un ticket sin sede asignada lo podia entregar cualquier expendedor de la
    # clinica. Se ata a la sede donde efectivamente se presenta, para que la
    # trazabilidad diga donde ocurrio la entrega.
    if not ticket.pharmacy_id:
        ticket.pharmacy_id = pharmacy.id

    # La orden que respalda el ticket tiene que seguir vigente. El motor lo
    # vuelve a comprobar bajo bloqueo; aqui se comprueba antes para poder dar
    # un mensaje util sin haber empezado nada.
    if ticket.order_id and ticket.order is not None and not ticket.order.is_dispensable:
        flash('La orden que respalda este ticket fue anulada o venció. '
              'No se puede entregar; consulte con el profesional que la emitió.')
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    if ticket.status not in ('autorizado', 'parcial'):
        if ticket.status == 'sin_stock':
            flash('Sin existencias. Usa "Reactivar" cuando se reponga el inventario.')
        elif ticket.status == 'entregado':
            flash('Este ticket ya fue entregado.')
        else:
            flash(f'Estado de ticket no valido para entrega: {ticket.status}.')
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    # --- Verificacion de identidad de quien retira ---------------------------
    # Antes se confirmaba la entrega sin contrastar a quien se le entregaba. Para
    # medicamentos —y en especial los de control especial— la constancia de quien
    # retiro es parte de la trazabilidad exigida.
    receiver_kind = (request.form.get('receiver_kind') or 'paciente').strip().lower()
    if receiver_kind not in {'paciente', 'tercero'}:
        receiver_kind = 'paciente'

    receiver_document = (request.form.get('receiver_document') or '').strip()
    receiver_name = bleach.clean((request.form.get('receiver_name') or '').strip())[:180]
    identity_verified = request.form.get('identity_verified') == 'on'

    if not identity_verified:
        flash(
            'Debe confirmar que verifico el documento de identidad de quien '
            'retira antes de registrar la entrega.'
        )
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    if receiver_kind == 'paciente' and receiver_document:
        # El documento presentado se contrasta contra el indice ciego del paciente:
        # se compara sin que el documento en claro llegue a almacenarse.
        if pii_hash(receiver_document) != ticket.patient.cedula_hash:
            audit(
                'delivery_identity_mismatch',
                details=f'ticket_id={ticket.id}',
            )
            db.session.commit()
            flash(
                'El documento presentado no coincide con el del paciente titular del '
                'ticket. Si retira otra persona, registrala como tercero autorizado.'
            )
            return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    if receiver_kind == 'tercero' and not (receiver_name and receiver_document):
        flash('Para una entrega a tercero debe registrar su nombre y su documento.')
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    # `ignore_commitments=True` se retiro: ignorar el stock ya reservado permitia
    # entregar a este paciente unidades apartadas para otro que ya tenia ticket.
    dispatch_eval = evaluate_dispatch_status(ticket, pharmacy.id)

    if dispatch_eval['status'] == 'none':
        for med in dispatch_eval['pending'] + dispatch_eval['deliverable']:
            stock = stock_for_med(current_user.clinic_id, med['nombre_med'], pharmacy.id)
            create_replenishment_alert(
                current_user.clinic_id, pharmacy.id, med, available_quantity(stock),
                order_id=ticket.order_id, ticket_id=ticket.id,
                note='Intento de entrega sin existencias disponibles.',
            )
        ticket.status = 'sin_stock'
        db.session.commit()
        flash('Sin existencias disponibles. El ticket quedo marcado como "Sin Stock" y el paciente fue notificado.')
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    try:
        result = confirm_delivery(
            ticket, pharmacy, current_user, dispatch_eval,
            receiver_kind=receiver_kind,
            receiver_name=receiver_name or None,
            receiver_document=receiver_document or None,
            identity_verified=identity_verified,
        )
    except SQLAlchemyError:
        current_app.logger.exception('Fallo al confirmar la entrega del ticket %s', ticket.id)
        flash(
            'No fue posible registrar la entrega. No se descontaron existencias. '
            'Vuelve a intentarlo.'
        )
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    # El motor revalida bajo bloqueo y puede negarse por algo que cambio entre
    # la pantalla y el envio. Ese motivo tiene que llegar al expendedor: si no,
    # ve que "no paso nada" y lo intenta otra vez.
    if result.get('motivo'):
        flash(result['motivo'])
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    if result.get('shortfalls'):
        detalle = ', '.join(
            f"{item['nombre_med']} ({item['disponible']}/{item['solicitado']})"
            for item in result['shortfalls']
        )
        flash(f'Algunas existencias se agotaron durante la entrega: {detalle}.')

    if result['status'] == 'sin_stock':
        flash('No habia existencias al momento de confirmar. No se descontó nada.')
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))
    if result['status'] == 'parcial':
        flash('Entrega parcial registrada. El ticket sigue activo por lo pendiente.')
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    flash('Entrega completa registrada. El ticket quedo cerrado.')
    return redirect(url_for('expendedor.dashboard'))


@expendedor_bp.route('/reactivate/<int:ticket_id>', methods=['POST'])
@login_required
@role_required(ROLE_EXPENDOR)
def reactivate(ticket_id):
    """Reactivate a sin_stock ticket when stock has been replenished."""
    pharmacy = _assigned_pharmacy()
    ticket = MedicationPickupTicket.query.filter_by(
        id=ticket_id,
        clinic_id=current_user.clinic_id,
        status='sin_stock',
    ).first_or_404()

    dispatch_eval = evaluate_dispatch_status(ticket, pharmacy.id)

    if not dispatch_eval['is_ready']:
        flash('Aun no hay existencias disponibles en esta farmacia para lo pendiente.')
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    # La reserva pasa por el libro mayor, con bloqueo de fila. Mutar
    # `cantidad_comprometida` a mano permitia que dos reactivaciones simultaneas
    # reservaran las mismas unidades para pacientes distintos.
    try:
        for med in dispatch_eval['deliverable']:
            reserve_units(
                clinic_id=current_user.clinic_id,
                med_name=med['nombre_med'],
                quantity=med.get('deliver_qty', med['cantidad']),
                pharmacy_id=pharmacy.id,
                performed_by_id=current_user.id,
                ticket_id=ticket.id,
                order_id=ticket.order_id,
                reason=f'Reactivacion del ticket {ticket.pickup_code}',
            )
    except InsufficientStock as error:
        db.session.rollback()
        flash(f'No fue posible reservar: {error}')
        return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))

    ticket.status = 'autorizado'
    ticket.pharmacy_id = pharmacy.id
    db.session.commit()

    pharmacy_name = pharmacy.name if pharmacy else 'la farmacia'
    db.session.add(Notification(
        user_id=ticket.patient_id,
        clinic_id=current_user.clinic_id,
        title='Medicamentos listos para recoger',
        message=(f'Su ticket {ticket.pickup_code} está listo en {pharmacy_name}. '
                 f'Fecha: {ticket.pickup_date} a las {ticket.pickup_time}.'),
        type='ticket_ready',
    ))
    audit('pickup_ticket_reactivated', details=f'ticket_id={ticket.id}')
    db.session.commit()
    flash(f'Ticket {ticket.pickup_code} reactivado. El paciente fue notificado.')
    return redirect(url_for('expendedor.dashboard', q=ticket.pickup_code))


@expendedor_bp.route('/toggle_priority/<int:ticket_id>', methods=['POST'])
@login_required
@role_required(ROLE_EXPENDOR)
def toggle_priority(ticket_id):
    """Toggle ticket priority between 'normal' and 'alta'."""
    ticket = MedicationPickupTicket.query.filter_by(
        id=ticket_id,
        clinic_id=current_user.clinic_id,
    ).first_or_404()
    new_priority = 'normal' if ticket.priority == 'alta' else 'alta'
    set_ticket_priority(ticket_id, current_user.clinic_id, new_priority)
    db.session.commit()
    flash(f'Prioridad del ticket {ticket.pickup_code} cambiada a {new_priority.upper()}.')
    return redirect(url_for('expendedor.dashboard'))

