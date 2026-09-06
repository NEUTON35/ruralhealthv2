"""Pruebas de aislamiento entre clínicas y de control de acceso.

Una fuga entre sedes expone la historia clínica de pacientes de una institución a
personal de otra. El ámbito por `clinic_id` aplicado en la capa ORM es una red de
seguridad; estas pruebas comprueban que además cada endpoint verifica la
pertenencia de forma explícita, porque el ámbito depende de la sesión y hay
caminos legítimos que lo desactivan.
"""

import pytest

from conftest import VALID_PASSWORD


@pytest.fixture
def two_clinics(app):
    from models import Clinic, Pharmacy, db

    with app.app_context():
        second = Clinic(name='Clinica Norte', legal_name='Clinica Norte SAS', status='active')
        db.session.add(second)
        db.session.flush()
        db.session.add(Pharmacy(clinic_id=second.id, name='Farmacia Norte', is_active=True))
        db.session.commit()
        return {'a': 1, 'b': second.id}


class TestCrossClinicAccess:

    def test_doctor_cannot_open_patient_of_another_clinic(
            self, app, two_clinics, make_user, client):
        """Un médico de la clínica A no puede abrir la historia de la clínica B."""
        make_user(role='doctor', username='doc_a', clinic_id=two_clinics['a'],
                  medical_registration='RM-A')
        patient_b = make_user(role='patient', username='pac_b', clinic_id=two_clinics['b'])

        client.post('/login', data={'username': 'doc_a', 'password': VALID_PASSWORD})
        response = client.get(f'/doctor/patient_history/{patient_b.id}')
        assert response.status_code == 404

    def test_doctor_cannot_prescribe_to_another_clinic_patient(
            self, app, two_clinics, make_user, client):
        make_user(role='doctor', username='doc_a2', clinic_id=two_clinics['a'],
                  medical_registration='RM-A2')
        patient_b = make_user(role='patient', username='pac_b2', clinic_id=two_clinics['b'])

        client.post('/login', data={'username': 'doc_a2', 'password': VALID_PASSWORD})
        response = client.get(f'/doctor/prescription/{patient_b.id}')
        assert response.status_code == 404

    def test_patient_cannot_open_another_patients_chat(self, app, make_user, client):
        from models import Chat, db

        doctor = make_user(role='doctor', username='doc_chat', medical_registration='RM-C')
        owner = make_user(role='patient', username='pac_duenio')
        intruder = make_user(role='patient', username='pac_intruso')

        with app.app_context():
            chat = Chat(clinic_id=1, patient_id=owner.id, doctor_id=doctor.id, status='open')
            db.session.add(chat)
            db.session.commit()
            chat_id = chat.id

        client.post('/login', data={'username': 'pac_intruso', 'password': VALID_PASSWORD})
        response = client.get(f'/patient/chat/{chat_id}')
        # Se redirige al panel propio en lugar de mostrar la conversación ajena.
        assert response.status_code == 302
        assert '/patient/dashboard' in response.headers['Location']

    def test_allergy_of_another_clinic_cannot_be_discarded(
            self, app, two_clinics, make_user, client):
        from models import PatientAllergy, db

        patient_b = make_user(role='patient', username='pac_alergia_b',
                              clinic_id=two_clinics['b'])
        make_user(role='doctor', username='doc_alergia_a', clinic_id=two_clinics['a'],
                  medical_registration='RM-AL')

        with app.app_context():
            allergy = PatientAllergy(
                clinic_id=two_clinics['b'], patient_id=patient_b.id,
                substance='Penicilina', substance_normalized='penicilina',
                severity='grave', status='confirmada',
            )
            db.session.add(allergy)
            db.session.commit()
            allergy_id = allergy.id

        client.post('/login', data={'username': 'doc_alergia_a', 'password': VALID_PASSWORD})
        response = client.post(f'/doctor/alergia/{allergy_id}/descartar',
                               data={'reason': 'Motivo suficientemente largo'})
        assert response.status_code == 404


