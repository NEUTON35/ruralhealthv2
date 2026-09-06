import json
import secrets
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models import (
    Appointment,
    Chat,
    IncomingShipment,
    IncomingShipmentItem,
    MedicalOrder,
    MedicationPickupTicket,
    Message,
    Notification,
    Pharmacy,
    ReplenishmentAlert,
    ROLE_PATIENT,
    ROLE_RECEPTIONIST,
    ROLE_STAFF,
    STAFF_ROLE_VALUES,
    Stock,
    User,
    db,
)
from pharmacy_utils import (
    available_quantity,
    build_stock_matrix,
    create_replenishment_alert,
    ensure_default_pharmacy,
    nearest_pharmacy,
    pharmacies_for_clinic,
    stock_for_med,
)
from security import audit, generate_pickup_hash, role_required, verify_order_hash
from time_utils import colombia_now
from dispatch_engine import reserve_stock_for_pending_tickets
import bleach

staff_bp = Blueprint('staff', __name__)


def _parse_medications(meds_json):
    try:
        raw_meds = json.loads(meds_json or '[]')
    except json.JSONDecodeError:
        return []

    if isinstance(raw_meds, dict):
        raw_meds = [{'nombre_med': name, 'cantidad': quantity} for name, quantity in raw_meds.items()]

    meds = []
    for item in raw_meds if isinstance(raw_meds, list) else []:
        if isinstance(item, str):
            name = item.strip()
            quantity = 1
            unit = 'unidad'
            instructions = ''
        elif isinstance(item, dict):
            name = (
                item.get('nombre_med')
                or item.get('nombre')
                or item.get('name')
                or item.get('med')
                or item.get('medicamento')
                or ''
            ).strip()
            quantity = item.get('cantidad') or item.get('quantity') or item.get('qty') or 1
            unit = (item.get('unidad') or item.get('unit') or 'unidad')[:40]
            instructions = (item.get('instrucciones') or item.get('instructions') or '')[:500]
        else:
            continue

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1

        if name and quantity > 0:
            meds.append({'nombre_med': name[:180], 'cantidad': quantity, 'unidad': unit, 'instrucciones': instructions})
    return meds


def _is_user_on_duty(user):
    start = user.atencion_inicio or '08:00'
    end = user.atencion_fin or '17:00'
    return start <= colombia_now().strftime('%H:%M') <= end


def _validate_order_hash(order):
    doctor = db.session.get(User, order.doctor_id)
    patient = db.session.get(User, order.patient_id)
    if not doctor or not patient or doctor.role != 'doctor':
        return False
    if doctor.clinic_id != current_user.clinic_id or patient.clinic_id != current_user.clinic_id:
        return False
    token = order.hash_seguridad or order.verification_hash
    return verify_order_hash(order.doctor_id, order.patient_id, token, order.meds_json, order.clinic_id)


def _meds_json(meds):
    return json.dumps(meds, ensure_ascii=False, sort_keys=True)


def _build_shortages(clinic_id, meds, pharmacy_id):
    shortages = []
    stock_rows = {}
    for med in meds:
        stock = stock_for_med(clinic_id, med['nombre_med'], pharmacy_id)
        stock_rows[med['nombre_med']] = stock
        available = available_quantity(stock)
        if available < med['cantidad']:
            shortages.append(f"{med['nombre_med']} ({available}/{med['cantidad']} {med['unidad']})")
    return shortages, stock_rows


