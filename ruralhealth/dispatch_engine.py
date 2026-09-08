"""
dispatch_engine.py, Motor central de despacho de medicamentos.

Funciones:
  evaluate_dispatch_status   → Determina si se puede despachar total/parcial/nada.
  create_partial_delivery    → Crea sub-ticket para medicamentos pendientes.
  reserve_stock_for_pending  → Al llegar nuevo stock, reserva para tickets sin_stock.
  check_order_completion     → Verifica si todos los sub-tickets cierran la orden.
  set_ticket_priority        → Cambia prioridad de un ticket.
"""

import json
import secrets

from models import (
    IncomingShipmentItem,
    MedicalOrder,
    MedicationPickupTicket,
    Notification,
    Stock,
    db,
)
from ledger import (
    InsufficientStock,
    dispense_units,
    dispensed_totals,
    disponible_para_ticket,
    liberar_reservas,
    record_dispensing,
    reservar_para_ticket,
    reserve_units,
)
from pharmacy_utils import (alerta_por_punto_de_reposicion, available_quantity,
                           resolver_alerta_de_punto_de_reposicion, stock_for_med)
from security import audit, generate_pickup_hash, pii_hash
from time_utils import colombia_now


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _parse_meds(meds_json: str) -> list:
    try:
        raw = json.loads(meds_json or '[]')
    except (json.JSONDecodeError, TypeError):
        return []
    result = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = (item.get('nombre_med') or item.get('medicamento') or item.get('name') or '').strip()
        if not name:
            continue
        try:
            qty = int(item.get('cantidad') or 1)
        except (TypeError, ValueError):
            qty = 1
        result.append({
            'nombre_med': name[:180],
            'cantidad': max(1, qty),
            'unidad': (item.get('unidad') or 'unidad')[:40],
            'instrucciones': (item.get('instrucciones') or '')[:500],
        })
    return result


def _meds_json(meds: list) -> str:
    return json.dumps(meds, ensure_ascii=False, sort_keys=True)


def _new_code() -> str:
    return secrets.token_urlsafe(8).replace('-', '').replace('_', '')[:12].upper()


# ─────────────────────────────────────────────
# 1. Evaluate Dispatch Status
# ─────────────────────────────────────────────

def evaluate_dispatch_status(ticket, pharmacy_id: int, ignore_commitments: bool = False) -> dict:
    """
    Evaluates what can be delivered NOW for a ticket, considering already delivered items.
    
    Returns:
        {
            'status': 'full' | 'partial' | 'none',
            'deliverable': [...], # Items to deliver in this session
            'pending': [...],     # Items still missing after this session
            'history': [...],     # Items already delivered in previous sessions
            'is_ready': bool,      # Can we deliver anything new?
        }
    """
    clinic_id = ticket.clinic_id
    original_meds = _parse_meds(ticket.meds_json)

    # Lo ya entregado se lee del libro mayor, no de `delivered_json`. Ese campo
    # se sobrescribia en cada entrega parcial, de modo que una lectura tardia
    # podia mostrar como pendiente algo ya entregado -y volver a entregarlo.
    delivered_map = dispensed_totals(ticket.id)
    if not delivered_map and ticket.delivered_json:
        # Tickets anteriores a la introduccion del libro mayor.
        for item in _parse_meds(ticket.delivered_json):
            key = item['nombre_med'].lower()
            delivered_map[key] = delivered_map.get(key, 0) + item['cantidad']


    deliverable_now = []
    remaining_after = []
    history = []
    
    for med in original_meds:
        name = med['nombre_med']
        name_l = name.lower()
        required = med['cantidad']
        given_so_far = delivered_map.get(name_l, 0)
        
        still_needed = required - given_so_far
        
        if still_needed <= 0:
            history.append({**med, 'cantidad': given_so_far, 'status': 'completado'})
            continue
            
        # If we reached here, some qty is still pending
        if given_so_far > 0:
            history.append({**med, 'cantidad': given_so_far, 'status': 'parcial'})
            
        stock = stock_for_med(clinic_id, name, pharmacy_id)
        
        if ignore_commitments:
            avail = stock.cantidad if stock else 0
        else:
            # Lo que puede llevarse ESTE ticket: lo que hay menos lo apartado
            # por OTROS. Antes se usaba `cantidad - comprometida` a secas, y
            # como la reserva del propio ticket esta dentro de `comprometida`,
            # la funcion se la restaba a si misma: con diez unidades en
            # estante y un ticket por diez, disponible daba cero. El paciente
            # se iba sin nada con el frasco delante, el ticket caia a "sin
            # stock" y no habia forma de reactivarlo. En un puesto con
            # existencias justas (el caso normal) pasaba siempre.
            avail = disponible_para_ticket(stock, ticket.id)
        
        can_give = min(still_needed, avail)
        
        if can_give > 0:
            deliverable_now.append({
                **med, 
                'cantidad_solicitada': required,
                'cantidad_pendiente': still_needed,
                'available': avail, 
                'deliver_qty': can_give
            })
            if can_give < still_needed:
                remaining_after.append({**med, 'cantidad': still_needed - can_give})
        else:
            remaining_after.append({**med, 'cantidad': still_needed, 'available': avail})

    # Overall status for this SESSION
    if not remaining_after and deliverable_now:
        status = 'full' # Can finish the ticket now
    elif deliverable_now:
        status = 'partial' # Can give some more
    else:
        status = 'none' # Nothing can be given now
        
    return {
        'status': status,
        'deliverable': deliverable_now,
        'pending': remaining_after,
        'history': history,
        'is_ready': len(deliverable_now) > 0,
        'total_remaining': len(remaining_after) + len(deliverable_now)
    }


