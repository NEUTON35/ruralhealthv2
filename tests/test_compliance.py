"""Pruebas de cumplimiento: habeas data y RIPS.

Ambos bloques cubren obligaciones legales cuyo incumplimiento es sancionable, y
en el caso del RIPS supone además reportar información falsa al sistema de salud.
"""

from datetime import datetime, timedelta

import pytest

from conftest import VALID_PASSWORD


# =============================================================================
# Habeas data (Ley 1581 de 2012)
# =============================================================================

class TestDataExport:

    def test_patient_can_download_own_data(self, app, make_user, client):
        import io
        import zipfile

        make_user(role='patient', username='pac_export', name='Carlos Ruiz')
        client.post('/login', data={'username': 'pac_export', 'password': VALID_PASSWORD})

        response = client.post('/privacidad-datos/exportar')
        assert response.status_code == 200
        assert response.mimetype == 'application/zip'

        archive = zipfile.ZipFile(io.BytesIO(response.data))
        names = set(archive.namelist())
        for expected in ('perfil.json', 'citas.csv', 'historia_clinica.csv',
                         'alergias.csv', 'consentimientos.csv', 'LEEME.txt'):
            assert expected in names, f'falta {expected} en la exportacion'

    def test_export_is_recorded_as_a_request(self, app, make_user, client):
        from models import DataSubjectRequest

        make_user(role='patient', username='pac_export2')
        client.post('/login', data={'username': 'pac_export2', 'password': VALID_PASSWORD})
        client.post('/privacidad-datos/exportar')

        with app.app_context():
            entry = DataSubjectRequest.query.filter_by(request_type='acceso').one()
            assert entry.status == 'atendida'
            assert entry.resolved_at is not None

    def test_export_neutralizes_csv_formulas(self, app, make_user, client):
        """Un nombre que empiece por "=" no debe ejecutarse al abrir el archivo."""
        import io
        import zipfile
        from models import Appointment, User, db

        patient = make_user(role='patient', username='pac_formula',
                            name='=cmd|calc!A1')
        doctor = make_user(role='doctor', username='doc_formula',
                           medical_registration='RM-F')
        with app.app_context():
            db.session.add(Appointment(
                clinic_id=1, patient_id=patient.id, doctor_id=doctor.id,
                date='2026-09-01', time='10:00', status='attended',
            ))
            db.session.commit()

        client.post('/login', data={'username': 'pac_formula', 'password': VALID_PASSWORD})
        response = client.post('/privacidad-datos/exportar')

        archive = zipfile.ZipFile(io.BytesIO(response.data))
        content = archive.read('citas.csv').decode('utf-8')
        assert '=cmd' not in content or "'=cmd" in content


class TestConsentRevocation:

    def test_revocation_closes_clinical_access(self, app, make_user, client):
        from models import InformedConsentLog, User, db
        from time_utils import colombia_now

        user = make_user(role='patient', username='pac_revoca')
        with app.app_context():
            db.session.add(InformedConsentLog(
                patient_id=user.id, clinic_id=1, consent_type='sensitive_data',
                granted=True, digital_signature_hash='abc123',
                document_version='1.0', timestamp=colombia_now(),
            ))
            db.session.commit()

        client.post('/login', data={'username': 'pac_revoca', 'password': VALID_PASSWORD})
        client.post('/privacidad-datos/revocar-consentimiento',
                    data={'consent_type': 'sensitive_data'})

        with app.app_context():
            refreshed = db.session.get(User, user.id)
            assert refreshed.sensitive_data_consent_at is None

            # Queda constancia de la revocatoria, sin borrar la autorización previa.
            logs = InformedConsentLog.query.filter_by(patient_id=user.id).all()
            assert len(logs) == 2
            assert any(log.granted is False for log in logs)
            assert any(log.revoked_at is not None for log in logs)


