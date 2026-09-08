"""Punto de reposición por medicamento y sede.

El defecto que estas pruebas previenen: la pantalla del expendedor avisaba de
stock bajo con un umbral fijo de cinco unidades, igual para cualquier
medicamento. Un antibiótico con ocho unidades no producía ningún aviso aunque
el punto de reposición real de esa sede fuera cincuenta. Cuando el aviso llega
tarde, el que se queda sin tratamiento es el paciente que caminó dos horas
hasta el puesto de salud.

La otra mitad de lo que se prueba aquí es que el sistema no afirme lo que no
sabe: sin punto de reposición definido, «no está agotado» es todo lo que puede
decir. No «está bien».
"""

import pytest


class TestEstadoDeStock:
    """Una sola definición de «stock bajo» para toda la aplicación."""

    def test_ocho_unidades_bajo_un_punto_de_cincuenta_es_stock_bajo(self, app, make_stock):
        """El caso exacto del defecto: con el umbral fijo de 5 salía normal."""
        from models import Stock, db
        from pharmacy_utils import STOCK_BAJO, estado_de_stock

        stock_id = make_stock('Amoxicilina 500mg', 8, minimo=50)
        with app.app_context():
            assert estado_de_stock(db.session.get(Stock, stock_id)) == STOCK_BAJO

    def test_por_encima_del_punto_de_reposicion_es_normal(self, app, make_stock):
        from models import Stock, db
        from pharmacy_utils import STOCK_NORMAL, estado_de_stock

        stock_id = make_stock('Losartán 50mg', 120, minimo=30)
        with app.app_context():
            assert estado_de_stock(db.session.get(Stock, stock_id)) == STOCK_NORMAL

    def test_justo_en_el_punto_de_reposicion_ya_es_bajo(self, app, make_stock):
        """El punto de reposición es «reponer al llegar aquí», no «al bajar de aquí»."""
        from models import Stock, db
        from pharmacy_utils import STOCK_BAJO, estado_de_stock

        stock_id = make_stock('Losartán 50mg', 30, minimo=30)
        with app.app_context():
            assert estado_de_stock(db.session.get(Stock, stock_id)) == STOCK_BAJO

    def test_sin_punto_de_reposicion_no_es_lo_mismo_que_normal(self, app, make_stock):
        """Sin umbral el sistema no puede decir que el stock esté bien."""
        from models import Stock, db
        from pharmacy_utils import STOCK_NORMAL, STOCK_SIN_UMBRAL, estado_de_stock

        stock_id = make_stock('Acetaminofén 500mg', 45, minimo=0)
        with app.app_context():
            estado = estado_de_stock(db.session.get(Stock, stock_id))
        assert estado == STOCK_SIN_UMBRAL
        assert estado != STOCK_NORMAL

    def test_agotado_manda_sobre_cualquier_umbral(self, app, make_stock):
        from models import Stock, db
        from pharmacy_utils import STOCK_AGOTADO, estado_de_stock

        for minimo in (0, 30):
            stock_id = make_stock(f'Vacío {minimo}', 0, minimo=minimo)
            with app.app_context():
                assert estado_de_stock(db.session.get(Stock, stock_id)) == STOCK_AGOTADO

    def test_lo_comprometido_no_cuenta_como_disponible(self, app, make_stock):
        """Reservado para otro paciente no es stock del que se pueda disponer."""
        from models import Stock, db
        from pharmacy_utils import STOCK_BAJO, estado_de_stock

        stock_id = make_stock('Losartán 50mg', 60, committed=45, minimo=30)
        with app.app_context():
            # 60 - 45 = 15 disponibles, por debajo de 30.
            assert estado_de_stock(db.session.get(Stock, stock_id)) == STOCK_BAJO

    def test_sin_fila_de_stock_no_revienta(self, app):
        from pharmacy_utils import STOCK_AGOTADO, estado_de_stock

        with app.app_context():
            assert estado_de_stock(None) == STOCK_AGOTADO


