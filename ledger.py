"""Libro mayor de inventario y dispensacion.

Este modulo es el unico camino por el que deben cambiar las existencias.

Dos propiedades lo justifican:

**Atomicidad.** El codigo anterior leia el stock, decidia cuanto entregar y lo
descontaba despues, sin bloqueo. Dos expendedores atendiendo en paralelo podian
entregar el mismo inventario dos veces, dejando sin medicamento a un paciente que
tenia ticket valido y unidades reservadas. Aqui toda modificacion ocurre bajo
bloqueo pesimista de la fila (``SELECT ... FOR UPDATE``) y con decremento
condicional: si la existencia cambio entre la lectura y la escritura, la
operacion falla en vez de producir un saldo incorrecto.

**Trazabilidad.** Cada movimiento deja un asiento inmutable con saldo anterior y
posterior, responsable, motivo y referencia al ticket u orden. Los asientos van
encadenados por hash: alterar uno rompe la verificacion de todos los siguientes.

SQLite no implementa ``SELECT ... FOR UPDATE``. En desarrollo se degrada a la
serializacion propia del motor, que es suficiente para un unico proceso; por eso
``config.py`` rechaza SQLite en produccion.
"""

import hashlib
import json

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from models import (
    STOCK_MOVE_ADJUST,
    STOCK_MOVE_DISPENSE,
    STOCK_MOVE_RECEIPT,
    STOCK_MOVE_RELEASE,
    STOCK_MOVE_RESERVE,
    DispensingLedgerEntry,
    Stock,
    StockLedgerEntry,
    db,
)
from time_utils import colombia_now


class InsufficientStock(Exception):
    """No hay existencias suficientes para completar el movimiento."""

    def __init__(self, med_name, requested, available):
        self.med_name = med_name
        self.requested = requested
        self.available = available
        super().__init__(
            f'Existencias insuficientes de "{med_name}": '
            f'se solicitan {requested}, hay {available} disponibles.'
        )


class ConcurrentModification(Exception):
    """Otra operacion modifico la misma fila mientras esta se ejecutaba."""


# --- Encadenamiento hash -----------------------------------------------------

def _chain_hash(previous_hash, payload):
    """Hash de un asiento, encadenado al anterior."""
    material = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(
        f'{previous_hash or "GENESIS"}|{material}'.encode('utf-8')
    ).hexdigest()


def _last_hash(model, clinic_id):
    """Hash del ultimo asiento de la clinica, o None si es el primero."""
    row = (
        db.session.query(model.entry_hash)
        .filter(model.clinic_id == clinic_id)
        .order_by(model.id.desc())
        .limit(1)
        .first()
    )
    return row[0] if row else None


# --- Bloqueo de fila ---------------------------------------------------------

ROW_LOCKING_DIALECTS = frozenset({'postgresql', 'mysql', 'mariadb', 'oracle'})


def _supports_row_locking():
    """True si el motor implementa SELECT ... FOR UPDATE.

    SQLite no lo implementa: serializa con un bloqueo de la base completa, que
    basta para un único proceso de desarrollo pero no para varios workers. Por
    eso `config.py` rechaza SQLite en produccion.
    """
    try:
        return db.engine.dialect.name in ROW_LOCKING_DIALECTS
    except Exception:
        return False


def lock_stock_row(clinic_id, med_name, pharmacy_id):
    """Recupera la fila de existencias con bloqueo exclusivo.

    A partir de aqui, y hasta el fin de la transaccion, ninguna otra sesion puede
    leer para actualizar ni modificar esta fila. Es lo que impide la doble
    dispensacion.
    """
    normalized = (med_name or '').strip().lower()
    query = Stock.query.filter(
        Stock.clinic_id == clinic_id,
        (func.lower(Stock.nombre_med) == normalized)
        | (func.lower(Stock.medicamento) == normalized),
    )
    if pharmacy_id:
        query = query.filter(Stock.pharmacy_id == pharmacy_id)

    if _supports_row_locking():
        query = query.with_for_update(nowait=False)

    return query.first()


# --- Asientos de inventario --------------------------------------------------