class TestDeletionRequest:

    def test_deletion_request_is_registered_with_legal_deadline(self, app, make_user, client):
        from models import DataSubjectRequest

        make_user(role='patient', username='pac_supresion')
        client.post('/login', data={'username': 'pac_supresion', 'password': VALID_PASSWORD})
        client.post('/privacidad-datos/solicitar', data={
            'request_type': 'supresion',
            'detail': 'Solicito la eliminacion de todos mis datos personales.',
        })

        with app.app_context():
            entry = DataSubjectRequest.query.filter_by(request_type='supresion').one()
            assert entry.status == 'recibida'
            assert entry.due_at is not None, 'debe fijarse el plazo legal de respuesta'
            assert entry.resolved_at is None

    def test_deactivation_preserves_clinical_history(self, app, make_user, client):
        """La historia clínica NO se elimina: hay deber legal de conservarla 15 años."""
        from models import DataSubjectRequest, MedicalHistory, User, db
        from time_utils import colombia_now

        patient = make_user(role='patient', username='pac_borrado')
        doctor = make_user(role='doctor', username='doc_borrado',
                           medical_registration='RM-BD')
        make_user(role='admin', username='admin_borrado')

        with app.app_context():
            db.session.add(MedicalHistory(
                clinic_id=1, patient_id=patient.id, doctor_id=doctor.id,
                record_type='note', summary='Consulta por cefalea', cie10_code='Z000',
            ))
            entry = DataSubjectRequest(
                user_id=patient.id, clinic_id=1, request_type='supresion',
                status='recibida', requested_at=colombia_now(),
            )
            db.session.add(entry)
            db.session.commit()
            request_id = entry.id

        client.post('/login', data={'username': 'admin_borrado', 'password': VALID_PASSWORD})
        client.post(f'/privacidad-datos/solicitudes/{request_id}/desactivar-cuenta')

        with app.app_context():
            refreshed = db.session.get(User, patient.id)
            assert refreshed.is_active_account is False
            assert refreshed.deactivated_at is not None
            # El registro clínico permanece.
            assert MedicalHistory.query.filter_by(patient_id=patient.id).count() == 1

    def test_admin_must_record_the_answer_given(self, app, make_user, client):
        from models import DataSubjectRequest, db
        from time_utils import colombia_now

        patient = make_user(role='patient', username='pac_resp')
        make_user(role='admin', username='admin_resp')

        with app.app_context():
            entry = DataSubjectRequest(
                user_id=patient.id, clinic_id=1, request_type='rectificacion',
                status='recibida', requested_at=colombia_now(),
            )
            db.session.add(entry)
            db.session.commit()
            request_id = entry.id

        client.post('/login', data={'username': 'admin_resp', 'password': VALID_PASSWORD})
        # Sin respuesta escrita no se cierra.
        client.post(f'/privacidad-datos/solicitudes/{request_id}/resolver',
                    data={'resolution_note': 'ok', 'outcome': 'atendida'})

        with app.app_context():
            entry = db.session.get(DataSubjectRequest, request_id)
            assert entry.resolved_at is None


# =============================================================================
# RIPS
# =============================================================================