def _create_pickup_ticket(order, patient_id, meds, pickup_date, pickup_time, pharmacy, stock_rows, shortages=None):
    shortages = shortages or []
    all_shortage = bool(shortages) and len(shortages) == len(meds)  # ALL meds are missing
    for med in meds:
        stock = stock_rows.get(med['nombre_med'])
        # Only commit stock if there IS available stock for this med
        if stock and available_quantity(stock) >= med['cantidad']:
            stock.cantidad_comprometida = (stock.cantidad_comprometida or 0) + med['cantidad']

    pickup_code = secrets.token_urlsafe(8).replace('-', '').replace('_', '')[:12].upper()
    location = pharmacy.address or pharmacy.name if pharmacy else current_user.clinic.location or current_user.clinic.name
    if all_shortage:
        note = 'SIN STOCK: Esta farmacia no tiene los medicamentos disponibles. Será notificado cuando estén listos.'
        ticket_status = 'sin_stock'
    elif shortages:
        note = 'Stock parcial. Algunos medicamentos requieren reposición: ' + ', '.join(shortages)
        ticket_status = 'autorizado'
    else:
        note = 'Por favor, presentarse 30 minutos antes en caso de filas.'
        ticket_status = 'autorizado'
    ticket = MedicationPickupTicket(
        clinic_id=current_user.clinic_id,
        order_id=order.id if order else None,
        pharmacy_id=pharmacy.id if pharmacy else None,
        patient_id=patient_id,
        staff_id=current_user.id,
        pickup_code=pickup_code,
        pickup_hash='pending',
        meds_json=_meds_json(meds),
        pickup_date=pickup_date,
        pickup_time=pickup_time,
        pickup_location=location,
        note=note,
        status=ticket_status,
    )
    db.session.add(ticket)
    db.session.flush()
    ticket.pickup_hash = generate_pickup_hash(ticket.id, ticket.order_id, ticket.patient_id, ticket.clinic_id, ticket.pickup_code)
    if shortages and pharmacy:
        for med in meds:
            stock = stock_rows.get(med['nombre_med'])
            available = available_quantity(stock)
            if available < med['cantidad']:
                create_replenishment_alert(
                    current_user.clinic_id,
                    pharmacy.id,
                    med,
                    available,
                    order_id=order.id if order else None,
                    ticket_id=ticket.id,
                    note='Ticket generado por recepcion con faltante de stock.',
                )
    # Notify patient about their ticket
    pharmacy_name = pharmacy.name if pharmacy else current_user.clinic.name
    if ticket_status == 'sin_stock':
        patient_msg = (f'⚠️ Tu ticket {pickup_code} fue generado pero no hay stock disponible en {pharmacy_name}. '
                       'El personal está tramitando la reposición. Te notificaremos cuando estén listos.')
        patient_title = '⚠️ Ticket pendiente de stock'
    else:
        meds_summary = ', '.join(f"{m['nombre_med']} x{m['cantidad']}" for m in meds[:3])
        patient_msg = (f'✅ Tu ticket de recogida {pickup_code} está listo. '
                       f'Medicamentos: {meds_summary}{" y más" if len(meds) > 3 else ""}. '
                       f'Fecha: {pickup_date} {pickup_time} en {pharmacy_name}. '
                       f'Lleva tu código o hash al mostrador.')
        patient_title = '💊 Medicamentos listos para recoger'
    patient_notif = Notification(
        user_id=patient_id,
        clinic_id=current_user.clinic_id,
        title=patient_title,
        message=patient_msg,
        type='ticket_ready' if ticket_status == 'autorizado' else 'stock_alert',
    )
    db.session.add(patient_notif)
    return ticket


def _create_pending_delivery(order, patient_id, doctor_id, shortages):
    reclaim_at = colombia_now() + timedelta(days=7)
    appointment = Appointment(
        clinic_id=current_user.clinic_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        date=reclaim_at.strftime('%Y-%m-%d'),
        time='09:00',
        appointment_type='Entrega Pendiente',
        description='Entrega Pendiente: stock insuficiente para ' + ', '.join(shortages),
        status='pending',
    )
    db.session.add(appointment)
    if order:
        order.status = 'pendiente_stock'
    return appointment


def _order_row(order):
    meds = _parse_medications(order.meds_json)
    return {
        'order': order,
        'meds': meds,
        'pharmacy_rows': build_stock_matrix(order.clinic_id, meds, order.patient),
    }


def _pharmacy_or_default(pharmacy_id=None):
    pharmacy = None
    if pharmacy_id:
        pharmacy = Pharmacy.query.filter_by(
            id=pharmacy_id,
            clinic_id=current_user.clinic_id,
            is_active=True,
        ).first()
    if pharmacy:
        return pharmacy
    pharmacies = pharmacies_for_clinic(current_user.clinic_id, active_only=True)
    if pharmacies:
        return pharmacies[0]
    pharmacy = ensure_default_pharmacy(current_user.clinic)
    db.session.commit()
    return pharmacy


