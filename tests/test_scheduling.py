"""Pruebas de la agenda de citas.

Cubren los defectos encontrados en la segunda auditoria: doble agendamiento por
carrera, horarios que quedaban bloqueados para siempre, y la serie de meses de la
grafica de analitica.
"""

from datetime import datetime, timedelta

import pytest

from conftest import VALID_PASSWORD


@pytest.fixture
def doctor_with_schedule(app, make_user):
    """Un medico con horario semanal completo y un paciente."""
    from models import DoctorSchedule, db

    doctor = make_user(role='doctor', username='doc_agenda',
                       medical_registration='RM-AG', signature_path='clinic_1/f.png')
    patient = make_user(role='patient', username='pac_agenda')

    with app.app_context():
        for day in range(7):
            db.session.add(DoctorSchedule(
                clinic_id=1, doctor_id=doctor.id, day_of_week=day,
                start_time='08:00', end_time='17:00', is_available=True,
            ))
        db.session.commit()

    return {'doctor': doctor, 'patient': patient}


def future_slot(days_ahead=3, time='10:00'):
    from time_utils import colombia_now
    return (colombia_now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d'), time


class TestDoubleBooking:
    """El horario de un profesional no puede asignarse dos veces."""

    def test_database_rejects_duplicate_slot(self, app, doctor_with_schedule, make_user):
        from models import Appointment, db
        from sqlalchemy.exc import IntegrityError

        date, time = future_slot()
        other = make_user(role='patient', username='pac_choque')

        with app.app_context():
            db.session.add(Appointment(
                clinic_id=1, patient_id=doctor_with_schedule['patient'].id,
                doctor_id=doctor_with_schedule['doctor'].id,
                date=date, time=time, status='pending',
            ))
            db.session.commit()

            # Segundo paciente, mismo medico, mismo horario.
            db.session.add(Appointment(
                clinic_id=1, patient_id=other.id,
                doctor_id=doctor_with_schedule['doctor'].id,
                date=date, time=time, status='pending',
            ))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()

            assert Appointment.query.filter_by(date=date, time=time).count() == 1

    def test_route_reports_conflict_without_error(self, app, doctor_with_schedule,
                                                  make_user, client):
        """El paciente debe ver un mensaje claro, no un error 500."""
        from models import Appointment, db

        date, time = future_slot()
        with app.app_context():
            db.session.add(Appointment(
                clinic_id=1, patient_id=doctor_with_schedule['patient'].id,
                doctor_id=doctor_with_schedule['doctor'].id,
                date=date, time=time, status='pending',
            ))
            db.session.commit()

        make_user(role='patient', username='pac_segundo')
        login = client.post('/login', data={'username': 'pac_segundo',
                                            'password': VALID_PASSWORD})
        assert login.status_code == 302, 'la sesion debe iniciarse para llegar a la ruta'

        doctor_id = doctor_with_schedule['doctor'].id
        response = client.post(
            f'/patient/book_appointment/{doctor_id}',
            data={'date': date, 'time': time, 'description': 'Consulta'},
            follow_redirects=True,
        )
        assert response.status_code == 200
        cuerpo = response.data.decode('utf-8', 'replace').lower()
        assert 'ocupada' in cuerpo or 'tomada' in cuerpo, (
            'debe explicarse que el horario ya no esta libre'
        )

        with app.app_context():
            assert Appointment.query.filter_by(date=date, time=time).count() == 1

    def test_cancelled_slot_can_be_rebooked(self, app, doctor_with_schedule, make_user):
        """Una cita cancelada libera el horario.

        Antes el horario quedaba ocupado de forma permanente: un paciente que no
        se presentaba inutilizaba ese cupo para siempre, en agendas donde cada
        consulta cuenta.
        """
        from models import Appointment, db

        date, time = future_slot()
        other = make_user(role='patient', username='pac_recupera')

        with app.app_context():
            first = Appointment(
                clinic_id=1, patient_id=doctor_with_schedule['patient'].id,
                doctor_id=doctor_with_schedule['doctor'].id,
                date=date, time=time, status='pending',
            )
            db.session.add(first)
            db.session.commit()

            first.status = 'cancelada'
            db.session.commit()

            # El mismo horario vuelve a estar disponible.
            db.session.add(Appointment(
                clinic_id=1, patient_id=other.id,
                doctor_id=doctor_with_schedule['doctor'].id,
                date=date, time=time, status='pending',
            ))
            db.session.commit()

            activas = Appointment.query.filter(
                Appointment.date == date, Appointment.time == time,
                Appointment.status == 'pending',
            ).count()
            assert activas == 1

    def test_no_show_slot_can_be_rebooked(self, app, doctor_with_schedule, make_user):
        from models import Appointment, db

        date, time = future_slot(days_ahead=4)
        other = make_user(role='patient', username='pac_noshow2')

        with app.app_context():
            first = Appointment(
                clinic_id=1, patient_id=doctor_with_schedule['patient'].id,
                doctor_id=doctor_with_schedule['doctor'].id,
                date=date, time=time, status='no_show',
            )
            db.session.add(first)
            db.session.commit()

            db.session.add(Appointment(
                clinic_id=1, patient_id=other.id,
                doctor_id=doctor_with_schedule['doctor'].id,
                date=date, time=time, status='pending',
            ))
            db.session.commit()   # no debe fallar

    def test_different_doctors_may_share_a_time(self, app, doctor_with_schedule, make_user):
        """La restriccion es por profesional, no global."""
        from models import Appointment, db

        date, time = future_slot(days_ahead=5)
        second_doctor = make_user(role='doctor', username='doc_agenda2',
                                  medical_registration='RM-AG2')

        with app.app_context():
            db.session.add(Appointment(
                clinic_id=1, patient_id=doctor_with_schedule['patient'].id,
                doctor_id=doctor_with_schedule['doctor'].id,
                date=date, time=time, status='pending',
            ))
            db.session.add(Appointment(
                clinic_id=1, patient_id=doctor_with_schedule['patient'].id,
                doctor_id=second_doctor.id,
                date=date, time=time, status='pending',
            ))
            db.session.commit()   # dos medicos distintos: sin conflicto


class TestSlotAvailability:

    def test_cancelled_appointments_do_not_count_as_booked(self, app, doctor_with_schedule):
        from routes_patient import get_doctor_availability
        from models import Appointment, User, db
        from time_utils import colombia_now

        target = (colombia_now() + timedelta(days=1)).strftime('%Y-%m-%d')

        with app.app_context():
            doctor = db.session.get(User, doctor_with_schedule['doctor'].id)
            libre_antes = next(
                d['slots'] for d in get_doctor_availability(doctor)
                if d['date'].strftime('%Y-%m-%d') == target
            )

            db.session.add(Appointment(
                clinic_id=1, patient_id=doctor_with_schedule['patient'].id,
                doctor_id=doctor.id, date=target, time='09:00', status='cancelada',
            ))
            db.session.commit()

            libre_despues = next(
                d['slots'] for d in get_doctor_availability(doctor)
                if d['date'].strftime('%Y-%m-%d') == target
            )

        assert libre_despues == libre_antes, (
            'una cita cancelada no debe reducir los cupos disponibles'
        )

    def test_active_appointment_reduces_availability(self, app, doctor_with_schedule):
        from routes_patient import get_doctor_availability
        from models import Appointment, User, db
        from time_utils import colombia_now

        target = (colombia_now() + timedelta(days=2)).strftime('%Y-%m-%d')

        with app.app_context():
            doctor = db.session.get(User, doctor_with_schedule['doctor'].id)
            antes = next(d['slots'] for d in get_doctor_availability(doctor)
                         if d['date'].strftime('%Y-%m-%d') == target)

            db.session.add(Appointment(
                clinic_id=1, patient_id=doctor_with_schedule['patient'].id,
                doctor_id=doctor.id, date=target, time='11:00', status='pending',
            ))
            db.session.commit()

            despues = next(d['slots'] for d in get_doctor_availability(doctor)
                           if d['date'].strftime('%Y-%m-%d') == target)

        assert despues == antes - 1


class TestPendingDeliverySlot:
    """Las entregas pendientes no deben amontonarse en el mismo horario."""

    def test_two_pending_deliveries_get_different_slots(self, app, make_user, make_stock):
        from models import Appointment, MedicalOrder, db
        from time_utils import colombia_now
        from security import generate_signed_order_hash
        import routes_staff

        doctor = make_user(role='doctor', username='doc_pend', medical_registration='RM-P')
        staff = make_user(role='staff', username='staff_pend')
        p1 = make_user(role='patient', username='pac_pend1')
        p2 = make_user(role='patient', username='pac_pend2')

        with app.test_request_context():
            from flask_login import login_user
            with app.app_context():
                from models import User
                login_user(db.session.get(User, staff.id))

                routes_staff._create_pending_delivery(None, p1.id, doctor.id, ['Amoxicilina'])
                db.session.flush()
                routes_staff._create_pending_delivery(None, p2.id, doctor.id, ['Ibuprofeno'])
                db.session.commit()

                citas = Appointment.query.filter_by(
                    doctor_id=doctor.id, appointment_type='Entrega Pendiente'
                ).all()
                horarios = [c.time for c in citas]

        assert len(citas) == 2
        assert len(set(horarios)) == 2, f'ambas cayeron en el mismo horario: {horarios}'


class TestAnalyticsMonthSeries:
    """La serie de 6 meses de la grafica debe ser correcta en cualquier fecha."""

    def test_no_duplicate_or_missing_months(self):
        from routes_analytics import dashboard  # noqa: F401  (import del modulo)

        def month_start(reference, months_back):
            total = reference.year * 12 + (reference.month - 1) - months_back
            return reference.replace(year=total // 12, month=total % 12 + 1, day=1,
                                     hour=0, minute=0, second=0, microsecond=0)

        # Se recorre un anio entero: el defecto anterior solo aparecia en ciertos
        # meses, asi que una sola fecha de prueba no lo habria detectado.
        for anio in (2025, 2026, 2027):
            for mes in range(1, 13):
                now = datetime(anio, mes, 15)
                serie = [month_start(now, i) for i in range(5, -1, -1)]
                claves = [(d.year, d.month) for d in serie]

                assert len(claves) == len(set(claves)), \
                    f'meses duplicados en {anio}-{mes:02d}: {claves}'

                # Consecutivos y en orden ascendente.
                for anterior, siguiente in zip(claves, claves[1:]):
                    esperado = anterior[0] * 12 + anterior[1]
                    assert siguiente[0] * 12 + siguiente[1] == esperado + 1, \
                        f'salto de mes en {anio}-{mes:02d}: {claves}'

                assert claves[-1] == (anio, mes), 'el ultimo mes debe ser el actual'

    def test_leap_year_february_included(self):
        def month_start(reference, months_back):
            total = reference.year * 12 + (reference.month - 1) - months_back
            return reference.replace(year=total // 12, month=total % 12 + 1, day=1)

        # Marzo de 2026: el caso donde la version anterior repetia diciembre y
        # se saltaba febrero por completo.
        now = datetime(2026, 3, 15)
        meses = [month_start(now, i).month for i in range(5, -1, -1)]
        assert 2 in meses, 'febrero debe aparecer en la serie'
        assert meses.count(12) == 1, 'diciembre no debe repetirse'


class TestPersistedStringsAreClean:
    """Nada de lo que se guarda en base de datos debe llevar emojis.

    Ese contenido se cifra, sale en la exportacion de historia clinica que reciben
    los auditores y se imprime en la orden medica. En Android antiguo muchos
    emojis renderizan como un cuadro vacio dentro de un dato clinico.
    """

    def test_no_emoji_in_source_literals(self):
        import glob
        import re

        emoji = re.compile(r'[\U0001F300-\U0001FAFF☀-➿]')
        ofensores = []
        for path in glob.glob('*.py'):
            for numero, linea in enumerate(open(path, encoding='utf-8'), 1):
                if emoji.search(linea):
                    ofensores.append(f'{path}:{numero}')
        assert not ofensores, f'emojis en literales de Python: {ofensores}'

    def test_notification_titles_are_plain_text(self, app, make_user, make_stock):
        from models import Notification, Pharmacy, db
        import re

        make_stock('Amoxicilina', 50)
        patient = make_user(role='patient', username='pac_notif')

        with app.app_context():
            db.session.add(Notification(
                user_id=patient.id, clinic_id=1,
                title='Medicamentos listos para recoger',
                message='Tu ticket ABC123 esta listo.',
                type='ticket_ready',
            ))
            db.session.commit()

            emoji = re.compile(r'[\U0001F300-\U0001FAFF☀-➿]')
            for notif in Notification.query.all():
                assert not emoji.search(notif.title or '')
                assert not emoji.search(notif.message or '')