class TestRIPSValidation:

    @pytest.fixture
    def clinic_with_attention(self, app, make_user):
        from models import Clinic, MedicalHistory, User, db
        from time_utils import colombia_now
        from datetime import date

        doctor = make_user(role='doctor', username='doc_rips',
                           medical_registration='RM-RIPS')
        patient = make_user(role='patient', username='pac_rips', name='Ana Lucia Perez Gomez')

        with app.app_context():
            clinic = db.session.get(Clinic, 1)
            clinic.nit = '900123456'
            clinic.habilitacion_code = '05001234501'
            clinic.department_code = '05'
            clinic.municipality_code = '001'

            subject = db.session.get(User, patient.id)
            subject.document_type = 'CC'
            subject.first_surname = 'Perez'
            subject.second_surname = 'Gomez'
            subject.first_name = 'Ana'
            subject.second_name = 'Lucia'
            subject.birth_date = date(1990, 5, 15)
            subject.sex = 'F'
            subject.department_code = '05'
            subject.municipality_code = '001'
            subject.affiliation_regime = 'subsidiado'
            subject.insurer_code = 'EPS123'

            db.session.add(MedicalHistory(
                clinic_id=1, patient_id=patient.id, doctor_id=doctor.id,
                record_type='consulta', cie10_code='Z000', cups_code='890201',
                summary='Consulta de control', created_at=colombia_now(),
            ))
            db.session.commit()

        return {'doctor': doctor, 'patient': patient}

    def test_export_refuses_when_data_is_missing(self, app, make_user):
        """La versión anterior inventaba los datos que faltaban."""
        from rips_service import RIPSValidationError, generate_rips
        from models import MedicalHistory, db
        from time_utils import colombia_now

        doctor = make_user(role='doctor', username='doc_incompleto',
                           medical_registration='RM-INC')
        patient = make_user(role='patient', username='pac_incompleto')

        with app.app_context():
            db.session.add(MedicalHistory(
                clinic_id=1, patient_id=patient.id, doctor_id=doctor.id,
                record_type='consulta', created_at=colombia_now(),
            ))
            db.session.commit()

            start = colombia_now() - timedelta(days=1)
            end = colombia_now() + timedelta(days=1)

            with pytest.raises(RIPSValidationError) as error:
                generate_rips(1, start, end)

            fields = {issue['field'] for issue in error.value.issues}
            # Debe señalar exactamente qué falta.
            assert 'habilitacion_code' in fields
            assert 'cie10_code' in fields
            assert 'birth_date' in fields

    def test_export_succeeds_with_complete_data(self, app, clinic_with_attention):
        import io
        import zipfile
        from rips_service import generate_rips
        from time_utils import colombia_now

        with app.app_context():
            start = colombia_now() - timedelta(days=1)
            end = colombia_now() + timedelta(days=1)
            data = generate_rips(1, start, end, invoice_number='FE-2026-00001')

            archive = zipfile.ZipFile(io.BytesIO(data))
            names = set(archive.namelist())
            assert {'CT000001.txt', 'AF000001.txt', 'US000001.txt', 'AC000001.txt'} <= names

            users_file = archive.read('US000001.txt').decode('utf-8')
            # Los apellidos reales, no los literales inventados de antes.
            assert 'Perez' in users_file
            assert 'Apellido1' not in users_file
            assert 'Apellido2' not in users_file

    def test_export_uses_real_age_not_a_constant(self, app, clinic_with_attention):
        import io
        import zipfile
        from rips_service import generate_rips
        from time_utils import colombia_now

        with app.app_context():
            start = colombia_now() - timedelta(days=1)
            end = colombia_now() + timedelta(days=1)
            data = generate_rips(1, start, end, invoice_number='FE-1')

            archive = zipfile.ZipFile(io.BytesIO(data))
            row = archive.read('US000001.txt').decode('utf-8').strip().split(',')
            expected_age = colombia_now().year - 1990 - (
                (colombia_now().month, colombia_now().day) < (5, 15)
            )
            assert row[8] == str(expected_age)
            assert row[10] == 'F', 'el sexo debe ser el registrado, no una constante'

    def test_preview_reports_without_generating(self, app, clinic_with_attention):
        from rips_service import preview_validation
        from time_utils import colombia_now

        with app.app_context():
            result = preview_validation(
                1, colombia_now() - timedelta(days=1), colombia_now() + timedelta(days=1)
            )
            assert result['can_export'] is True
            assert result['total_records'] == 1
            assert result['total_patients'] == 1
            assert result['issues'] == []

    def test_doctor_without_registration_blocks_export(self, app, make_user):
        from rips_service import RIPSValidationError, generate_rips
        from models import Clinic, MedicalHistory, User, db
        from time_utils import colombia_now
        from datetime import date

        doctor = make_user(role='doctor', username='doc_sin_rm')
        patient = make_user(role='patient', username='pac_sin_rm')

        with app.app_context():
            clinic = db.session.get(Clinic, 1)
            clinic.nit = '900123456'
            clinic.habilitacion_code = '05001234501'

            subject = db.session.get(User, patient.id)
            subject.document_type = 'CC'
            subject.first_surname = 'Lopez'
            subject.first_name = 'Juan'
            subject.birth_date = date(1985, 1, 1)
            subject.sex = 'M'
            subject.department_code = '05'
            subject.municipality_code = '001'

            db.session.add(MedicalHistory(
                clinic_id=1, patient_id=patient.id, doctor_id=doctor.id,
                cie10_code='Z000', cups_code='890201', created_at=colombia_now(),
            ))
            db.session.commit()

            with pytest.raises(RIPSValidationError) as error:
                generate_rips(1, colombia_now() - timedelta(days=1),
                              colombia_now() + timedelta(days=1))
            assert any(i['field'] == 'medical_registration' for i in error.value.issues)


class TestRetentionPolicies:

    def test_clinical_history_retention_is_registered(self, app):
        from models import RetentionPolicy

        with app.app_context():
            policy = RetentionPolicy.query.filter_by(record_type='historia_clinica').one()
            assert policy.retention_years == 15
            assert 'Resolucion 839' in policy.legal_basis