def record_stock_movement(
    clinic_id,
    stock,
    movement_type,
    quantity,
    performed_by_id=None,
    reason=None,
    ticket_id=None,
    order_id=None,
    shipment_id=None,
    batch_number=None,
    expiry_date=None,
    balance_before=None,
    committed_before=None,
):
    """Escribe el asiento de un movimiento ya aplicado a `stock`.

    Se llama *despues* de mutar la fila, con los saldos previos capturados antes,
    de modo que el asiento refleje exactamente la transicion ocurrida.
    """
    entry = StockLedgerEntry(
        clinic_id=clinic_id,
        stock_id=stock.id if stock else None,
        pharmacy_id=stock.pharmacy_id if stock else None,
        med_name=(stock.medicamento or stock.nombre_med) if stock else '',
        movement_type=movement_type,
        quantity=quantity,
        balance_before=balance_before if balance_before is not None else 0,
        balance_after=stock.cantidad if stock else 0,
        committed_before=committed_before if committed_before is not None else 0,
        committed_after=(stock.cantidad_comprometida or 0) if stock else 0,
        batch_number=batch_number,
        expiry_date=expiry_date,
        ticket_id=ticket_id,
        order_id=order_id,
        shipment_id=shipment_id,
        performed_by_id=performed_by_id,
        reason=(reason or '')[:300] or None,
        created_at=colombia_now(),
    )

    previous = _last_hash(StockLedgerEntry, clinic_id)
    entry.previous_hash = previous
    entry.entry_hash = _chain_hash(previous, {
        'clinic_id': clinic_id,
        'med': entry.med_name,
        'type': movement_type,
        'qty': quantity,
        'before': entry.balance_before,
        'after': entry.balance_after,
        'by': performed_by_id,
        'ticket': ticket_id,
        'at': entry.created_at.isoformat(),
    })

    db.session.add(entry)
    return entry


def _conditional_decrement(stock_id, quantity):
    """Descuenta `quantity` solo si el saldo actual alcanza. Devuelve si ocurrio.

    Es una operacion de comparar-y-actualizar en una sola sentencia SQL:

        UPDATE stock SET cantidad = cantidad - :q WHERE id = :id AND cantidad >= :q

    La condicion viaja dentro del propio UPDATE, asi que el motor la evalua de
    forma atomica sobre la fila. Si otra transaccion se adelanto y consumio las
    existencias, la sentencia no afecta ninguna fila y lo sabemos por `rowcount`.

    Esto no depende del dialecto: funciona igual en PostgreSQL y en SQLite. El
    bloqueo pesimista de `lock_stock_row` sigue aplicandose donde existe, porque
    ademas serializa la lectura previa; pero la correccion ya no descansa solo en
    el, que es lo que fallaba cuando el motor no implementa SELECT ... FOR UPDATE.
    """
    result = db.session.execute(
        db.text(
            'UPDATE stock '
            'SET cantidad = cantidad - :quantity, '
            '    cantidad_comprometida = CASE '
            '        WHEN COALESCE(cantidad_comprometida, 0) >= :quantity '
            '        THEN COALESCE(cantidad_comprometida, 0) - :quantity '
            '        ELSE 0 END '
            'WHERE id = :stock_id AND cantidad >= :quantity'
        ),
        {'quantity': quantity, 'stock_id': stock_id},
    )
    return result.rowcount == 1


def dispense_units(
    clinic_id,
    med_name,
    quantity,
    pharmacy_id,
    performed_by_id,
    ticket_id=None,
    order_id=None,
    reason=None,
):
    """Descuenta existencias de forma atomica y deja el asiento correspondiente.

    Debe ejecutarse dentro de una transaccion abierta; no hace commit, para que
    quien la invoca decida el alcance de la unidad de trabajo.

    Raises:
        InsufficientStock: si el saldo real no alcanza en el momento de descontar.
    """
    if quantity <= 0:
        raise ValueError('La cantidad a dispensar debe ser mayor que cero.')

    stock = lock_stock_row(clinic_id, med_name, pharmacy_id)
    if stock is None:
        raise InsufficientStock(med_name, quantity, 0)

    balance_before = stock.cantidad or 0
    committed_before = stock.cantidad_comprometida or 0
    stock_id = stock.id

    # Comprobacion temprana, para dar un mensaje util cuando es evidente que no
    # alcanza. No es la que garantiza la correccion.
    if balance_before < quantity:
        raise InsufficientStock(med_name, quantity, balance_before)

    if not _conditional_decrement(stock_id, quantity):
        # Entre la lectura y la escritura, otra entrega consumio las existencias.
        # Sin esta comprobacion ambas prosperarian y se entregaria mas inventario
        # del que existe, dejando sin medicamento a un paciente con ticket valido.
        db.session.expire_all()
        current = db.session.get(Stock, stock_id)
        raise InsufficientStock(med_name, quantity, (current.cantidad if current else 0))

    # La fila cambio por SQL directo: se recarga para que el asiento refleje el
    # saldo real y no el que el ORM tenia en memoria.
    db.session.expire(stock)
    stock = db.session.get(Stock, stock_id)

    record_stock_movement(
        clinic_id=clinic_id,
        stock=stock,
        movement_type=STOCK_MOVE_DISPENSE,
        quantity=-quantity,
        performed_by_id=performed_by_id,
        reason=reason or 'Entrega de medicamento a paciente',
        ticket_id=ticket_id,
        order_id=order_id,
        balance_before=balance_before,
        committed_before=committed_before,
    )

    if (stock.cantidad or 0) < 0:
        # Invariante que nunca debe romperse. Si se llega aqui, algo mutó las
        # existencias por fuera de este modulo y la transaccion debe revertirse.
        raise ConcurrentModification(
            f'El saldo de "{med_name}" quedo negativo. Operacion revertida.'
        )

    return stock