def _create_ticket_for_pharmacy(order, meds, pharmacy, pickup_date, pickup_time):
    shortages, stock_rows = _build_shortages(current_user.clinic_id, meds, pharmacy.id)
    return _create_pickup_ticket(order, order.patient_id, meds, pickup_date, pickup_time, pharmacy, stock_rows, shortages), shortages


def _split_meds_by_availability(order, meds):
    pharmacies = pharmacies_for_clinic(current_user.clinic_id, active_only=True, patient=order.patient)
    if not pharmacies:
        pharmacies = [ensure_default_pharmacy(current_user.clinic)]
        db.session.commit()

    groups = {}
    nearest = nearest_pharmacy(pharmacies, order.patient) or pharmacies[0]
    for med in meds:
        chosen = None
        for pharmacy in pharmacies:
            stock = stock_for_med(current_user.clinic_id, med['nombre_med'], pharmacy.id)
            if available_quantity(stock) >= med['cantidad']:
                chosen = pharmacy
                break
        chosen = chosen or nearest
        groups.setdefault(chosen.id, {'pharmacy': chosen, 'meds': []})['meds'].append(med)
    return list(groups.values())


@staff_bp.route('/dashboard', methods=['GET', 'POST'])
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def dashboard():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_hours':
            start = request.form.get('atencion_inicio') or '08:00'
            end = request.form.get('atencion_fin') or '17:00'
            if start >= end:
                flash('La hora de inicio debe ser menor que la hora de fin.')
            else:
                current_user.atencion_inicio = start
                current_user.atencion_fin = end
                audit('staff_hours_updated', details=f'{start}-{end}')
                db.session.commit()
                flash('Horario de atencion actualizado.')
        return redirect(url_for('staff.dashboard'))

    now = colombia_now()
    expired_orders = MedicalOrder.query.filter(
        MedicalOrder.clinic_id == current_user.clinic_id,
        MedicalOrder.status == 'pendiente',
        MedicalOrder.expires_at < now,
    ).all()
    for order in expired_orders:
        order.status = 'vencido'
    if expired_orders:
        audit('medical_orders_marked_expired', details=f'count={len(expired_orders)}')
        db.session.commit()

    orders = MedicalOrder.query.filter(
        MedicalOrder.clinic_id == current_user.clinic_id,
        MedicalOrder.status.in_(('pendiente', 'pendiente_stock')),
    ).order_by(MedicalOrder.created_at.asc()).all()
    rows = [_order_row(order) for order in orders]

    chats = Chat.query.filter_by(clinic_id=current_user.clinic_id, doctor_id=current_user.id, status='open').order_by(Chat.timestamp.desc()).all()
    chat_rows = []
    for chat in chats:
        unread = Message.query.filter_by(clinic_id=current_user.clinic_id, chat_id=chat.id, sender_id=chat.patient_id, is_read=False).count()
        last_msg = Message.query.filter_by(clinic_id=current_user.clinic_id, chat_id=chat.id).order_by(Message.timestamp.desc()).first()
        chat_rows.append({'chat': chat, 'unread': unread, 'last_msg': last_msg})
    return render_template('staff_dashboard.html', orders=rows, staff_chats=chat_rows)


@staff_bp.route('/verify_order/<verification_hash>')
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def verify_order(verification_hash):
    order = MedicalOrder.query.filter(
        MedicalOrder.clinic_id == current_user.clinic_id,
        (MedicalOrder.verification_hash == verification_hash) | (MedicalOrder.hash_seguridad == verification_hash),
    ).first_or_404()

    if order.expires_at < colombia_now():
        order.status = 'vencido'
        audit('medical_order_expired_on_verify', details=f'order_id={order.id}')
        db.session.commit()
        flash('La orden esta vencida.')
        return redirect(url_for('staff.dashboard'))

    if not _validate_order_hash(order):
        audit('medical_order_hash_rejected', details=f'order_id={order.id}')
        abort(403)

    audit('medical_order_verified', details=f'order_id={order.id}')
    db.session.commit()
    return render_template(
        'staff_dashboard.html',
        orders=[_order_row(order)],
        staff_chats=[],
        verified_order=order,
    )


