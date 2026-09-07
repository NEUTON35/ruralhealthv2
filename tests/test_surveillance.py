# -*- coding: utf-8 -*-
"""Vigilancia en salud publica (Decreto 3518 de 2006).

Lo que se comprueba es que un caso notificable no pueda pasar inadvertido, y
que cerrarlo exija dejar constancia. La radicacion ocurre en el sistema del
INS; aqui se verifica la deteccion y el rastro.
"""

from datetime import date, timedelta

import pytest

import surveillance
from time_utils import colombia_now


@pytest.fixture
def atencion(app, make_user):
    """Crea una atencion y devuelve una funcion para fijarle el diagnostico."""
    from models import MedicalHistory, db as _db

    paciente = make_user(role='patient', birth_date=date(1995, 2, 2), sex='F')
    doctor = make_user(role='doctor', medical_registration='RM-1')

    def _crear(cie10):
        with app.app_context():
            historia = MedicalHistory(
                clinic_id=1, patient_id=paciente.id, doctor_id=doctor.id,
                record_type='consultation', summary='Consulta',
                diagnosis='Diagnostico', cie10_code=cie10, cups_code='890201',
                external_cause='26', consultation_purpose='15',
                care_modality='01', created_at=colombia_now())
            _db.session.add(historia)
            _db.session.commit()
            return historia.id
    _crear.doctor_id = doctor.id
    return _crear


class TestDeteccion:

    def test_un_diagnostico_de_dengue_abre_un_pendiente(self, app, atencion):
        from models import SIVIGILA_PENDIENTE, SivigilaNotification, db as _db
        hid = atencion('A90')
        with app.app_context():
            from models import MedicalHistory
            eventos = surveillance.detectar(
                _db, _db.session.get(MedicalHistory, hid), commit=True)
            assert eventos, 'no se detecto el dengue'
            aviso = SivigilaNotification.query.filter_by(
                medical_history_id=hid).first()
            assert aviso.status == SIVIGILA_PENDIENTE
            assert aviso.due_at is not None

    def test_la_notificacion_inmediata_vence_en_24_horas(self, app, atencion):
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion('T74')          # violencia intrafamiliar
        with app.app_context():
            surveillance.detectar(_db, _db.session.get(MedicalHistory, hid), commit=True)
            aviso = SivigilaNotification.query.filter_by(medical_history_id=hid).first()
            assert aviso.periodicity == 'inmediata'
            margen = aviso.due_at - aviso.detected_at
            assert margen <= timedelta(hours=24)

    def test_la_semanal_admite_la_semana_epidemiologica(self, app, atencion):
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion('A90')
        with app.app_context():
            surveillance.detectar(_db, _db.session.get(MedicalHistory, hid), commit=True)
            aviso = SivigilaNotification.query.filter_by(medical_history_id=hid).first()
            assert aviso.periodicity == 'semanal'
            assert aviso.due_at - aviso.detected_at == timedelta(days=7)

    def test_un_diagnostico_corriente_no_abre_nada(self, app, atencion):
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion('Z000')         # examen medico general
        with app.app_context():
            surveillance.detectar(_db, _db.session.get(MedicalHistory, hid), commit=True)
            assert SivigilaNotification.query.filter_by(
                medical_history_id=hid).count() == 0

    def test_no_duplica_el_pendiente(self, app, atencion):
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion('A90')
        with app.app_context():
            historia = _db.session.get(MedicalHistory, hid)
            surveillance.detectar(_db, historia, commit=True)
            surveillance.detectar(_db, historia, commit=True)
            assert SivigilaNotification.query.filter_by(
                medical_history_id=hid).count() == 1

    def test_detecta_intento_de_suicidio(self, app, atencion):
        """Notificacion inmediata. Es de los que no puede pasar de largo."""
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion('X708')
        with app.app_context():
            surveillance.detectar(_db, _db.session.get(MedicalHistory, hid), commit=True)
            aviso = SivigilaNotification.query.filter_by(medical_history_id=hid).first()
            assert aviso is not None
            assert aviso.periodicity == 'inmediata'

    def test_detecta_desnutricion_aguda(self, app, atencion):
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion('E440')
        with app.app_context():
            surveillance.detectar(_db, _db.session.get(MedicalHistory, hid), commit=True)
            assert SivigilaNotification.query.filter_by(
                medical_history_id=hid).first() is not None

    def test_la_deteccion_es_por_prefijo(self, app, atencion):
        """Un evento agrupa varios codigos: A90, A91.0, A91.9 son dengue."""
        assert surveillance._normalizar('A91.9') == 'A919'
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion('A919')
        with app.app_context():
            surveillance.detectar(_db, _db.session.get(MedicalHistory, hid), commit=True)
            assert SivigilaNotification.query.filter_by(
                medical_history_id=hid).first() is not None

    def test_sin_diagnostico_no_detecta_nada(self, app, atencion):
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion(None)
        with app.app_context():
            eventos = surveillance.detectar(
                _db, _db.session.get(MedicalHistory, hid), commit=True)
            assert eventos == []