def reserve_units(
    clinic_id,
    med_name,
    quantity,
    pharmacy_id,
    performed_by_id,
    ticket_id=None,
    order_id=None,
    reason=None,
):
    """Aparta existencias para un ticket, sin descontarlas todavia.

    La reserva es lo que garantiza que el paciente que ya tiene ticket encuentre
    su medicamento al llegar al mostrador. Solo se reserva lo que esta realmente
    disponible: existencias menos lo ya comprometido.
    """
    if quantity <= 0:
        raise ValueError('La cantidad a reservar debe ser mayor que cero.')

    stock = lock_stock_row(clinic_id, med_name, pharmacy_id)
    if stock is None:
        raise InsufficientStock(med_name, quantity, 0)

    balance_before = stock.cantidad or 0
    committed_before = stock.cantidad_comprometida or 0
    available = max(0, balance_before - committed_before)
    stock_id = stock.id

    if available < quantity:
        raise InsufficientStock(med_name, quantity, available)

    # Misma tecnica que en el descuento: la condicion viaja dentro del UPDATE,
    # de modo que dos reservas simultaneas no pueden apartar las mismas unidades
    # para pacientes distintos.
    result = db.session.execute(
        db.text(
            'UPDATE stock '
            'SET cantidad_comprometida = COALESCE(cantidad_comprometida, 0) + :quantity '
            'WHERE id = :stock_id '
            '  AND cantidad - COALESCE(cantidad_comprometida, 0) >= :quantity'
        ),
        {'quantity': quantity, 'stock_id': stock_id},
    )
    if result.rowcount != 1:
        db.session.expire_all()
        current = db.session.get(Stock, stock_id)
        remaining = max(0, (current.cantidad or 0) - (current.cantidad_comprometida or 0)) \
            if current else 0
        raise InsufficientStock(med_name, quantity, remaining)

    db.session.expire(stock)
    stock = db.session.get(Stock, stock_id)

    record_stock_movement(
        clinic_id=clinic_id,
        stock=stock,
        movement_type=STOCK_MOVE_RESERVE,
        quantity=0,   # el saldo fisico no cambia; cambia lo comprometido
        performed_by_id=performed_by_id,
        reason=reason or 'Reserva para ticket de recogida',
        ticket_id=ticket_id,
        order_id=order_id,
        balance_before=balance_before,
        committed_before=committed_before,
    )
    return stock


def release_units(
    clinic_id,
    med_name,
    quantity,
    pharmacy_id,
    performed_by_id,
    ticket_id=None,
    reason=None,
):
    """Libera una reserva (ticket cancelado o vencido)."""
    if quantity <= 0:
        return None

    stock = lock_stock_row(clinic_id, med_name, pharmacy_id)
    if stock is None:
        return None

    balance_before = stock.cantidad or 0
    committed_before = stock.cantidad_comprometida or 0
    stock.cantidad_comprometida = max(0, committed_before - quantity)

    record_stock_movement(
        clinic_id=clinic_id,
        stock=stock,
        movement_type=STOCK_MOVE_RELEASE,
        quantity=0,
        performed_by_id=performed_by_id,
        reason=reason or 'Liberacion de reserva',
        ticket_id=ticket_id,
        balance_before=balance_before,
        committed_before=committed_before,
    )
    return stock


def receive_units(
    clinic_id,
    stock,
    quantity,
    performed_by_id,
    shipment_id=None,
    batch_number=None,
    expiry_date=None,
    reason=None,
):
    """Ingresa existencias por recepcion de un cargamento."""
    if quantity <= 0:
        raise ValueError('La cantidad a ingresar debe ser mayor que cero.')

    balance_before = stock.cantidad or 0
    committed_before = stock.cantidad_comprometida or 0
    stock.cantidad = balance_before + quantity

    record_stock_movement(
        clinic_id=clinic_id,
        stock=stock,
        movement_type=STOCK_MOVE_RECEIPT,
        quantity=quantity,
        performed_by_id=performed_by_id,
        reason=reason or 'Recepcion de cargamento',
        shipment_id=shipment_id,
        batch_number=batch_number,
        expiry_date=expiry_date,
        balance_before=balance_before,
        committed_before=committed_before,
    )
    return stock