@staff_bp.route('/dispense/<int:order_id>', methods=['POST'])
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def dispense(order_id):
    order = MedicalOrder.query.filter_by(id=order_id, clinic_id=current_user.clinic_id).first_or_404()
    if order.status not in {'pendiente', 'pendiente_stock'}:
        flash('Esta orden ya no esta disponible para autorizar.')
        return redirect(url_for('staff.dashboard'))
    if order.expires_at < colombia_now():
        order.status = 'vencido'
        audit('medical_order_expired_on_dispense', details=f'order_id={order.id}')
        db.session.commit()
        flash('La orden esta vencida.')
        return redirect(url_for('staff.dashboard'))
    if not _validate_order_hash(order):
        audit('medical_order_hash_rejected', details=f'order_id={order.id}')
        abort(403)

    meds = _parse_medications(order.meds_json)
    pickup_date = request.form.get('pickup_date') or colombia_now().strftime('%Y-%m-%d')
    pickup_time = request.form.get('pickup_time') or (colombia_now() + timedelta(hours=1)).strftime('%H:%M')
    strategy = request.form.get('fulfillment_strategy') or 'nearest'
    selected_pharmacy_id = request.form.get('pharmacy_id', type=int)
    created_tickets = []
    all_shortages = []

    if strategy == 'split':
        for group in _split_meds_by_availability(order, meds):
            ticket, shortages = _create_ticket_for_pharmacy(order, group['meds'], group['pharmacy'], pickup_date, pickup_time)
            created_tickets.append(ticket)
            all_shortages.extend(shortages)
    else:
        pharmacies = pharmacies_for_clinic(current_user.clinic_id, active_only=True, patient=order.patient)
        if strategy == 'selected':
            pharmacy = _pharmacy_or_default(selected_pharmacy_id)
        else:
            pharmacy = nearest_pharmacy(pharmacies, order.patient) or _pharmacy_or_default()
        ticket, all_shortages = _create_ticket_for_pharmacy(order, meds, pharmacy, pickup_date, pickup_time)
        created_tickets.append(ticket)

    if not created_tickets:
        flash('No fue posible generar tickets para esta orden.')
        return redirect(url_for('staff.dashboard'))

    order.status = 'autorizada'
    audit('pickup_ticket_created', details=f'order_id={order.id}; tickets={[ticket.id for ticket in created_tickets]}; shortages={all_shortages}')
    db.session.commit()
    if all_shortages:
        flash('Ticket generado. Hay medicamentos sin stock y se crearon alertas de reposicion.')
    elif len(created_tickets) > 1:
        flash('Orden dividida en varios tickets de recogida.')
    else:
        flash('Ticket de recogida generado y stock apartado.')
    return redirect(url_for('staff.ticket', ticket_id=created_tickets[0].id))


@staff_bp.route('/ticket/<int:ticket_id>')
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def ticket(ticket_id):
    ticket_obj = MedicationPickupTicket.query.filter_by(id=ticket_id, clinic_id=current_user.clinic_id).first_or_404()
    related_tickets = []
    if ticket_obj.order_id:
        related_tickets = MedicationPickupTicket.query.filter_by(
            clinic_id=current_user.clinic_id,
            order_id=ticket_obj.order_id,
        ).order_by(MedicationPickupTicket.id.asc()).all()
    return render_template('pickup_ticket.html', ticket=ticket_obj, meds=_parse_medications(ticket_obj.meds_json), related_tickets=related_tickets)


@staff_bp.route('/order/<int:order_id>/map')
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def order_map(order_id):
    order = MedicalOrder.query.filter_by(id=order_id, clinic_id=current_user.clinic_id).first_or_404()
    meds = _parse_medications(order.meds_json)
    return render_template(
        'staff_order_map.html',
        order=order,
        meds=meds,
        pharmacy_rows=build_stock_matrix(current_user.clinic_id, meds, order.patient),
    )