class TestAlertaPorPuntoDeReposicion:
    """El umbral solo sirve si alguien se entera de que se cruzó."""

    def test_cruzar_el_punto_abre_una_alerta(self, app, make_stock):
        from models import ALERTA_POR_PUNTO_REPOSICION, ReplenishmentAlert, Stock, db
        from pharmacy_utils import alerta_por_punto_de_reposicion

        stock_id = make_stock('Amoxicilina 500mg', 8, minimo=50)
        with app.app_context():
            alerta_por_punto_de_reposicion(db.session.get(Stock, stock_id))
            db.session.commit()
            alerta = ReplenishmentAlert.query.filter_by(
                origin=ALERTA_POR_PUNTO_REPOSICION).one()
            assert alerta.med_name == 'Amoxicilina 500mg'
            assert alerta.required_quantity == 50
            assert alerta.available_quantity == 8
            assert alerta.status == 'abierta'

    def test_sin_umbral_definido_no_se_inventa_una_alerta(self, app, make_stock):
        from models import ReplenishmentAlert, Stock, db
        from pharmacy_utils import alerta_por_punto_de_reposicion

        stock_id = make_stock('Acetaminofén 500mg', 3, minimo=0)
        with app.app_context():
            alerta_por_punto_de_reposicion(db.session.get(Stock, stock_id))
            db.session.commit()
            assert ReplenishmentAlert.query.count() == 0

    def test_por_encima_del_punto_no_hay_alerta(self, app, make_stock):
        from models import ReplenishmentAlert, Stock, db
        from pharmacy_utils import alerta_por_punto_de_reposicion

        stock_id = make_stock('Losartán 50mg', 120, minimo=30)
        with app.app_context():
            alerta_por_punto_de_reposicion(db.session.get(Stock, stock_id))
            db.session.commit()
            assert ReplenishmentAlert.query.count() == 0

    def test_no_se_repite_la_alerta_del_mismo_medicamento(self, app, make_stock):
        """Quince avisos del mismo losartán equivalen a ninguno."""
        from models import ReplenishmentAlert, Stock, db
        from pharmacy_utils import alerta_por_punto_de_reposicion

        stock_id = make_stock('Losartán 50mg', 10, minimo=30)
        with app.app_context():
            for _ in range(4):
                alerta_por_punto_de_reposicion(db.session.get(Stock, stock_id))
                db.session.commit()
            assert ReplenishmentAlert.query.count() == 1

    def test_reponer_por_encima_del_punto_cierra_la_alerta(self, app, make_stock):
        from models import ALERTA_POR_PUNTO_REPOSICION, ReplenishmentAlert, Stock, db
        from pharmacy_utils import (alerta_por_punto_de_reposicion,
                                    resolver_alerta_de_punto_de_reposicion)

        stock_id = make_stock('Losartán 50mg', 10, minimo=30)
        with app.app_context():
            stock = db.session.get(Stock, stock_id)
            alerta_por_punto_de_reposicion(stock)
            db.session.commit()

            stock.cantidad = 200
            resolver_alerta_de_punto_de_reposicion(stock)
            db.session.commit()

            alerta = ReplenishmentAlert.query.filter_by(
                origin=ALERTA_POR_PUNTO_REPOSICION).one()
            assert alerta.status == 'resuelta'
            assert alerta.resolved_at is not None

    def test_reponer_no_borra_la_alerta_por_demanda(self, app, make_stock):
        """Que llegue mercancía después no deshace que un paciente se quedara sin ella."""
        from models import ALERTA_POR_DEMANDA, ReplenishmentAlert, Stock, db
        from pharmacy_utils import (create_replenishment_alert,
                                    resolver_alerta_de_punto_de_reposicion)

        stock_id = make_stock('Losartán 50mg', 2, minimo=30)
        with app.app_context():
            stock = db.session.get(Stock, stock_id)
            create_replenishment_alert(
                stock.clinic_id, stock.pharmacy_id,
                {'nombre_med': 'Losartán 50mg', 'cantidad': 30}, 2)
            db.session.commit()

            stock.cantidad = 200
            resolver_alerta_de_punto_de_reposicion(stock)
            db.session.commit()

            alerta = ReplenishmentAlert.query.filter_by(origin=ALERTA_POR_DEMANDA).one()
            assert alerta.status == 'abierta'

    def test_la_alerta_antigua_queda_marcada_como_de_demanda(self, app, make_stock):
        """El origen por defecto describe lo único que el sistema sabía generar."""
        from models import ALERTA_POR_DEMANDA, Stock, db
        from pharmacy_utils import create_replenishment_alert

        stock_id = make_stock('Losartán 50mg', 2)
        with app.app_context():
            stock = db.session.get(Stock, stock_id)
            alerta = create_replenishment_alert(
                stock.clinic_id, stock.pharmacy_id,
                {'nombre_med': 'Losartán 50mg', 'cantidad': 30}, 2)
            db.session.commit()
            assert alerta.origin == ALERTA_POR_DEMANDA