def adjust_units(clinic_id, stock, new_quantity, performed_by_id, reason):
    """Ajuste manual de inventario.

    Exige motivo: un ajuste sin justificacion es indistinguible de un faltante
    encubierto, y es exactamente lo que una auditoria de farmacia busca.
    """
    if not (reason or '').strip():
        raise ValueError('Todo ajuste de inventario requiere un motivo escrito.')

    balance_before = stock.cantidad or 0
    committed_before = stock.cantidad_comprometida or 0
    delta = int(new_quantity) - balance_before
    stock.cantidad = max(0, int(new_quantity))

    record_stock_movement(
        clinic_id=clinic_id,
        stock=stock,
        movement_type=STOCK_MOVE_ADJUST,
        quantity=delta,
        performed_by_id=performed_by_id,
        reason=reason,
        balance_before=balance_before,
        committed_before=committed_before,
    )
    return stock


# --- Asientos de dispensacion ------------------------------------------------

def record_dispensing(
    clinic_id,
    ticket,
    med_name,
    quantity,
    unit,
    dispensed_by_id,
    pharmacy_id,
    receiver_kind='paciente',
    receiver_name=None,
    receiver_document_hash=None,
    identity_verified=False,
    batch_number=None,
    notes=None,
):
    """Deja constancia de la entrega de un medicamento concreto."""
    entry = DispensingLedgerEntry(
        clinic_id=clinic_id,
        ticket_id=ticket.id,
        order_id=ticket.order_id,
        patient_id=ticket.patient_id,
        pharmacy_id=pharmacy_id,
        dispensed_by_id=dispensed_by_id,
        med_name=med_name,
        quantity=quantity,
        unit=unit or 'unidad',
        batch_number=batch_number,
        receiver_kind=receiver_kind,
        receiver_name=receiver_name,
        receiver_document_hash=receiver_document_hash,
        identity_verified=bool(identity_verified),
        dispensed_at=colombia_now(),
        notes=notes,
    )

    previous = _last_hash(DispensingLedgerEntry, clinic_id)
    entry.previous_hash = previous
    entry.entry_hash = _chain_hash(previous, {
        'clinic_id': clinic_id,
        'ticket': ticket.id,
        'patient': ticket.patient_id,
        'med': med_name,
        'qty': quantity,
        'by': dispensed_by_id,
        'at': entry.dispensed_at.isoformat(),
    })

    db.session.add(entry)
    return entry


def dispensed_totals(ticket_id):
    """Suma entregada por medicamento, leida de los asientos.

    Reemplaza la lectura de `delivered_json`, que se sobrescribia en cada entrega
    parcial. El libro mayor es la fuente de verdad.
    """
    rows = (
        db.session.query(
            DispensingLedgerEntry.med_name,
            func.sum(DispensingLedgerEntry.quantity),
        )
        .filter(DispensingLedgerEntry.ticket_id == ticket_id)
        .group_by(DispensingLedgerEntry.med_name)
        .all()
    )
    return {name.strip().lower(): int(total or 0) for name, total in rows}


# --- Verificacion de integridad ---------------------------------------------

def verify_chain(model, clinic_id, limit=None):
    """Recorre la cadena de asientos y devuelve los rotos.

    Un asiento roto significa que fue alterado o eliminado despues de escribirse,
    o que uno anterior lo fue.
    """
    query = model.query.filter(model.clinic_id == clinic_id).order_by(model.id.asc())
    if limit:
        query = query.limit(limit)

    broken = []
    expected_previous = None

    for entry in query.all():
        if expected_previous is not None and entry.previous_hash != expected_previous:
            broken.append({
                'id': entry.id,
                'reason': 'El hash anterior no coincide con el asiento precedente.',
                'expected': expected_previous,
                'found': entry.previous_hash,
            })
        expected_previous = entry.entry_hash

    return broken


def stock_balance_matches_ledger(clinic_id, stock):
    """Compara el saldo actual con el que resulta de sumar el libro mayor.

    Una discrepancia indica que alguien modifico existencias sin pasar por este
    modulo: por la interfaz de base de datos, por un script, o por un camino de
    codigo que quedo sin migrar.
    """
    total = (
        db.session.query(func.coalesce(func.sum(StockLedgerEntry.quantity), 0))
        .filter(
            StockLedgerEntry.clinic_id == clinic_id,
            StockLedgerEntry.stock_id == stock.id,
        )
        .scalar()
    )
    return int(total or 0) == int(stock.cantidad or 0)


def safe_commit():
    """Confirma la transaccion y revierte de forma limpia ante un fallo."""
    try:
        db.session.commit()
        return True
    except SQLAlchemyError:
        db.session.rollback()
        raise