@staff_bp.route('/inventory', methods=['GET', 'POST'])
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def inventory():
    default_pharmacy = ensure_default_pharmacy(current_user.clinic)
    db.session.commit()
    pharmacies = Pharmacy.query.filter_by(clinic_id=current_user.clinic_id).order_by(Pharmacy.name.asc()).all()
    pharmacy_ids = {pharmacy.id for pharmacy in pharmacies}
    if request.method == 'POST':
        stock_id = request.form.get('stock_id', type=int)
        pharmacy_id = request.form.get('pharmacy_id', type=int) or default_pharmacy.id
        if pharmacy_id not in pharmacy_ids:
            pharmacy_id = default_pharmacy.id
        name = (request.form.get('nombre_med') or '').strip()[:180]
        quantity = max(0, request.form.get('cantidad', type=int) or 0)
        unit = (request.form.get('unidad') or 'unidad').strip()[:40] or 'unidad'

        item = Stock.query.filter_by(id=stock_id, clinic_id=current_user.clinic_id).first() if stock_id else None
        qty_added = 0
        if item:
            qty_added = quantity - item.cantidad
            item.nombre_med = name or item.nombre_med
            item.medicamento = name or item.medicamento
            item.cantidad = quantity
            item.pharmacy_id = pharmacy_id
            item.unidad = unit
            audit('stock_updated', details=f'stock_id={item.id}')
        elif name:
            qty_added = quantity
            item = Stock(clinic_id=current_user.clinic_id, pharmacy_id=pharmacy_id, nombre_med=name, medicamento=name, cantidad=quantity, unidad=unit)
            db.session.add(item)
            audit('stock_created', details=f'name={name}')

        db.session.flush()
        if qty_added > 0 and item:
            reactivated = reserve_stock_for_pending_tickets(current_user.clinic_id, pharmacy_id, item.nombre_med, qty_added)
            if reactivated:
                flash(f'Stock actualizado. Se reactivaron automáticamente {len(reactivated)} ticket(s) en espera.')
            else:
                flash('Stock actualizado.')
        else:
            flash('Stock actualizado.')

        db.session.commit()
        return redirect(url_for('staff.inventory'))

    items = Stock.query.filter_by(clinic_id=current_user.clinic_id).join(Pharmacy, Stock.pharmacy_id == Pharmacy.id).order_by(Pharmacy.name.asc(), Stock.nombre_med.asc()).all()
    return render_template('inventory.html', stock_items=items, pharmacies=pharmacies, default_pharmacy=default_pharmacy)


@staff_bp.route('/start_chat', methods=['GET', 'POST'])
@login_required
@role_required(ROLE_PATIENT)
def start_chat():
    staff_user = User.query.filter(User.clinic_id == current_user.clinic_id, User.role.in_(STAFF_ROLE_VALUES)).order_by(User.id.asc()).first()
    if not staff_user:
        flash('Esta clinica aun no tiene staff para consultas de medicamentos.')
        return redirect(url_for('patient.dashboard'))
    if not _is_user_on_duty(staff_user):
        flash('El personal está fuera de su horario laboral.')
        return redirect(url_for('patient.dashboard'))

    chat = Chat.query.filter_by(clinic_id=current_user.clinic_id, patient_id=current_user.id, doctor_id=staff_user.id, status='open').first()
    if not chat:
        chat = Chat(clinic_id=current_user.clinic_id, patient_id=current_user.id, doctor_id=staff_user.id, reason='Consulta de medicamentos', mode='staff')
        db.session.add(chat)
        audit('staff_chat_started', details=f'staff_id={staff_user.id}')
        db.session.commit()
    return redirect(url_for('patient.chat', chat_id=chat.id))


