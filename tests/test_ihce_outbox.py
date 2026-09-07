# -*- coding: utf-8 -*-
"""Cola de envio de los RDA, contra la base de datos real.

Lo que se comprueba aqui es sobre todo el comportamiento cuando las cosas van
mal, que es lo que decide si el sistema sirve en una zona rural: que la caida
del Ministerio no impida atender, que un fallo de red se reintente y uno de
estructura no, y que nunca se remita dos veces la misma atencion.
"""

import json
from datetime import date, datetime, timedelta

import pytest

from ihce import outbox
from ihce.client import DuplicadoError, IHCEError
from ihce.config import IHCEConfig


@pytest.fixture
def atencion(app, make_user):
    """Una atencion completa, con todos los datos que exige el perfil."""
    from models import Clinic, MedicalHistory, db as _db
    from time_utils import colombia_now

    paciente = make_user(role='patient', birth_date=date(1990, 5, 2), sex='F',
                         first_name='Ana', first_surname='Lopez',
                         municipality_code='05001', department_code='05',
                         zone='R', nationality_code='170',
                         ethnic_group='99', disability='08')
    doctor = make_user(role='doctor', medical_registration='RM-777',
                       first_name='Luis', first_surname='Mora')

    with app.app_context():
        clinica = _db.session.get(Clinic, 1)
        clinica.habilitacion_code = '0500199999'
        historia = MedicalHistory(
            clinic_id=1, patient_id=paciente.id, doctor_id=doctor.id,
            record_type='consultation', summary='Cuadro gripal',
            diagnosis='Rinofaringitis', cie10_code='J00X',
            created_at=colombia_now())
        _db.session.add(historia)
        _db.session.commit()
        return historia.id


def _config():
    return IHCEConfig.from_env({
        'IHCE_TENANT_ID': 't', 'IHCE_CLIENT_ID': 'c',
        'IHCE_CLIENT_SECRET': 's', 'IHCE_SUBSCRIPTION_KEY': 'k',
        'IHCE_BASE_URL': 'https://api.ejemplo/ihce',
        'IHCE_SCOPE': 'sc', 'IHCE_HABILITACION': '0500199999',
    })


class ClienteFalso:
    """Cliente que responde lo que se le indique, sin tocar la red."""

    def __init__(self, resultado=None):
        self.resultado = resultado if resultado is not None else {'id': 'RDA-1'}
        self.enviados = []

    def enviar_rda(self, bundle, operacion=None):
        self.enviados.append(bundle)
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


class TestEncolar:

    def test_encolar_crea_un_pendiente(self, app, atencion):
        from models import MedicalHistory, RDASubmission, RDA_PENDIENTE, db as _db
        with app.app_context():
            historia = _db.session.get(MedicalHistory, atencion)
            outbox.encolar(_db, historia, commit=True)
            envio = RDASubmission.query.filter_by(medical_history_id=atencion).one()
            assert envio.status == RDA_PENDIENTE
            assert envio.attempts == 0

    def test_encolar_dos_veces_no_duplica(self, app, atencion):
        """Una atencion se remite una sola vez, aunque se guarde dos veces."""
        from models import MedicalHistory, RDASubmission, db as _db
        with app.app_context():
            historia = _db.session.get(MedicalHistory, atencion)
            outbox.encolar(_db, historia, commit=True)
            outbox.encolar(_db, historia, commit=True)
            assert RDASubmission.query.filter_by(
                medical_history_id=atencion).count() == 1

    def test_la_base_impide_el_duplicado_aunque_falle_el_codigo(self, app, atencion):
        """No se confia solo en la comprobacion previa: hay indice unico."""
        from sqlalchemy.exc import IntegrityError
        from models import RDASubmission, db as _db
        with app.app_context():
            _db.session.add(RDASubmission(medical_history_id=atencion,
                                          patient_id=1, clinic_id=1))
            _db.session.commit()
            _db.session.add(RDASubmission(medical_history_id=atencion,
                                          patient_id=1, clinic_id=1))
            with pytest.raises(IntegrityError):
                _db.session.commit()
            _db.session.rollback()