# ─────────────────────────────────────────────
# 2. Confirm Delivery (full or partial)
# ─────────────────────────────────────────────

def _bloquear_ticket(ticket_id, clinic_id):
    """Recupera el ticket con bloqueo de fila, para revalidarlo sin carreras.

    `lock_stock_row` protegia las existencias, pero nadie protegia el ticket:
    su estado se leia fuera de la transaccion y dos peticiones simultaneas
    pasaban las dos.
    """
    from ledger import _supports_row_locking

    consulta = MedicationPickupTicket.query.filter_by(id=ticket_id,
                                                      clinic_id=clinic_id)
    if _supports_row_locking():
        consulta = consulta.with_for_update(nowait=False)
    return consulta.first()


def confirm_delivery(
    ticket,
    pharmacy,
    expendedor_user,
    dispatch_eval: dict,
    receiver_kind: str = 'paciente',
    receiver_name: str = None,
    receiver_document: str = None,
    identity_verified: bool = False,
) -> dict:
    """Procesa una sesion de entrega de forma atomica.

    Cambios respecto de la version anterior, todos por la misma razon: entre la
    evaluacion que vio el expendedor en pantalla y este instante puede haber
    pasado otra entrega.

    1. Las existencias se descuentan bajo bloqueo de fila y con verificacion
       posterior al bloqueo. Antes se leia y se escribia sin proteccion, de modo
       que dos expendedores concurrentes podian entregar el mismo inventario dos
       veces y dejar sin medicamento a un paciente con ticket valido.
    2. Cada medicamento entregado deja un asiento en el libro mayor. Antes solo
       se sobrescribia `delivered_json`, que destruia el historial de las entregas
       parciales anteriores.
    3. Si algun medicamento falla por existencias, la transaccion completa se
       revierte: no queda una entrega a medias registrada como completa.
    """
    clinic_id = ticket.clinic_id

    # --- Puertas de entrada, antes de tocar nada ---
    #
    # a) La fila del ticket se bloquea y se revalida su estado DENTRO de la
    #    transaccion. Antes la comprobacion vivia en la ruta, fuera de toda
    #    transaccion: dos peticiones simultaneas, un doble clic, un reenvio
    #    del formulario, dos expendedores con el mismo codigo, leian
    #    "autorizado" a la vez y ambas dispensaban. Reproducido: 20 unidades
    #    entregadas contra una prescripcion de 10, y el libro de dispensacion
    #    registrando una entrega que excede la orden medica.
    ticket = _bloquear_ticket(ticket.id, clinic_id)
    if ticket is None or ticket.status not in ('autorizado', 'parcial'):
        return {'status': (ticket.status if ticket else 'desconocido'),
                'delivered': [], 'motivo': 'El ticket ya no admite entrega.'}

    # b) La orden que respalda el ticket tiene que seguir vigente. Se validaba
    #    al EMITIR el ticket y nunca mas. Entre la emision y el mostrador, el
    #    profesional puede haber anulado la orden, por una dosis equivocada,
    #    una alergia o una interaccion, y el ticket seguia entregando lo que
    #    el medico acababa de retirar.
    if ticket.order_id and ticket.order is not None and not ticket.order.is_dispensable:
        audit('dispense_blocked_order_not_dispensable',
              details=f'ticket_id={ticket.id}; order_id={ticket.order_id}')
        db.session.commit()
        return {'status': ticket.status, 'delivered': [],
                'motivo': 'La orden que respalda este ticket fue anulada o vencio.'}

    deliverable_now = dispatch_eval['deliverable']
    remaining_after = list(dispatch_eval['pending'])

    if not deliverable_now:
        ticket.status = 'sin_stock'
        db.session.commit()
        return {'status': 'sin_stock', 'delivered': []}

    receiver_document_hash = pii_hash(receiver_document) if receiver_document else None
    delivered_records = []
    shortfalls = []

    try:
        for med in deliverable_now:
            name = med['nombre_med']
            requested = med['deliver_qty']

            try:
                dispense_units(
                    clinic_id=clinic_id,
                    med_name=name,
                    quantity=requested,
                    pharmacy_id=pharmacy.id,
                    performed_by_id=expendedor_user.id,
                    ticket_id=ticket.id,
                    order_id=ticket.order_id,
                    reason=f'Entrega del ticket {ticket.pickup_code}',
                )
            except InsufficientStock as error:
                # Otra entrega consumio las existencias entre la evaluacion y
                # este momento. Se anota como pendiente en lugar de descontar de
                # un saldo que ya no existe.
                shortfalls.append({
                    'nombre_med': name,
                    'solicitado': requested,
                    'disponible': error.available,
                })
                remaining_after.append({**med, 'cantidad': requested})
                continue

            record_dispensing(
                clinic_id=clinic_id,
                ticket=ticket,
                med_name=name,
                quantity=requested,
                unit=med.get('unidad') or 'unidad',
                dispensed_by_id=expendedor_user.id,
                pharmacy_id=pharmacy.id,
                receiver_kind=receiver_kind,
                receiver_name=receiver_name,
                receiver_document_hash=receiver_document_hash,
                identity_verified=identity_verified,
            )
            delivered_records.append({
                'nombre_med': name,
                'cantidad': requested,
                'unidad': med.get('unidad') or 'unidad',
            })

        if not delivered_records:
            # Nada pudo entregarse: se revierte y se marca sin existencias.
            db.session.rollback()
            ticket.status = 'sin_stock'
            db.session.commit()
            audit(
                'delivery_failed_insufficient_stock',
                details=f'ticket_id={ticket.id}; faltantes={len(shortfalls)}',
            )
            db.session.commit()
            return {'status': 'sin_stock', 'delivered': [], 'shortfalls': shortfalls}

        # `delivered_json` se conserva como resumen para las pantallas, pero la
        # fuente de verdad pasa a ser el libro mayor.
        totals = dispensed_totals(ticket.id)
        summary = []
        for med in _parse_meds(ticket.meds_json):
            given = totals.get(med['nombre_med'].lower(), 0)
            if given:
                summary.append({
                    'nombre_med': med['nombre_med'],
                    'cantidad': given,
                    'unidad': med.get('unidad') or 'unidad',
                })
        ticket.delivered_json = _meds_json(summary)
        ticket.expendedor_id = expendedor_user.id
        ticket.delivered_at = colombia_now()

        if not remaining_after:
            ticket.status = 'entregado'
            _notify(
                ticket.patient_id, clinic_id,
                'Entrega completada',
                f'Su ticket {ticket.pickup_code} fue entregado en su totalidad.',
                'delivery_confirmed',
            )
        else:
            ticket.status = 'parcial'
            _notify(
                ticket.patient_id, clinic_id,
                'Entrega parcial realizada',
                (
                    f'Se entregaron algunos medicamentos del ticket {ticket.pickup_code}. '
                    'Aun quedan pendientes por recoger.'
                ),
                'partial_delivery',
            )

        if ticket.staff_id and ticket.staff_id != expendedor_user.id:
            _notify(
                ticket.staff_id, clinic_id,
                f'Entrega {ticket.status}',
                f'El ticket {ticket.pickup_code} paso a estado {ticket.status}.',
                'delivery_update',
            )

        check_order_completion(ticket.order_id, clinic_id)

        # Entregar baja el stock, y bajar el stock puede cruzar el punto de
        # reposicion de la sede. Se mira aqui, dentro de la misma transaccion:
        # si la entrega se revierte, la alerta se revierte con ella.
        for registro in delivered_records:
            alerta_por_punto_de_reposicion(
                stock_for_med(clinic_id, registro['nombre_med'], pharmacy.id))

        audit(
            'pickup_ticket_delivered',
            details=(
                f'ticket_id={ticket.id}; estado={ticket.status}; '
                f'medicamentos={len(delivered_records)}; '
                f'identidad_verificada={"si" if identity_verified else "no"}'
            ),
        )
        db.session.commit()

    except Exception:
        # Una entrega a medias es peor que ninguna: el paciente se lleva parte
        # del medicamento pero el sistema cree que se entrego todo.
        db.session.rollback()
        raise

    return {
        'status': ticket.status,
        'delivered': delivered_records,
        'shortfalls': shortfalls,
    }