@staff_bp.route('/chat/<int:chat_id>', methods=['GET', 'POST'])
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def chat(chat_id):
    chat_obj = Chat.query.filter_by(id=chat_id, clinic_id=current_user.clinic_id, doctor_id=current_user.id).first_or_404()

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'close':
            chat_obj.status = 'closed'
            chat_obj.closed_by = 'staff'
            audit('staff_chat_closed', details=f'chat_id={chat_obj.id}')
        elif action == 'create_pickup_ticket':
            selected_ids = request.form.getlist('stock_id')
            groups = {}
            for stock_id in selected_ids:
                stock = Stock.query.filter_by(id=int(stock_id), clinic_id=current_user.clinic_id).first()
                quantity = request.form.get(f'qty_{stock_id}', type=int) or 0
                if stock and quantity > 0:
                    pharmacy = stock.pharmacy or _pharmacy_or_default()
                    group = groups.setdefault(pharmacy.id, {'pharmacy': pharmacy, 'meds': [], 'stock_rows': {}})
                    med = {'nombre_med': stock.nombre_med, 'medicamento': stock.nombre_med, 'cantidad': quantity, 'unidad': stock.unidad, 'instrucciones': 'Autorizado por staff'}
                    group['meds'].append(med)
                    group['stock_rows'][stock.nombre_med] = stock
            if not groups:
                flash('Selecciona al menos un medicamento.')
            else:
                pickup_date = request.form.get('pickup_date') or colombia_now().strftime('%Y-%m-%d')
                pickup_time = request.form.get('pickup_time') or (colombia_now() + timedelta(hours=1)).strftime('%H:%M')
                tickets = []
                all_shortages = []
                for group in groups.values():
                    shortages = []
                    for med in group['meds']:
                        stock = group['stock_rows'].get(med['nombre_med'])
                        if available_quantity(stock) < med['cantidad']:
                            shortages.append(f"{med['nombre_med']} ({available_quantity(stock)}/{med['cantidad']} {med['unidad']})")
                    ticket_obj = _create_pickup_ticket(None, chat_obj.patient_id, group['meds'], pickup_date, pickup_time, group['pharmacy'], group['stock_rows'], shortages)
                    tickets.append(ticket_obj)
                    all_shortages.extend(shortages)
                db.session.add(Message(
                    clinic_id=current_user.clinic_id,
                    chat_id=chat_obj.id,
                    sender_id=current_user.id,
                    content=f'Ticket(s) de recogida {", ".join(ticket.pickup_code for ticket in tickets)} generados para {pickup_date} {pickup_time}.',
                ))
                audit('pickup_ticket_created_from_chat', details=f'tickets={[ticket.id for ticket in tickets]}; chat_id={chat_obj.id}; shortages={all_shortages}')
                db.session.commit()
                if all_shortages:
                    flash('Se genero ticket y alerta de reposicion para el faltante.')
                return redirect(url_for('staff.ticket', ticket_id=tickets[0].id))
        else:
            content = bleach.clean((request.form.get('content') or '').strip()[:5000])
            if content:
                db.session.add(Message(clinic_id=current_user.clinic_id, chat_id=chat_obj.id, sender_id=current_user.id, content=content))
                audit('staff_chat_message_sent', details=f'chat_id={chat_obj.id}')
        db.session.commit()
        return redirect(url_for('staff.chat', chat_id=chat_obj.id))

    unread = Message.query.filter_by(clinic_id=current_user.clinic_id, chat_id=chat_obj.id, sender_id=chat_obj.patient_id, is_read=False).all()
    for message in unread:
        message.is_read = True
    db.session.commit()

    messages = Message.query.filter_by(clinic_id=current_user.clinic_id, chat_id=chat_obj.id).order_by(Message.timestamp.asc()).all()
    stock_items = Stock.query.filter_by(clinic_id=current_user.clinic_id).join(Pharmacy, Stock.pharmacy_id == Pharmacy.id).order_by(Pharmacy.name.asc(), Stock.nombre_med.asc()).all()
    patient_orders = MedicalOrder.query.filter(
        MedicalOrder.clinic_id == current_user.clinic_id,
        MedicalOrder.patient_id == chat_obj.patient_id,
        MedicalOrder.status.in_(('pendiente', 'pendiente_stock')),
    ).order_by(MedicalOrder.created_at.desc()).all()
    return render_template('staff_chat.html', chat=chat_obj, messages=messages, stock_items=stock_items, patient_orders=patient_orders)


