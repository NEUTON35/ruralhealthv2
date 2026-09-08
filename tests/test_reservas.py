"""Las existencias apartadas tienen dueño.

Los cuatro defectos que estas pruebas previenen se reprodujeron sobre el
sistema real. Los cuatro terminan igual: un paciente que caminó hasta el puesto
de salud se va sin su medicamento.

1. **El paciente no podía recoger lo suyo.** Al emitir el ticket se apartaban
   sus unidades en `Stock.cantidad_comprometida`. En el mostrador, la
   evaluación calculaba disponible = cantidad − comprometida: le restaba al
   paciente su propia reserva. Con diez unidades en estante y un ticket por
   diez, disponible daba cero. El ticket caía a «sin stock» con el frasco
   delante, y reactivarlo daba lo mismo: quedaba muerto. En un puesto con
   existencias justas (el caso normal) pasaba siempre.

2. **La reserva de un paciente se la llevaba otro.** Al dispensar se restaba de
   `cantidad_comprometida` sin mirar de quién era.

3. **El mismo ticket se entregaba dos veces.** El estado se comprobaba fuera de
   toda transacción: un doble clic entregaba veinte unidades contra una
   prescripción de diez.

4. **Se dispensaba contra órdenes anuladas.** La validez se comprobaba al
   emitir el ticket y nunca más. El profesional anulaba la orden por una
   alergia, y el ticket seguía entregando.
"""

import pytest


@pytest.fixture
def mostrador(app, make_user, make_stock):
    """Farmacia, expendedor, paciente y una orden vigente por diez unidades."""
    from models import MedicalOrder, MedicationPickupTicket, Pharmacy, db
    from security import generate_pickup_hash, generate_signed_order_hash
    from time_utils import colombia_now
    from datetime import timedelta

    doctor = make_user(role='doctor', username='doc_res',
                       medical_registration='RM-RES')
    patient = make_user(role='patient', username='pac_res')
    staff = make_user(role='staff', username='sta_res')
    expendedor = make_user(role='expendedor', username='exp_res')

    meds = ('[{"nombre_med": "Amoxicilina", "cantidad": 10, '
            '"unidad": "tableta"}]')

    with app.app_context():
        pharmacy = Pharmacy.query.filter_by(clinic_id=1).first()
        expendedor_row = db.session.get(type(patient), expendedor.id)
        expendedor_row.pharmacy_id = pharmacy.id

        sello = generate_signed_order_hash(doctor.id, patient.id, meds, 1)
        orden = MedicalOrder(
            clinic_id=1, order_number='OM-RES', doctor_id=doctor.id,
            patient_id=patient.id, meds_json=meds, status='activa',
            verification_hash=sello, hash_seguridad=sello,
            created_at=colombia_now(),
            expires_at=colombia_now() + timedelta(days=30))
        db.session.add(orden)
        db.session.flush()

        ticket = MedicationPickupTicket(
            clinic_id=1, order_id=orden.id, pharmacy_id=pharmacy.id,
            patient_id=patient.id, staff_id=staff.id,
            pickup_code='RESERVA12345', pickup_hash='pendiente',
            meds_json=meds,
            pickup_date=colombia_now().strftime('%Y-%m-%d'),
            pickup_time='10:00', status='autorizado')
        db.session.add(ticket)
        db.session.commit()
        ticket.pickup_hash = generate_pickup_hash(
            ticket.id, ticket.order_id, ticket.patient_id, ticket.clinic_id,
            ticket.pickup_code)
        db.session.commit()
        return {'ticket_id': ticket.id, 'orden_id': orden.id,
                'pharmacy_id': pharmacy.id, 'expendedor_id': expendedor.id,
                'patient_id': patient.id, 'staff_id': staff.id}