# ─────────────────────────────────────────────
# 3. Reserve Stock for Pending Tickets (Auto-trigger on stock arrival)
# ─────────────────────────────────────────────

def reserve_stock_for_pending_tickets(clinic_id: int, pharmacy_id: int, med_name: str, quantity_added: int) -> list:
    """
    Called when new stock arrives (either via IncomingShipment or manual inventory update).
    Scans sin_stock tickets in priority order and auto-reserves stock.

    Returns list of reactivated ticket IDs.
    """
    # Get all sin_stock tickets for this clinic that need this medication
    # Priority: 'alta' first, then by created_at ASC (oldest first)
    sin_stock_tickets = (
        MedicationPickupTicket.query
        .filter(
            MedicationPickupTicket.clinic_id == clinic_id,
            MedicationPickupTicket.status.in_(['sin_stock', 'parcial']),
            # Solo los de ESTA sede.
            #
            # Sin este filtro, la llegada de mercancia a la sede B reasignaba
            # (mas abajo, `ticket.pharmacy_id = pharmacy_id`) tickets de la
            # sede A, dejando `pickup_location` diciendo "Sede A", que es lo
            # impreso en el papel que el paciente lleva en la mano, mientras
            # la notificacion le decia que fuera a B. El paciente se presenta
            # donde dice su ticket, el expendedor de A lo rechaza por
            # pertenecer a otra farmacia, y en una vereda "la otra sede" puede
            # ser media jornada de camino.
            (MedicationPickupTicket.pharmacy_id == pharmacy_id)
            | (MedicationPickupTicket.pharmacy_id.is_(None)),
        )
        .order_by(
            # alta priority first (alphabetically 'alta' < 'normal')
            MedicationPickupTicket.priority.asc(),
            MedicationPickupTicket.created_at.asc(),
        )
        .all()
    )

    reactivated = []
    stock = stock_for_med(clinic_id, med_name, pharmacy_id)

    # Llego mercancia: si con ella el medicamento vuelve por encima del punto
    # de reposicion, la alerta que lo avisaba ya no describe nada. Cerrarla
    # aqui es lo que evita que la bandeja del administrador se llene de avisos
    # de cosas ya resueltas, el dia que eso pasa, deja de leerlos todos.
    resolver_alerta_de_punto_de_reposicion(stock)

    for ticket in sin_stock_tickets:
        meds = _parse_meds(ticket.meds_json)
        needs_this_med = [m for m in meds if m['nombre_med'].lower() == med_name.lower()]
        if not needs_this_med:
            continue

        # Check if ANY new items in the ticket now have enough stock
        dispatch = evaluate_dispatch_status(ticket, pharmacy_id)
        if dispatch['is_ready']:
            # Commit stock as compromised for this ticket
            for med in dispatch['deliverable']:
                s = stock_for_med(clinic_id, med['nombre_med'], pharmacy_id)
                if s:
                    commit_qty = med.get('deliver_qty', med['cantidad'])
                    # Con dueno y de forma idempotente: antes se sumaba a mano
                    # a `cantidad_comprometida`, asi que cada reintento
                    # apartaba otro tanto y nadie lo soltaba nunca.
                    reservar_para_ticket(clinic_id, s, ticket.id, commit_qty,
                                         reason='Reserva al llegar mercancia')

            # Reactivate ticket
            old_status = ticket.status
            ticket.status = 'autorizado'
            ticket.pharmacy_id = pharmacy_id

            # Notify patient
            from models import Pharmacy
            pharmacy = db.session.get(Pharmacy, pharmacy_id)
            pharmacy_name = pharmacy.name if pharmacy else 'la farmacia'
            pharmacy_address = pharmacy.address or '' if pharmacy else ''

            _notify(
                user_id=ticket.patient_id,
                clinic_id=clinic_id,
                title='Medicamentos disponibles para recoger',
                message=(
                    f'Su ticket {ticket.pickup_code} está listo. '
                    f'Los medicamentos ya están disponibles en {pharmacy_name}. '
                    f'{f"Dirección: {pharmacy_address}. " if pharmacy_address else ""}'
                    f'Fecha sugerida de recogida: {ticket.pickup_date} a las {ticket.pickup_time}. '
                    f'Código: {ticket.pickup_code}.'
                ),
                notif_type='ticket_ready',
            )
            audit(
                'ticket_reactivated_on_stock_arrival',
                details=f'ticket_id={ticket.id}; med={med_name}; pharmacy_id={pharmacy_id}',
            )
            reactivated.append(ticket.id)

    return reactivated