# ─────────────────────────────────────────────────────────────────────────────
# PANEL DE FALTANTES — Pending Tickets Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@staff_bp.route('/pendientes', methods=['GET', 'POST'])
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def pendientes():
    """Dashboard showing incomplete/pending medication tickets with delay tracking."""
    if request.method == 'POST':
        action = request.form.get('action')
        ticket_id = request.form.get('ticket_id', type=int)
        if action == 'set_priority' and ticket_id:
            ticket = MedicationPickupTicket.query.filter_by(
                id=ticket_id, clinic_id=current_user.clinic_id
            ).first()
            if ticket:
                from dispatch_engine import set_ticket_priority
                new_priority = request.form.get('priority', 'alta')
                set_ticket_priority(ticket_id, current_user.clinic_id, new_priority)
                db.session.commit()
                flash(f'Prioridad del ticket {ticket.pickup_code} cambiada a {new_priority.upper()}.')
        elif action == 'reactivate' and ticket_id:
            ticket = MedicationPickupTicket.query.filter_by(
                id=ticket_id, clinic_id=current_user.clinic_id
            ).first()
            if ticket:
                from dispatch_engine import evaluate_dispatch_status
                pharmacy_id = ticket.pharmacy_id or ensure_default_pharmacy(current_user.clinic).id
                dispatch_eval = evaluate_dispatch_status(ticket, pharmacy_id, ignore_commitments=True)
                if dispatch_eval['is_ready']:
                    for med in dispatch_eval['deliverable']:
                        s = stock_for_med(current_user.clinic_id, med['nombre_med'], pharmacy_id)
                        if s:
                            commit_qty = med.get('deliver_qty', med['cantidad'])
                            s.cantidad_comprometida = (s.cantidad_comprometida or 0) + commit_qty
                    ticket.status = 'autorizado'
                    db.session.add(Notification(
                        user_id=ticket.patient_id,
                        clinic_id=current_user.clinic_id,
                        title='💊 Medicamentos disponibles',
                        message=f'Tu ticket {ticket.pickup_code} ya tiene stock disponible en la sede. Puedes acercarte a recogerlos.',
                        type='ticket_ready',
                    ))
                    audit('ticket_reactivated_by_staff', details=f'ticket_id={ticket.id}')
                    db.session.commit()
                    flash(f'Paciente notificado y ticket {ticket.pickup_code} reactivado con éxito.')
                else:
                    flash(f'Aún no hay stock físico suficiente para reactivar el ticket {ticket.pickup_code}.')
        return redirect(url_for('staff.pendientes'))

    now = colombia_now()
    # All non-delivered, non-cancelled tickets
    pending_tickets = MedicationPickupTicket.query.filter(
        MedicationPickupTicket.clinic_id == current_user.clinic_id,
        MedicationPickupTicket.status.in_(['sin_stock', 'autorizado', 'parcial']),
    ).order_by(
        MedicationPickupTicket.priority.asc(),
        MedicationPickupTicket.created_at.asc(),
    ).all()

    # Enrich with delay info and missing meds summary
    rows = []
    for ticket in pending_tickets:
        import json as _json
        meds = []
        try:
            meds = _json.loads(ticket.meds_json or '[]')
        except Exception:
            pass
        days_pending = (now - ticket.created_at).days
        urgency = 'critico' if days_pending >= 7 else 'alto' if days_pending >= 3 else 'normal'
        rows.append({
            'ticket': ticket,
            'meds': meds,
            'days_pending': days_pending,
            'urgency': urgency,
        })

    # Open replenishment alerts for context
    alerts = ReplenishmentAlert.query.filter_by(
        clinic_id=current_user.clinic_id,
        status='abierta',
    ).order_by(ReplenishmentAlert.created_at.desc()).limit(30).all()

    # Upcoming confirmed shipments
    incoming = IncomingShipment.query.filter_by(
        clinic_id=current_user.clinic_id,
        status='en_camino',
    ).order_by(IncomingShipment.expected_date.asc()).all()

    return render_template(
        'staff_pendientes.html',
        rows=rows,
        alerts=alerts,
        incoming=incoming,
        today_str=now.strftime('%Y-%m-%d'),
    )


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO EN CAMINO — Incoming Shipments
# ─────────────────────────────────────────────────────────────────────────────