class TestEntregaCruzaElPunto:
    """Entregar baja el stock; bajar el stock puede cruzar el umbral."""

    @pytest.fixture
    def ticket_listo(self, app, make_user, make_stock):
        from models import MedicationPickupTicket, Pharmacy, db
        from security import generate_pickup_hash
        from time_utils import colombia_now

        patient = make_user(role='patient', username='pac_punto')
        staff = make_user(role='staff', username='staff_punto')
        expendedor = make_user(role='expendedor', username='exp_punto')

        with app.app_context():
            pharmacy = Pharmacy.query.filter_by(clinic_id=1).first()
            ticket = MedicationPickupTicket(
                clinic_id=1,
                pharmacy_id=pharmacy.id,
                patient_id=patient.id,
                staff_id=staff.id,
                pickup_code='PUNTO1234567',
                pickup_hash='pendiente',
                meds_json='[{"nombre_med": "Losart\\u00e1n 50mg", "cantidad": 10, "unidad": "unidad"}]',
                pickup_date=colombia_now().strftime('%Y-%m-%d'),
                pickup_time='10:00',
                status='autorizado',
            )
            db.session.add(ticket)
            db.session.commit()
            ticket.pickup_hash = generate_pickup_hash(
                ticket.id, ticket.order_id, ticket.patient_id,
                ticket.clinic_id, ticket.pickup_code)
            db.session.commit()
            return {'ticket_id': ticket.id, 'pharmacy_id': pharmacy.id,
                    'expendedor_id': expendedor.id}

    def test_la_entrega_que_cruza_el_punto_deja_alerta(self, app, make_stock, ticket_listo):
        from dispatch_engine import confirm_delivery, evaluate_dispatch_status
        from models import (ALERTA_POR_PUNTO_REPOSICION, MedicationPickupTicket,
                            Pharmacy, ReplenishmentAlert, User, db)

        # 35 disponibles, punto de reposición 30: quedan 25 tras entregar 10.
        make_stock('Losartán 50mg', 35, minimo=30)
        with app.app_context():
            ticket = db.session.get(MedicationPickupTicket, ticket_listo['ticket_id'])
            pharmacy = db.session.get(Pharmacy, ticket_listo['pharmacy_id'])
            expendedor = db.session.get(User, ticket_listo['expendedor_id'])
            evaluacion = evaluate_dispatch_status(ticket, pharmacy.id)
            confirm_delivery(ticket, pharmacy, expendedor, evaluacion)

            alerta = ReplenishmentAlert.query.filter_by(
                origin=ALERTA_POR_PUNTO_REPOSICION).one()
            assert alerta.med_name == 'Losartán 50mg'
            assert alerta.available_quantity == 25

    def test_la_entrega_que_no_cruza_el_punto_no_deja_alerta(self, app, make_stock, ticket_listo):
        from dispatch_engine import confirm_delivery, evaluate_dispatch_status
        from models import (MedicationPickupTicket, Pharmacy, ReplenishmentAlert,
                            User, db)

        # 200 disponibles, punto 30: quedan 190. No hay nada que avisar.
        make_stock('Losartán 50mg', 200, minimo=30)
        with app.app_context():
            ticket = db.session.get(MedicationPickupTicket, ticket_listo['ticket_id'])
            pharmacy = db.session.get(Pharmacy, ticket_listo['pharmacy_id'])
            expendedor = db.session.get(User, ticket_listo['expendedor_id'])
            evaluacion = evaluate_dispatch_status(ticket, pharmacy.id)
            confirm_delivery(ticket, pharmacy, expendedor, evaluacion)

            assert ReplenishmentAlert.query.count() == 0