class TestResolucion:

    def _pendiente(self, app, atencion, cie10='A90'):
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion(cie10)
        historia = _db.session.get(MedicalHistory, hid)
        surveillance.detectar(_db, historia, commit=True)
        return SivigilaNotification.query.filter_by(medical_history_id=hid).first()

    def test_marcar_notificada_exige_la_ficha(self, app, atencion):
        """Sin referencia de la ficha no hay constancia de nada."""
        from models import db as _db
        with app.app_context():
            aviso = self._pendiente(app, atencion)
            with pytest.raises(ValueError) as exc:
                surveillance.marcar_notificada(_db, aviso, atencion.doctor_id, '')
            assert 'ficha' in str(exc.value).lower()

    def test_marcar_notificada_guarda_quien_y_cuando(self, app, atencion):
        from models import SIVIGILA_NOTIFICADA, db as _db
        with app.app_context():
            aviso = self._pendiente(app, atencion)
            surveillance.marcar_notificada(_db, aviso, atencion.doctor_id, 'FICHA-2026-88')
            assert aviso.status == SIVIGILA_NOTIFICADA
            assert aviso.ficha_reference == 'FICHA-2026-88'
            assert aviso.notified_at is not None
            assert aviso.notified_by_id == atencion.doctor_id

    def test_descartar_exige_justificacion(self, app, atencion):
        """La deteccion es amplia a proposito; descartar sin motivo convertiria
        la lista en algo que se vacia por incomodidad."""
        from models import db as _db
        with app.app_context():
            aviso = self._pendiente(app, atencion)
            with pytest.raises(ValueError):
                surveillance.descartar(_db, aviso, atencion.doctor_id, 'no')

    def test_descartar_con_motivo_deja_el_motivo(self, app, atencion):
        from models import SIVIGILA_DESCARTADA, db as _db
        with app.app_context():
            aviso = self._pendiente(app, atencion)
            motivo = 'Codigo registrado por error, el paciente consulto por otra causa'
            surveillance.descartar(_db, aviso, atencion.doctor_id, motivo)
            assert aviso.status == SIVIGILA_DESCARTADA
            assert aviso.resolution_note == motivo


class TestPendientesYVencimiento:

    def test_un_pendiente_vencido_se_marca_como_tal(self, app, atencion):
        from models import MedicalHistory, SivigilaNotification, db as _db
        hid = atencion('T74')
        with app.app_context():
            surveillance.detectar(_db, _db.session.get(MedicalHistory, hid), commit=True)
            aviso = SivigilaNotification.query.filter_by(medical_history_id=hid).first()
            aviso.due_at = colombia_now() - timedelta(hours=1)
            _db.session.commit()
            assert aviso.is_overdue
            assert aviso in surveillance.pendientes(solo_vencidos=True)

    def test_los_pendientes_salen_por_urgencia(self, app, atencion):
        from models import MedicalHistory, db as _db
        with app.app_context():
            for codigo in ('A90', 'T74'):     # semanal y luego inmediata
                surveillance.detectar(
                    _db, _db.session.get(MedicalHistory, atencion(codigo)), commit=True)
            lista = surveillance.pendientes()
            assert len(lista) == 2
            # La inmediata vence antes, asi que va primero.
            assert lista[0].periodicity == 'inmediata'

    def test_resumen_por_estado(self, app, atencion):
        from models import MedicalHistory, db as _db
        with app.app_context():
            surveillance.detectar(
                _db, _db.session.get(MedicalHistory, atencion('A90')), commit=True)
            assert surveillance.resumen()['pendientes'] == 1


class TestIntegracionConLaAtencion:

    def test_guardar_una_atencion_notificable_abre_el_pendiente(
            self, app, client, login, make_user):
        """La obligacion no puede depender de que el profesional se acuerde."""
        from models import (Chat, Clinic, MedicalHistory, SivigilaNotification,
                            db as _db)

        paciente = make_user(role='patient', birth_date=date(1990, 1, 1), sex='M')
        doctor = make_user(role='doctor', medical_registration='RM-9')

        with app.app_context():
            _db.session.get(Clinic, 1).habilitacion_code = '0500177777'
            # El catalogo semilla no trae Z000 como notificable; se anade un
            # evento que si case con el diagnostico de prueba disponible.
            from models import NotifiableEvent
            _db.session.add(NotifiableEvent(
                code='999', name='Evento de prueba', periodicity='inmediata',
                cie10_prefixes='Z000'))
            chat = Chat(clinic_id=1, patient_id=paciente.id,
                        doctor_id=doctor.id, status='open')
            _db.session.add(chat)
            _db.session.commit()
            chat_id = chat.id

        login(doctor.username)
        respuesta = client.post(f'/doctor/chat/{chat_id}', data={
            'action': 'save_history',
            'summary': 'Consulta', 'diagnosis': 'Examen medico general',
            'cie10_code': 'Z000', 'cups_code': '890201',
            'treatment': 'Ninguno',
            'external_cause': '26', 'consultation_purpose': '15',
            'care_modality': '01',
        }, follow_redirects=False)
        assert respuesta.status_code in (302, 303)

        with app.app_context():
            historia = MedicalHistory.query.filter_by(patient_id=paciente.id).first()
            assert historia is not None
            aviso = SivigilaNotification.query.filter_by(
                medical_history_id=historia.id).first()
            assert aviso is not None, (
                'la atencion se guardo sin abrir el pendiente de notificacion: '
                'incumple el Decreto 3518 de 2006')