class TestTransmision:

    def _envio(self, app, atencion):
        from models import MedicalHistory, db as _db
        historia = _db.session.get(MedicalHistory, atencion)
        return outbox.encolar(_db, historia, commit=True)

    def test_un_envio_aceptado_guarda_el_acuse(self, app, atencion):
        from models import RDA_ACEPTADO, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            cliente = ClienteFalso({'id': 'RDA-ABC-123'})
            estado = outbox.procesar_envio(_db, envio, cliente)
            _db.session.commit()
            assert estado == RDA_ACEPTADO
            assert envio.remote_id == 'RDA-ABC-123'
            assert envio.sent_at is not None
            assert envio.bundle_hash  # queda la huella de lo transmitido

    def test_el_duplicado_cuenta_como_cumplido(self, app, atencion):
        """409 significa que el Ministerio ya lo tenia. El deber esta cumplido."""
        from models import RDA_DUPLICADO, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            cliente = ClienteFalso(DuplicadoError('ya existe', detalle='repetido'))
            estado = outbox.procesar_envio(_db, envio, cliente)
            _db.session.commit()
            assert estado == RDA_DUPLICADO
            assert envio.next_attempt_at is None  # no se reintenta

    def test_un_fallo_de_red_se_reprograma(self, app, atencion):
        from models import RDA_PENDIENTE, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            cliente = ClienteFalso(IHCEError('sin conexion', permanente=False))
            estado = outbox.procesar_envio(_db, envio, cliente)
            _db.session.commit()
            assert estado == RDA_PENDIENTE
            assert envio.attempts == 1
            assert envio.next_attempt_at is not None

    def test_la_espera_entre_reintentos_crece(self, app, atencion):
        from models import db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            cliente = ClienteFalso(IHCEError('sin conexion', permanente=False))
            esperas = []
            for _ in range(3):
                outbox.procesar_envio(_db, envio, cliente)
                esperas.append(envio.next_attempt_at)
            assert esperas[1] > esperas[0]
            assert esperas[2] > esperas[1]

    def test_un_fallo_de_estructura_no_se_reintenta(self, app, atencion):
        """Reintentar un 400 da 400 otra vez. Solo gasta red."""
        from models import RDA_RECHAZADO, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            cliente = ClienteFalso(IHCEError('estructura invalida', codigo=400,
                                             permanente=True, detalle='campo X'))
            estado = outbox.procesar_envio(_db, envio, cliente)
            _db.session.commit()
            assert estado == RDA_RECHAZADO
            assert envio.next_attempt_at is None
            assert 'campo X' in envio.last_error

    def test_los_reintentos_tienen_techo(self, app, atencion):
        """Un envio con cien fallos no se arregla con el ciento uno."""
        from models import RDA_RECHAZADO, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            cliente = ClienteFalso(IHCEError('caido', permanente=False))
            for _ in range(outbox.MAX_INTENTOS + 1):
                estado = outbox.procesar_envio(_db, envio, cliente)
            _db.session.commit()
            assert estado == RDA_RECHAZADO
            assert 'agotaron' in envio.last_error

    def test_sin_diagnostico_queda_bloqueado_y_dice_por_que(self, app, atencion):
        from models import MedicalHistory, RDA_BLOQUEADO, db as _db
        with app.app_context():
            historia = _db.session.get(MedicalHistory, atencion)
            historia.cie10_code = None
            _db.session.commit()
            envio = self._envio(app, atencion)
            cliente = ClienteFalso()
            estado = outbox.procesar_envio(_db, envio, cliente)
            _db.session.commit()
            assert estado == RDA_BLOQUEADO
            assert 'CIE-10' in envio.last_error
            assert cliente.enviados == []  # no se gasto la llamada

    def test_sin_registro_medico_queda_bloqueado(self, app, atencion):
        from models import MedicalHistory, RDA_BLOQUEADO, db as _db
        with app.app_context():
            historia = _db.session.get(MedicalHistory, atencion)
            historia.doctor.medical_registration = None
            _db.session.commit()
            envio = self._envio(app, atencion)
            estado = outbox.procesar_envio(_db, envio, ClienteFalso())
            _db.session.commit()
            assert estado == RDA_BLOQUEADO
            assert 'RETHUS' in envio.last_error