class TestRoleEnforcement:

    @pytest.mark.parametrize('path', [
        '/admin/dashboard',
        '/admin/auditoria',
        '/superadmin/',
        '/staff/dashboard',
        '/expendedor/dashboard',
    ])
    def test_patient_cannot_reach_privileged_pages(self, app, make_user, client, path):
        make_user(role='patient', username='pac_rol')
        client.post('/login', data={'username': 'pac_rol', 'password': VALID_PASSWORD})
        assert client.get(path).status_code == 403

    def test_doctor_cannot_reach_admin_pages(self, app, make_user, client):
        make_user(role='doctor', username='doc_rol', medical_registration='RM-R')
        client.post('/login', data={'username': 'doc_rol', 'password': VALID_PASSWORD})
        assert client.get('/admin/dashboard').status_code == 403

    def test_clinic_admin_cannot_issue_reset_for_another_admin(
            self, app, make_user, client):
        """Sería una vía de escalada de privilegios dentro del propio sistema."""
        make_user(role='admin', username='admin_uno')
        other = make_user(role='admin', username='admin_dos')

        client.post('/login', data={'username': 'admin_uno', 'password': VALID_PASSWORD})
        response = client.post(f'/admin/emitir_reset/{other.id}')
        assert response.status_code == 403

    def test_unauthenticated_is_redirected_to_login(self, client):
        response = client.get('/patient/dashboard')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']


class TestUploadAccessControl:

    def test_unknown_file_returns_404_not_403(self, app, make_user, client):
        """Distinguir 403 de 404 permitiría enumerar qué archivos existen."""
        make_user(role='patient', username='pac_archivo')
        client.post('/login', data={'username': 'pac_archivo', 'password': VALID_PASSWORD})
        assert client.get('/uploads/inexistente.png').status_code == 404

    def test_path_traversal_rejected(self, app, make_user, client):
        make_user(role='patient', username='pac_traversal')
        client.post('/login', data={'username': 'pac_traversal', 'password': VALID_PASSWORD})
        for attempt in ('../.env', '..%2f.env', 'clinic_1/../../.env'):
            assert client.get(f'/uploads/{attempt}').status_code in (400, 404)

    def test_unauthenticated_cannot_read_uploads(self, client):
        assert client.get('/uploads/cualquier.png').status_code == 403


class TestPatientVisibility:

    def test_omnisearch_requires_authentication(self, client):
        response = client.get('/api/omnisearch?q=juan')
        assert response.status_code == 401

    def test_omnisearch_scoped_to_own_clinic(self, app, two_clinics, make_user, client):
        make_user(role='doctor', username='doc_busca', clinic_id=two_clinics['a'],
                  medical_registration='RM-B', name='Doctor Buscador')
        make_user(role='patient', username='pac_otra_clinica',
                  clinic_id=two_clinics['b'], name='Paciente Ajeno')

        client.post('/login', data={'username': 'doc_busca', 'password': VALID_PASSWORD})
        response = client.get('/api/omnisearch?q=Ajeno')
        titles = [item['title'] for item in response.get_json()['results']]
        assert 'Paciente Ajeno' not in titles

    def test_omnisearch_rejects_short_queries(self, app, make_user, client):
        make_user(role='patient', username='pac_corta')
        client.post('/login', data={'username': 'pac_corta', 'password': VALID_PASSWORD})
        assert client.get('/api/omnisearch?q=a').get_json()['results'] == []


class TestDeactivatedAccounts:

    def test_deactivated_account_cannot_log_in(self, app, make_user, client):
        make_user(role='patient', username='pac_inactivo', is_active_account=False)
        response = client.post('/login', data={
            'username': 'pac_inactivo', 'password': VALID_PASSWORD,
        })
        assert response.status_code == 403

    def test_deactivation_invalidates_existing_session(self, app, make_user, client):
        from models import User, db

        user = make_user(role='patient', username='pac_revocado')
        client.post('/login', data={'username': 'pac_revocado', 'password': VALID_PASSWORD})
        assert client.get('/patient/dashboard').status_code == 200

        with app.app_context():
            target = db.session.get(User, user.id)
            target.is_active_account = False
            db.session.commit()

        # La sesión abierta deja de servir de inmediato.
        response = client.get('/patient/dashboard')
        assert response.status_code == 302
        assert '/login' in response.headers['Location']