class TestElTicketVeSuPropiaReserva:

    def test_lo_apartado_para_este_ticket_le_sigue_estando_disponible(
            self, app, make_stock, mostrador):
        """El caso exacto: diez en estante, diez apartadas para este ticket."""
        from dispatch_engine import evaluate_dispatch_status
        from ledger import reservar_para_ticket
        from models import MedicationPickupTicket, Stock, db

        stock_id = make_stock('Amoxicilina', 10,
                              pharmacy_id=mostrador['pharmacy_id'])
        with app.app_context():
            stock = db.session.get(Stock, stock_id)
            ticket = db.session.get(MedicationPickupTicket,
                                    mostrador['ticket_id'])
            reservar_para_ticket(1, stock, ticket.id, 10)
            db.session.commit()

            # Todo el estante está comprometido... para este mismo ticket.
            assert db.session.get(Stock, stock_id).cantidad_comprometida == 10

            evaluacion = evaluate_dispatch_status(ticket,
                                                  mostrador['pharmacy_id'])
        assert evaluacion['status'] == 'full', (
            'el ticket no ve las unidades que tiene apartadas para sí mismo')

    def test_lo_apartado_por_otro_ticket_no_esta_disponible(
            self, app, make_user, make_stock, mostrador):
        """La reserva ajena sí se resta: para eso existe."""
        from dispatch_engine import evaluate_dispatch_status
        from ledger import reservar_para_ticket
        from models import MedicationPickupTicket, Stock, db
        from time_utils import colombia_now

        stock_id = make_stock('Amoxicilina', 10,
                              pharmacy_id=mostrador['pharmacy_id'])
        with app.app_context():
            otro = MedicationPickupTicket(
                clinic_id=1, pharmacy_id=mostrador['pharmacy_id'],
                patient_id=mostrador['patient_id'],
                staff_id=mostrador['staff_id'], pickup_code='OTRO12345678',
                pickup_hash='x', meds_json='[]',
                pickup_date=colombia_now().strftime('%Y-%m-%d'),
                pickup_time='11:00', status='autorizado')
            db.session.add(otro)
            db.session.flush()

            stock = db.session.get(Stock, stock_id)
            reservar_para_ticket(1, stock, otro.id, 10)
            db.session.commit()

            ticket = db.session.get(MedicationPickupTicket,
                                    mostrador['ticket_id'])
            evaluacion = evaluate_dispatch_status(ticket,
                                                  mostrador['pharmacy_id'])
        assert evaluacion['status'] == 'none', (
            'se ofrecieron unidades apartadas para otro paciente')

    def test_entregar_solo_consume_la_reserva_propia(
            self, app, make_user, make_stock, mostrador):
        """Una entrega sin reserva propia no puede comerse la de otro."""
        from ledger import dispense_units, reservar_para_ticket
        from models import MedicationPickupTicket, Stock, db
        from time_utils import colombia_now

        stock_id = make_stock('Amoxicilina', 20,
                              pharmacy_id=mostrador['pharmacy_id'])
        with app.app_context():
            cronico = MedicationPickupTicket(
                clinic_id=1, pharmacy_id=mostrador['pharmacy_id'],
                patient_id=mostrador['patient_id'],
                staff_id=mostrador['staff_id'], pickup_code='CRONICO12345',
                pickup_hash='x', meds_json='[]',
                pickup_date=colombia_now().strftime('%Y-%m-%d'),
                pickup_time='11:00', status='autorizado')
            db.session.add(cronico)
            db.session.flush()
            reservar_para_ticket(1, db.session.get(Stock, stock_id),
                                 cronico.id, 10)
            db.session.commit()

            # Otro ticket, sin reserva, se lleva diez.
            dispense_units(clinic_id=1, med_name='Amoxicilina', quantity=10,
                           pharmacy_id=mostrador['pharmacy_id'],
                           performed_by_id=mostrador['expendedor_id'],
                           ticket_id=mostrador['ticket_id'])
            db.session.commit()

            stock = db.session.get(Stock, stock_id)
            assert stock.cantidad == 10
            assert stock.cantidad_comprometida == 10, (
                'la entrega se comió la reserva del paciente crónico')