class TestProcesarPendientes:

    def test_sin_credenciales_no_transmite_pero_no_pierde_nada(self, app, atencion):
        """Los RDA se acumulan hasta que haya credenciales. No se descartan."""
        from models import MedicalHistory, RDASubmission, RDA_PENDIENTE, db as _db
        with app.app_context():
            outbox.encolar(_db, _db.session.get(MedicalHistory, atencion), commit=True)
            resumen = outbox.procesar_pendientes(_db, IHCEConfig.from_env({}))
            assert resumen['procesados'] == 0
            assert 'motivo' in resumen
            envio = RDASubmission.query.filter_by(medical_history_id=atencion).one()
            assert envio.status == RDA_PENDIENTE

    def test_no_toca_los_que_aun_no_toca_reintentar(self, app, atencion):
        from models import MedicalHistory, RDASubmission, db as _db
        from time_utils import colombia_now
        with app.app_context():
            envio = outbox.encolar(_db, _db.session.get(MedicalHistory, atencion),
                                   commit=True)
            envio.next_attempt_at = colombia_now() + timedelta(hours=2)
            _db.session.commit()
            cliente = ClienteFalso()
            resumen = outbox.procesar_pendientes(_db, _config(), cliente=cliente)
            assert resumen['procesados'] == 0
            assert cliente.enviados == []

    def test_resumen_por_estado(self, app, atencion):
        from models import MedicalHistory, db as _db
        with app.app_context():
            outbox.encolar(_db, _db.session.get(MedicalHistory, atencion), commit=True)
            conteo = outbox.resumen_estado(_db)
            assert conteo.get('pendiente') == 1


class TestIntegracionConLaAtencion:

    def test_guardar_una_historia_encola_su_rda(self, app, client, login, make_user):
        """La obligacion no depende de que alguien se acuerde de encolar."""
        from models import (Chat, Clinic, MedicalHistory, RDASubmission,
                            db as _db)

        paciente = make_user(role='patient', birth_date=date(1990, 1, 1), sex='M')
        doctor = make_user(role='doctor', medical_registration='RM-1')

        with app.app_context():
            _db.session.get(Clinic, 1).habilitacion_code = '0500188888'
            chat = Chat(clinic_id=1, patient_id=paciente.id,
                        doctor_id=doctor.id, status='open')
            _db.session.add(chat)
            _db.session.commit()
            chat_id = chat.id

        login(doctor.username)
        respuesta = client.post(f'/doctor/chat/{chat_id}', data={
            'action': 'save_history',
            'summary': 'Consulta de control',
            'diagnosis': 'Examen medico general',
            'cie10_code': 'Z000',
            'cups_code': '890201',
            'treatment': 'Continuar tratamiento',
        }, follow_redirects=False)
        assert respuesta.status_code in (302, 303)

        with app.app_context():
            historia = MedicalHistory.query.filter_by(patient_id=paciente.id).first()
            assert historia is not None, 'no se guardo la historia'
            envio = RDASubmission.query.filter_by(
                medical_history_id=historia.id).first()
            assert envio is not None, (
                'la atencion se guardo sin encolar su RDA: incumple la '
                'Resolucion 1888 de 2025')


class TestAuditoria:
    """Articulo 6.5 del manual: registro inmutable de cada operacion."""

    def _envio(self, app, atencion):
        from models import MedicalHistory, db as _db
        historia = _db.session.get(MedicalHistory, atencion)
        return outbox.encolar(_db, historia, commit=True)

    def test_un_envio_aceptado_deja_entrada_en_la_auditoria(self, app, atencion):
        from models import AuditLog, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            outbox.procesar_envio(_db, envio, ClienteFalso({'id': 'RDA-7'}))
            _db.session.commit()
            entrada = AuditLog.query.filter_by(event='rda_enviado').first()
            assert entrada is not None
            assert 'RDA-7' in entrada.details
            assert str(atencion) in entrada.details

    def test_el_rechazo_tambien_queda_registrado(self, app, atencion):
        """El manual pide el estado de la transaccion: exito, error o rechazo."""
        from models import AuditLog, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            outbox.procesar_envio(_db, envio, ClienteFalso(
                IHCEError('mal', codigo=400, permanente=True)))
            _db.session.commit()
            assert AuditLog.query.filter_by(event='rda_rechazado').first() is not None

    def test_la_auditoria_no_guarda_datos_clinicos(self, app, atencion):
        from models import AuditLog, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)
            outbox.procesar_envio(_db, envio, ClienteFalso({'id': 'RDA-7'}))
            _db.session.commit()
            entrada = AuditLog.query.filter_by(event='rda_enviado').first()
            for prohibido in ('J00X', 'Rinofaringitis', 'Cuadro gripal'):
                assert prohibido not in (entrada.details or '')

    def test_un_fallo_al_auditar_no_deshace_el_envio(self, app, atencion, monkeypatch):
        """El envio ya ocurrio: no se puede fingir que no."""
        from models import RDA_ACEPTADO, db as _db
        with app.app_context():
            envio = self._envio(app, atencion)

            def revienta(*a, **k):
                raise RuntimeError('auditoria caida')

            monkeypatch.setattr('security.audit', revienta)
            estado = outbox.procesar_envio(_db, envio, ClienteFalso({'id': 'X'}))
            assert estado == RDA_ACEPTADO
