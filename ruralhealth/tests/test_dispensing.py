"""Pruebas del libro mayor y de la dispensación de medicamentos.

El fallo que estas pruebas previenen tiene consecuencia física: si el sistema
descuenta mal las existencias, un paciente con ticket válido llega al mostrador y
no encuentra su medicamento. En un tratamiento crónico o crítico, eso es daño.
"""

import threading

import pytest


@pytest.fixture
def clinic_setup(app, make_user):
    """Farmacia, expendedor, paciente y un ticket listo para entregar."""
    from models import MedicationPickupTicket, Pharmacy, db
    from security import generate_pickup_hash
    from time_utils import colombia_now

    patient = make_user(role='patient', username='pac_disp')
    staff = make_user(role='staff', username='staff_disp')
    expendedor = make_user(role='expendedor', username='exp_disp')

    with app.app_context():
        pharmacy = Pharmacy.query.filter_by(clinic_id=1).first()
        ticket = MedicationPickupTicket(
            clinic_id=1,
            pharmacy_id=pharmacy.id,
            patient_id=patient.id,
            staff_id=staff.id,
            pickup_code='TEST12345678',
            pickup_hash='pendiente',
            meds_json='[{"nombre_med": "Amoxicilina", "cantidad": 10, "unidad": "unidad"}]',
            pickup_date=colombia_now().strftime('%Y-%m-%d'),
            pickup_time='10:00',
            status='autorizado',
        )
        db.session.add(ticket)
        db.session.commit()
        ticket.pickup_hash = generate_pickup_hash(
            ticket.id, ticket.order_id, ticket.patient_id, ticket.clinic_id, ticket.pickup_code
        )
        db.session.commit()
        ticket_id = ticket.id
        pharmacy_id = pharmacy.id

    return {
        'patient': patient,
        'staff': staff,
        'expendedor': expendedor,
        'ticket_id': ticket_id,
        'pharmacy_id': pharmacy_id,
    }