@staff_bp.route('/envios', methods=['GET', 'POST'])
@login_required
@role_required(ROLE_STAFF, ROLE_RECEPTIONIST)
def envios():
    """Manage incoming medication shipments ('En Camino' module)."""
    pharmacies = Pharmacy.query.filter_by(clinic_id=current_user.clinic_id, is_active=True).all()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create_shipment':
            pharmacy_id = request.form.get('pharmacy_id', type=int)
            supplier_name = (request.form.get('supplier_name') or '').strip()
            expected_date = (request.form.get('expected_date') or '').strip()
            note = (request.form.get('note') or '').strip()

            if not pharmacy_id or not expected_date:
                flash('Selecciona una farmacia y fecha estimada de llegada.')
                return redirect(url_for('staff.envios'))

            # Parse items (med_name[], quantity[])
            med_names = request.form.getlist('med_name[]')
            quantities = request.form.getlist('quantity[]')

            if not any(m.strip() for m in med_names):
                flash('Agrega al menos un medicamento al cargamento.')
                return redirect(url_for('staff.envios'))

            shipment = IncomingShipment(
                clinic_id=current_user.clinic_id,
                pharmacy_id=pharmacy_id,
                supplier_name=supplier_name or None,
                expected_date=expected_date,
                note=note or None,
                status='en_camino',
                created_by_user_id=current_user.id,
            )
            db.session.add(shipment)
            db.session.flush()

            for med_name, qty_str in zip(med_names, quantities):
                med_name = med_name.strip()
                if not med_name:
                    continue
                try:
                    qty = max(1, int(qty_str))
                except (TypeError, ValueError):
                    qty = 1
                item = IncomingShipmentItem(
                    clinic_id=current_user.clinic_id,
                    shipment_id=shipment.id,
                    med_name=med_name,
                    quantity_incoming=qty,
                )
                db.session.add(item)

            audit('incoming_shipment_created', details=f'shipment_id={shipment.id}; pharmacy_id={pharmacy_id}')
            db.session.commit()
            flash(f'✅ Cargamento registrado para {expected_date}. El staff puede informar al paciente la fecha estimada.')
            return redirect(url_for('staff.envios'))

        elif action == 'receive_shipment':
            shipment_id = request.form.get('shipment_id', type=int)
            shipment = IncomingShipment.query.filter_by(
                id=shipment_id,
                clinic_id=current_user.clinic_id,
            ).first()
            if not shipment or shipment.status != 'en_camino':
                flash('Cargamento no encontrado o ya procesado.')
                return redirect(url_for('staff.envios'))

            # Add stock to pharmacy inventory and trigger auto-reservation
            reactivated_total = []
            for item in shipment.items:
                # Find or create stock entry
                stock = Stock.query.filter_by(
                    clinic_id=current_user.clinic_id,
                    pharmacy_id=shipment.pharmacy_id,
                    nombre_med=item.med_name,
                ).first()
                if stock:
                    stock.cantidad += item.quantity_incoming
                else:
                    stock = Stock(
                        clinic_id=current_user.clinic_id,
                        pharmacy_id=shipment.pharmacy_id,
                        nombre_med=item.med_name,
                        cantidad=item.quantity_incoming,
                        unidad='unidad',
                    )
                    db.session.add(stock)
                db.session.flush()

                # Auto-reserve stock for pending tickets
                reactivated = reserve_stock_for_pending_tickets(
                    current_user.clinic_id,
                    shipment.pharmacy_id,
                    item.med_name,
                    item.quantity_incoming,
                )
                reactivated_total.extend(reactivated)

                # Close related replenishment alerts
                ReplenishmentAlert.query.filter_by(
                    clinic_id=current_user.clinic_id,
                    pharmacy_id=shipment.pharmacy_id,
                    med_name=item.med_name,
                    status='abierta',
                ).update({'status': 'resuelta', 'resolved_at': colombia_now()})

            shipment.status = 'recibido'
            shipment.received_at = colombia_now()
            audit('incoming_shipment_received', details=f'shipment_id={shipment.id}; reactivated_tickets={reactivated_total}')
            db.session.commit()
            flash(
                f'✅ Cargamento recibido. Stock actualizado. '
                f'{len(reactivated_total)} ticket(s) reactivados automáticamente. Pacientes notificados.'
            )
            return redirect(url_for('staff.envios'))

        elif action == 'cancel_shipment':
            shipment_id = request.form.get('shipment_id', type=int)
            shipment = IncomingShipment.query.filter_by(
                id=shipment_id,
                clinic_id=current_user.clinic_id,
                status='en_camino',
            ).first()
            if shipment:
                shipment.status = 'cancelado'
                audit('incoming_shipment_cancelled', details=f'shipment_id={shipment.id}')
                db.session.commit()
                flash('Cargamento cancelado.')
            return redirect(url_for('staff.envios'))

    # GET — list all shipments
    shipments = IncomingShipment.query.filter_by(
        clinic_id=current_user.clinic_id,
    ).order_by(IncomingShipment.expected_date.desc()).limit(50).all()

    return render_template(
        'staff_envios.html',
        shipments=shipments,
        pharmacies=pharmacies,
    )