# ─────────────────────────────────────────────
# 4. Order Completion Check
# ─────────────────────────────────────────────

def check_order_completion(order_id: int | None, clinic_id: int) -> bool:
    """
    Marks a MedicalOrder as 'completada' when all its tickets (and sub-tickets)
    are in 'entregado' status. Returns True if order was completed.
    """
    if not order_id:
        return False
    order = MedicalOrder.query.filter_by(id=order_id, clinic_id=clinic_id).first()
    if not order:
        return False

    # All tickets for this order (including sub-tickets via parent)
    all_tickets = MedicationPickupTicket.query.filter_by(
        clinic_id=clinic_id,
        order_id=order_id,
    ).all()

    if not all_tickets:
        return False

    all_done = all(t.status == 'entregado' for t in all_tickets)
    if all_done and order.status != 'completada':
        order.status = 'completada'
        audit('medical_order_completed', details=f'order_id={order_id}')
        return True
    return False


# ─────────────────────────────────────────────
# 5. Priority Management
# ─────────────────────────────────────────────

def set_ticket_priority(ticket_id: int, clinic_id: int, priority: str) -> bool:
    """Sets priority on a ticket and all its sub-tickets. Returns True on success."""
    if priority not in ('normal', 'alta'):
        return False
    ticket = MedicationPickupTicket.query.filter_by(id=ticket_id, clinic_id=clinic_id).first()
    if not ticket:
        return False
    ticket.priority = priority
    for sub in ticket.sub_tickets or []:
        sub.priority = priority
    audit('ticket_priority_set', details=f'ticket_id={ticket_id}; priority={priority}')
    return True


def get_ready_to_complete_tickets(clinic_id: int, pharmacy_id: int) -> list:
    """
    Finds tickets in 'parcial' or 'sin_stock' status that have at least one 
    pending item currently available in stock at this pharmacy.
    """
    pending_tickets = MedicationPickupTicket.query.filter(
        MedicationPickupTicket.clinic_id == clinic_id,
        MedicationPickupTicket.status.in_(['parcial', 'sin_stock'])
    ).all()
    
    ready = []
    for ticket in pending_tickets:
        eval = evaluate_dispatch_status(ticket, pharmacy_id)
        if eval['is_ready']:
            ready.append({
                'ticket': ticket,
                'eval': eval
            })
    return ready


# ─────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────

def _notify(user_id: int, clinic_id: int, title: str, message: str, notif_type: str = 'info'):
    notif = Notification(
        user_id=user_id,
        clinic_id=clinic_id,
        title=title,
        message=message,
        type=notif_type,
    )
    db.session.add(notif)