class TestStockLedger:

    def test_dispensing_creates_ledger_entry(self, app, make_stock, make_user):
        from ledger import dispense_units
        from models import Stock, StockLedgerEntry, db

        stock_id = make_stock('Amoxicilina', 100)
        user = make_user(role='expendedor', username='exp_ledger')

        with app.app_context():
            dispense_units(
                clinic_id=1, med_name='Amoxicilina', quantity=10,
                pharmacy_id=Stock.query.get(stock_id).pharmacy_id,
                performed_by_id=user.id, reason='Prueba',
            )
            db.session.commit()

            entry = StockLedgerEntry.query.one()
            assert entry.quantity == -10
            assert entry.balance_before == 100
            assert entry.balance_after == 90
            assert entry.performed_by_id == user.id
            assert Stock.query.get(stock_id).cantidad == 90

    def test_ledger_entries_are_hash_chained(self, app, make_stock, make_user):
        from ledger import dispense_units, verify_chain
        from models import Stock, StockLedgerEntry, db

        stock_id = make_stock('Amoxicilina', 100)
        user = make_user(role='expendedor', username='exp_chain')

        with app.app_context():
            pharmacy_id = Stock.query.get(stock_id).pharmacy_id
            for _ in range(5):
                dispense_units(
                    clinic_id=1, med_name='Amoxicilina', quantity=5,
                    pharmacy_id=pharmacy_id, performed_by_id=user.id,
                )
            db.session.commit()

            entries = StockLedgerEntry.query.order_by(StockLedgerEntry.id.asc()).all()
            assert len(entries) == 5
            for previous, current in zip(entries, entries[1:]):
                assert current.previous_hash == previous.entry_hash
            assert verify_chain(StockLedgerEntry, 1) == []

    def test_insufficient_stock_raises_before_mutating(self, app, make_stock, make_user):
        from ledger import InsufficientStock, dispense_units
        from models import Stock, db

        stock_id = make_stock('Amoxicilina', 5)
        user = make_user(role='expendedor', username='exp_falta')

        with app.app_context():
            pharmacy_id = Stock.query.get(stock_id).pharmacy_id
            with pytest.raises(InsufficientStock) as error:
                dispense_units(
                    clinic_id=1, med_name='Amoxicilina', quantity=10,
                    pharmacy_id=pharmacy_id, performed_by_id=user.id,
                )
            assert error.value.available == 5
            db.session.rollback()
            # El saldo queda intacto.
            assert Stock.query.get(stock_id).cantidad == 5

    def test_balance_never_goes_negative(self, app, make_stock, make_user):
        from ledger import InsufficientStock, dispense_units
        from models import Stock, db

        stock_id = make_stock('Amoxicilina', 3)
        user = make_user(role='expendedor', username='exp_neg')

        with app.app_context():
            pharmacy_id = Stock.query.get(stock_id).pharmacy_id
            dispense_units(clinic_id=1, med_name='Amoxicilina', quantity=3,
                           pharmacy_id=pharmacy_id, performed_by_id=user.id)
            db.session.commit()
            assert Stock.query.get(stock_id).cantidad == 0

            with pytest.raises(InsufficientStock):
                dispense_units(clinic_id=1, med_name='Amoxicilina', quantity=1,
                               pharmacy_id=pharmacy_id, performed_by_id=user.id)
            db.session.rollback()
            assert Stock.query.get(stock_id).cantidad == 0

    def test_reservation_does_not_reduce_physical_stock(self, app, make_stock, make_user):
        from ledger import reserve_units
        from models import Stock, db

        stock_id = make_stock('Amoxicilina', 50)
        user = make_user(role='staff', username='staff_reserva')

        with app.app_context():
            stock = Stock.query.get(stock_id)
            reserve_units(clinic_id=1, med_name='Amoxicilina', quantity=20,
                          pharmacy_id=stock.pharmacy_id, performed_by_id=user.id)
            db.session.commit()

            stock = Stock.query.get(stock_id)
            assert stock.cantidad == 50           # el físico no cambia
            assert stock.cantidad_comprometida == 20

    def test_cannot_reserve_beyond_available(self, app, make_stock, make_user):
        """Lo ya reservado para otro paciente no se puede volver a reservar."""
        from ledger import InsufficientStock, reserve_units
        from models import Stock, db

        stock_id = make_stock('Amoxicilina', 50, committed=45)
        user = make_user(role='staff', username='staff_sobre')

        with app.app_context():
            stock = Stock.query.get(stock_id)
            with pytest.raises(InsufficientStock) as error:
                reserve_units(clinic_id=1, med_name='Amoxicilina', quantity=10,
                              pharmacy_id=stock.pharmacy_id, performed_by_id=user.id)
            assert error.value.available == 5

    def test_adjustment_requires_written_reason(self, app, make_stock, make_user):
        """Un ajuste sin motivo es indistinguible de un faltante encubierto."""
        from ledger import adjust_units
        from models import Stock

        stock_id = make_stock('Amoxicilina', 50)
        user = make_user(role='admin', username='admin_ajuste')

        with app.app_context():
            stock = Stock.query.get(stock_id)
            with pytest.raises(ValueError):
                adjust_units(1, stock, 30, user.id, reason='')
            with pytest.raises(ValueError):
                adjust_units(1, stock, 30, user.id, reason='   ')

    def test_ledger_detects_out_of_band_mutation(self, app, make_stock, make_user):
        """Modificar existencias sin pasar por el libro mayor debe detectarse."""
        from ledger import dispense_units, stock_balance_matches_ledger
        from models import Stock, db

        stock_id = make_stock('Amoxicilina', 100)
        user = make_user(role='expendedor', username='exp_fuera')

        with app.app_context():
            stock = Stock.query.get(stock_id)
            dispense_units(clinic_id=1, med_name='Amoxicilina', quantity=10,
                           pharmacy_id=stock.pharmacy_id, performed_by_id=user.id)
            db.session.commit()

            # Alguien edita la tabla directamente, saltándose ledger.py.
            stock = Stock.query.get(stock_id)
            stock.cantidad = 999
            db.session.commit()

            assert not stock_balance_matches_ledger(1, stock)