class TestQuienPuedeFijarlo:
    """El expendedor, para su sede. El administrador, para las de su clínica."""

    def test_el_expendedor_lo_fija_en_su_farmacia(self, app, client, login, make_user, make_stock):
        from models import Pharmacy, Stock, db

        with app.app_context():
            pharmacy_id = Pharmacy.query.filter_by(clinic_id=1).first().id
        make_user(role='expendedor', username='exp_fija', pharmacy_id=pharmacy_id)
        stock_id = make_stock('Losartán 50mg', 120, pharmacy_id=pharmacy_id)

        login('exp_fija')
        respuesta = client.post('/expendedor/dashboard', data={
            'action': 'set_punto_reposicion',
            'stock_id': stock_id,
            'cantidad_minima': 40,
        })
        assert respuesta.status_code in (200, 302)
        with app.app_context():
            assert db.session.get(Stock, stock_id).cantidad_minima == 40

    def test_el_expendedor_no_toca_el_de_otra_farmacia(self, app, client, login, make_user, make_stock):
        from models import Pharmacy, Stock, db

        with app.app_context():
            propia = Pharmacy.query.filter_by(clinic_id=1).first()
            ajena = Pharmacy(clinic_id=1, name='Farmacia vecina', is_active=True)
            db.session.add(ajena)
            db.session.commit()
            propia_id, ajena_id = propia.id, ajena.id

        make_user(role='expendedor', username='exp_limitado', pharmacy_id=propia_id)
        stock_id = make_stock('Losartán 50mg', 120, pharmacy_id=ajena_id, minimo=7)

        login('exp_limitado')
        client.post('/expendedor/dashboard', data={
            'action': 'set_punto_reposicion',
            'stock_id': stock_id,
            'cantidad_minima': 999,
        })
        with app.app_context():
            assert db.session.get(Stock, stock_id).cantidad_minima == 7

    def test_el_administrador_lo_fija_en_cualquier_sede_de_su_clinica(
            self, app, client, login, make_user, make_stock):
        from models import Pharmacy, Stock, db

        with app.app_context():
            otra = Pharmacy(clinic_id=1, name='Farmacia de la vereda', is_active=True)
            db.session.add(otra)
            db.session.commit()
            otra_id = otra.id

        make_user(role='admin', username='adm_fija')
        stock_id = make_stock('Losartán 50mg', 120, pharmacy_id=otra_id)

        login('adm_fija')
        client.post('/admin/dashboard', data={
            'action': 'set_punto_reposicion',
            'stock_id': stock_id,
            'cantidad_minima': 60,
        })
        with app.app_context():
            assert db.session.get(Stock, stock_id).cantidad_minima == 60

    def test_el_administrador_no_alcanza_la_clinica_ajena(
            self, app, client, login, make_user, make_stock):
        from models import Clinic, Pharmacy, Stock, db

        with app.app_context():
            otra = Clinic(name='Clinica Norte', legal_name='Clinica Norte SAS',
                          status='active')
            db.session.add(otra)
            db.session.flush()
            farmacia = Pharmacy(clinic_id=otra.id, name='Farmacia Norte',
                                is_active=True)
            db.session.add(farmacia)
            db.session.commit()
            otra_id, farmacia_id = otra.id, farmacia.id

        make_user(role='admin', username='adm_curioso', clinic_id=1)
        stock_id = make_stock('Losartán 50mg', 120, clinic_id=otra_id,
                              pharmacy_id=farmacia_id, minimo=5)

        login('adm_curioso')
        client.post('/admin/dashboard', data={
            'action': 'set_punto_reposicion',
            'stock_id': stock_id,
            'cantidad_minima': 999,
        })
        with app.app_context():
            fila = Stock.query.filter_by(id=stock_id).execution_options(
                include_all_clinics=True).first()
            assert fila.cantidad_minima == 5

    def test_el_inventario_del_personal_guarda_el_punto(
            self, app, client, login, make_user, make_stock):
        from models import Stock, db

        make_user(role='staff', username='staff_fija')
        stock_id = make_stock('Losartán 50mg', 120)

        login('staff_fija')
        with app.app_context():
            fila = db.session.get(Stock, stock_id)
            datos = {
                'stock_id': stock_id,
                'pharmacy_id': fila.pharmacy_id,
                'nombre_med': fila.nombre_med,
                'cantidad': fila.cantidad,
                'unidad': fila.unidad,
                'cantidad_minima': 25,
            }
        client.post('/staff/inventory', data=datos)
        with app.app_context():
            assert db.session.get(Stock, stock_id).cantidad_minima == 25