class TestLaOrdenTieneQueSeguirVigente:

    def test_no_se_entrega_contra_una_orden_anulada(
            self, app, make_stock, mostrador):
        from dispatch_engine import confirm_delivery, evaluate_dispatch_status
        from models import (MedicalOrder, MedicationPickupTicket, Pharmacy,
                            Stock, User, db)
        from time_utils import colombia_now

        stock_id = make_stock('Amoxicilina', 100,
                              pharmacy_id=mostrador['pharmacy_id'])
        with app.app_context():
            orden = db.session.get(MedicalOrder, mostrador['orden_id'])
            orden.status = 'anulada'
            orden.annulled_at = colombia_now()
            db.session.commit()

            ticket = db.session.get(MedicationPickupTicket,
                                    mostrador['ticket_id'])
            pharmacy = db.session.get(Pharmacy, mostrador['pharmacy_id'])
            expendedor = db.session.get(User, mostrador['expendedor_id'])
            evaluacion = evaluate_dispatch_status(ticket, pharmacy.id)
            resultado = confirm_delivery(ticket, pharmacy, expendedor,
                                         evaluacion)

            assert not resultado['delivered'], (
                'se entregó contra una orden que el profesional anuló')
            assert db.session.get(Stock, stock_id).cantidad == 100

    def test_no_se_entrega_contra_una_orden_vencida(
            self, app, make_stock, mostrador):
        from dispatch_engine import confirm_delivery, evaluate_dispatch_status
        from models import (MedicalOrder, MedicationPickupTicket, Pharmacy,
                            Stock, User, db)
        from datetime import timedelta
        from time_utils import colombia_now

        stock_id = make_stock('Amoxicilina', 100,
                              pharmacy_id=mostrador['pharmacy_id'])
        with app.app_context():
            orden = db.session.get(MedicalOrder, mostrador['orden_id'])
            orden.expires_at = colombia_now() - timedelta(days=1)
            db.session.commit()

            ticket = db.session.get(MedicationPickupTicket,
                                    mostrador['ticket_id'])
            resultado = confirm_delivery(
                ticket, db.session.get(Pharmacy, mostrador['pharmacy_id']),
                db.session.get(User, mostrador['expendedor_id']),
                evaluate_dispatch_status(ticket, mostrador['pharmacy_id']))

            assert not resultado['delivered']
            assert db.session.get(Stock, stock_id).cantidad == 100


class TestNoSeEntregaDosVeces:

    def test_un_ticket_ya_entregado_no_vuelve_a_descontar(
            self, app, make_stock, mostrador):
        """El estado se revalida bajo bloqueo, dentro de la transacción."""
        from dispatch_engine import confirm_delivery, evaluate_dispatch_status
        from models import (MedicationPickupTicket, Pharmacy, Stock, User, db)

        stock_id = make_stock('Amoxicilina', 100,
                              pharmacy_id=mostrador['pharmacy_id'])
        with app.app_context():
            ticket = db.session.get(MedicationPickupTicket,
                                    mostrador['ticket_id'])
            pharmacy = db.session.get(Pharmacy, mostrador['pharmacy_id'])
            expendedor = db.session.get(User, mostrador['expendedor_id'])

            evaluacion = evaluate_dispatch_status(ticket, pharmacy.id)
            confirm_delivery(ticket, pharmacy, expendedor, evaluacion)
            assert db.session.get(Stock, stock_id).cantidad == 90

            # La misma evaluación, reenviada: el ticket ya está entregado.
            segundo = confirm_delivery(ticket, pharmacy, expendedor,
                                       evaluacion)
            assert not segundo['delivered']
            assert db.session.get(Stock, stock_id).cantidad == 90, (
                'se descontó dos veces contra la misma prescripción')


class TestLiberarReservas:

    def test_soltar_devuelve_las_unidades_al_comun(
            self, app, make_stock, mostrador):
        from ledger import liberar_reservas, reservar_para_ticket
        from models import Stock, db

        stock_id = make_stock('Amoxicilina', 10,
                              pharmacy_id=mostrador['pharmacy_id'])
        with app.app_context():
            reservar_para_ticket(1, db.session.get(Stock, stock_id),
                                 mostrador['ticket_id'], 10)
            db.session.commit()
            assert db.session.get(Stock, stock_id).cantidad_comprometida == 10

            liberar_reservas(mostrador['ticket_id'], 'Ticket caducado')
            db.session.commit()
            assert db.session.get(Stock, stock_id).cantidad_comprometida == 0

    def test_reservar_dos_veces_no_aparta_el_doble(
            self, app, make_stock, mostrador):
        """El botón de reactivar se pulsaba varias veces y cada una apartaba."""
        from ledger import reservar_para_ticket
        from models import Stock, db

        stock_id = make_stock('Amoxicilina', 100,
                              pharmacy_id=mostrador['pharmacy_id'])
        with app.app_context():
            for _ in range(4):
                reservar_para_ticket(1, db.session.get(Stock, stock_id),
                                     mostrador['ticket_id'], 10)
                db.session.commit()
            assert db.session.get(Stock, stock_id).cantidad_comprometida == 10
