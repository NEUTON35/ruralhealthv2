# -*- coding: utf-8 -*-
"""Adenda de la historia clinica (Resolucion 1995 de 1999).

La historia clinica no se corrige borrando: se corrige por adenda, que deja
intacto lo anterior. Que un profesional no pueda editar lo escrito es correcto;
que no pueda enmendarlo, no. Un diagnostico equivocado se queda ahi, y ademas
ya viajo al IHCE.
"""

from datetime import date

import pytest

from time_utils import colombia_now


@pytest.fixture
def consulta(app, make_user):
    from models import Chat, Clinic, MedicalHistory, db as _db

    paciente = make_user(role='patient', birth_date=date(1990, 1, 1), sex='F')
    doctor = make_user(role='doctor', medical_registration='RM-5')

    with app.app_context():
        _db.session.get(Clinic, 1).habilitacion_code = '0500166666'
        historia = MedicalHistory(
            clinic_id=1, patient_id=paciente.id, doctor_id=doctor.id,
            record_type='consultation', summary='Cuadro respiratorio',
            diagnosis='Rinofaringitis', cie10_code='Z000', cups_code='890201',
            external_cause='26', consultation_purpose='15', care_modality='01',
            created_at=colombia_now())
        _db.session.add(historia)
        _db.session.commit()
        return {'patient': paciente, 'doctor': doctor, 'history_id': historia.id}


class TestAdenda:

    def test_la_adenda_no_altera_el_registro_original(self, app, client, login, consulta):
        from models import MedicalHistory, db as _db
        login(consulta['doctor'].username)

        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': 'El diagnostico se digito por error; corresponde a otro codigo',
            'cie10_code': 'A00',
            'diagnosis': 'Colera',
        }, follow_redirects=True)

        with app.app_context():
            original = _db.session.get(MedicalHistory, consulta['history_id'])
            assert original.cie10_code == 'Z000', 'se altero el registro original'
            assert original.diagnosis == 'Rinofaringitis'

    def test_la_adenda_queda_vinculada_al_original(self, app, client, login, consulta):
        from models import MedicalHistory, db as _db
        login(consulta['doctor'].username)

        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': 'El diagnostico se digito por error; corresponde a otro codigo',
            'cie10_code': 'A00',
        }, follow_redirects=True)

        with app.app_context():
            adenda = MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).first()
            assert adenda is not None
            assert adenda.is_amendment
            assert adenda.record_type == 'adenda'
            assert adenda.cie10_code == 'A00'

    def test_la_adenda_conserva_el_motivo(self, app, client, login, consulta):
        from models import MedicalHistory
        login(consulta['doctor'].username)
        motivo = 'El codigo se digito por error de transcripcion en la consulta'

        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': motivo,
        }, follow_redirects=True)

        with app.app_context():
            adenda = MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).first()
            assert adenda.amendment_reason == motivo

    def test_sin_motivo_no_se_registra(self, app, client, login, consulta):
        """Una correccion sin explicacion no es una adenda, es un cambio a ciegas."""
        from models import MedicalHistory
        login(consulta['doctor'].username)

        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': 'error',
        }, follow_redirects=True)

        with app.app_context():
            assert MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).count() == 0

    def test_los_campos_no_corregidos_se_heredan(self, app, client, login, consulta):
        """Solo se cambia lo que se corrige; el resto viene del original."""
        from models import MedicalHistory
        login(consulta['doctor'].username)

        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': 'Se corrige unicamente el resumen de la consulta',
            'summary': 'Cuadro respiratorio con fiebre de tres dias',
        }, follow_redirects=True)

        with app.app_context():
            adenda = MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).first()
            assert adenda.cie10_code == 'Z000'
            assert adenda.external_cause == '26'
            assert adenda.care_modality == '01'

    def test_no_se_enmienda_una_adenda(self, app, client, login, consulta):
        """La cadena se enmienda desde el original, no en cascada."""
        from models import MedicalHistory
        login(consulta['doctor'].username)

        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': 'Primera correccion del registro por error de codigo',
        }, follow_redirects=True)

        with app.app_context():
            adenda = MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).first()
            adenda_id = adenda.id

        client.post(f'/doctor/historia/{adenda_id}/adenda', data={
            'amendment_reason': 'Intento de enmendar una adenda ya registrada',
        }, follow_redirects=True)

        with app.app_context():
            assert MedicalHistory.query.filter_by(amends_id=adenda_id).count() == 0

    def test_un_codigo_invalido_se_rechaza(self, app, client, login, consulta):
        from models import MedicalHistory
        login(consulta['doctor'].username)

        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': 'Se intenta corregir con un codigo que no existe',
            'cie10_code': 'NOEXISTE',
        }, follow_redirects=True)

        with app.app_context():
            assert MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).count() == 0

    def test_la_adenda_se_remite_al_ihce(self, app, client, login, consulta):
        """El IHCE recibio la version anterior; tiene que recibir la corregida."""
        from models import MedicalHistory, RDASubmission
        login(consulta['doctor'].username)

        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': 'El diagnostico se digito por error de transcripcion',
            'cie10_code': 'A00',
        }, follow_redirects=True)

        with app.app_context():
            adenda = MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).first()
            assert RDASubmission.query.filter_by(
                medical_history_id=adenda.id).first() is not None

    def test_una_correccion_puede_volver_el_caso_notificable(
            self, app, client, login, consulta):
        """Si el diagnostico corregido es un evento de interes, hay que avisar."""
        from models import MedicalHistory, NotifiableEvent, SivigilaNotification, db as _db
        with app.app_context():
            _db.session.add(NotifiableEvent(
                code='998', name='Evento de prueba', periodicity='inmediata',
                cie10_prefixes='A00'))
            _db.session.commit()

        login(consulta['doctor'].username)
        client.post(f"/doctor/historia/{consulta['history_id']}/adenda", data={
            'amendment_reason': 'El diagnostico correcto corresponde a otro codigo',
            'cie10_code': 'A00',
        }, follow_redirects=True)

        with app.app_context():
            adenda = MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).first()
            assert SivigilaNotification.query.filter_by(
                medical_history_id=adenda.id).first() is not None

    def test_otro_profesional_de_otra_clinica_no_puede_enmendar(
            self, app, client, login, consulta, make_user):
        from models import Clinic, MedicalHistory, db as _db
        with app.app_context():
            otra = Clinic(name='Otra', legal_name='Otra ESE', status='active',
                          activa=True)
            _db.session.add(otra)
            _db.session.commit()
            otra_id = otra.id

        intruso = make_user(role='doctor', clinic_id=otra_id,
                            medical_registration='RM-X')
        login(intruso.username)
        respuesta = client.post(
            f"/doctor/historia/{consulta['history_id']}/adenda",
            data={'amendment_reason': 'Intento de enmienda desde otra clinica'},
            follow_redirects=False)
        assert respuesta.status_code in (302, 403, 404)

        with app.app_context():
            assert MedicalHistory.query.filter_by(
                amends_id=consulta['history_id']).count() == 0