class TestConcurrentDispensing:
    """El hallazgo P0-4: dos entregas simultáneas del mismo inventario."""

    def test_two_concurrent_dispenses_cannot_exceed_stock(self, app, make_stock, make_user):
        """Con 10 unidades, dos entregas de 10 no pueden ambas prosperar.

        Antes de la corrección ambas leían el mismo saldo, decidían que había
        suficiente y descontaban por separado: el segundo paciente se quedaba sin
        medicamento con un ticket que el sistema daba por atendido.
        """
        from ledger import InsufficientStock, dispense_units
        from models import Stock, db

        stock_id = make_stock('Amoxicilina', 10)
        user = make_user(role='expendedor', username='exp_concurrente')

        results = []
        barrier = threading.Barrier(2)

        def attempt():
            with app.app_context():
                try:
                    barrier.wait(timeout=5)
                    stock = Stock.query.get(stock_id)
                    dispense_units(
                        clinic_id=1, med_name='Amoxicilina', quantity=10,
                        pharmacy_id=stock.pharmacy_id, performed_by_id=user.id,
                    )
                    db.session.commit()
                    results.append('ok')
                except InsufficientStock:
                    db.session.rollback()
                    results.append('rechazado')
                except Exception as error:
                    db.session.rollback()
                    # SQLite serializa con bloqueo de base completa: el segundo
                    # hilo puede recibir "database is locked" en lugar del
                    # rechazo lógico. PostgreSQL, que es lo exigido en
                    # producción, usa bloqueo de fila y devuelve el rechazo.
                    results.append(f'error:{type(error).__name__}')

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        with app.app_context():
            final = Stock.query.get(stock_id).cantidad

        # La invariante que importa: el saldo nunca queda negativo y como mucho
        # una de las dos entregas prosperó.
        assert final >= 0, 'el saldo no puede quedar negativo'
        assert results.count('ok') <= 1, 'solo una entrega puede prosperar'


class TestDispensingLedger:

    def test_partial_deliveries_accumulate_without_overwriting(self, app, clinic_setup, make_stock):
        """Las entregas parciales deben sumarse, no sobrescribirse.

        `delivered_json` se reescribía en cada entrega y destruía el historial
        anterior. El libro mayor conserva un asiento por acto de entrega.
        """
        from dispatch_engine import confirm_delivery, evaluate_dispatch_status
        from ledger import dispensed_totals
        from models import DispensingLedgerEntry, MedicationPickupTicket, Pharmacy, db

        make_stock('Amoxicilina', 4)

        with app.app_context():
            ticket = MedicationPickupTicket.query.get(clinic_setup['ticket_id'])
            pharmacy = Pharmacy.query.get(clinic_setup['pharmacy_id'])
            expendedor = db.session.get(
                type(ticket).patient.property.mapper.class_, clinic_setup['expendedor'].id
            )

            # Primera entrega: solo hay 4 de las 10 requeridas.
            evaluation = evaluate_dispatch_status(ticket, pharmacy.id)
            result = confirm_delivery(ticket, pharmacy, expendedor, evaluation,
                                      identity_verified=True)
            assert result['status'] == 'parcial'
            assert dispensed_totals(ticket.id)['amoxicilina'] == 4

        # Llega más inventario.
        with app.app_context():
            from models import Stock
            stock = Stock.query.filter_by(clinic_id=1).first()
            stock.cantidad = 6
            db.session.commit()

        with app.app_context():
            ticket = MedicationPickupTicket.query.get(clinic_setup['ticket_id'])
            pharmacy = Pharmacy.query.get(clinic_setup['pharmacy_id'])
            from models import User
            expendedor = db.session.get(User, clinic_setup['expendedor'].id)

            evaluation = evaluate_dispatch_status(ticket, pharmacy.id)
            result = confirm_delivery(ticket, pharmacy, expendedor, evaluation,
                                      identity_verified=True)

            # El total acumulado es 10, no 6: la primera entrega no se perdió.
            assert dispensed_totals(ticket.id)['amoxicilina'] == 10
            assert result['status'] == 'entregado'
            assert DispensingLedgerEntry.query.filter_by(ticket_id=ticket.id).count() == 2

    def test_dispensing_records_who_received(self, app, clinic_setup, make_stock):
        from dispatch_engine import confirm_delivery, evaluate_dispatch_status
        from models import DispensingLedgerEntry, MedicationPickupTicket, Pharmacy, User, db

        make_stock('Amoxicilina', 20)

        with app.app_context():
            ticket = MedicationPickupTicket.query.get(clinic_setup['ticket_id'])
            pharmacy = Pharmacy.query.get(clinic_setup['pharmacy_id'])
            expendedor = db.session.get(User, clinic_setup['expendedor'].id)

            evaluation = evaluate_dispatch_status(ticket, pharmacy.id)
            confirm_delivery(
                ticket, pharmacy, expendedor, evaluation,
                receiver_kind='tercero',
                receiver_name='Maria Gomez',
                receiver_document='1234567890',
                identity_verified=True,
            )

            entry = DispensingLedgerEntry.query.filter_by(ticket_id=ticket.id).one()
            assert entry.receiver_kind == 'tercero'
            assert entry.receiver_name == 'Maria Gomez'
            assert entry.identity_verified is True
            # El documento se guarda con hash, nunca en claro.
            assert entry.receiver_document_hash
            assert '1234567890' not in (entry.receiver_document_hash or '')
