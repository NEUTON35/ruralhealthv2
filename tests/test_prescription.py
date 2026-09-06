"""Pruebas del flujo de emisión de órdenes médicas.

Una orden médica es un documento con validez legal y con consecuencia física: lo
que dice se le entrega al paciente. Estas pruebas cubren los requisitos legales de
emisión y la interacción con el motor de seguridad clínica.
"""

from datetime import timedelta

import pytest

from conftest import VALID_PASSWORD


@pytest.fixture
def doctor_and_patient(app, make_user):
    from models import User, db
    from time_utils import colombia_now

    doctor = make_user(
        role='doctor', username='doc_receta',
        medical_registration='RM-12345',
        signature_path='clinic_1/firma.png',
    )
    patient = make_user(role='patient', username='pac_receta', name='Ana Perez')
    return {'doctor': doctor, 'patient': patient}


def prescription_form(**overrides):
    """Formulario mínimo válido para emitir una orden."""
    from time_utils import colombia_now
    data = {
        'med_name': 'Paracetamol',
        'quantity': '20',
        'unit': 'tableta',
        'dosage': '500 mg',
        'frequency': 'cada 8 horas',
        'route': 'ORAL',
        'instructions': 'Tomar con alimentos',
        'dx_code': 'Z000',
        'dx_description': 'Examen medico general',
        'expires_date': (colombia_now() + timedelta(days=30)).strftime('%Y-%m-%d'),
        'expires_time': '23:59',
        'patient_level': '1',
    }
    data.update(overrides)
    return data


class TestLegalRequirements:

    def test_doctor_without_registration_cannot_prescribe(self, app, make_user, client):
        """Sin registro médico profesional la orden no tendría validez legal."""
        make_user(role='doctor', username='doc_sin_registro',
                  signature_path='clinic_1/f.png')
        patient = make_user(role='patient', username='pac_sin_reg')

        client.post('/login', data={
            'username': 'doc_sin_registro', 'password': VALID_PASSWORD,
        })
        response = client.post(f'/doctor/prescription/{patient.id}',
                               data=prescription_form(), follow_redirects=False)
        assert response.status_code == 302
        assert '/settings' in response.headers['Location']

        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_doctor_without_signature_cannot_prescribe(self, app, make_user, client):
        make_user(role='doctor', username='doc_sin_firma',
                  medical_registration='RM-999')
        patient = make_user(role='patient', username='pac_sin_firma')

        client.post('/login', data={'username': 'doc_sin_firma', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{patient.id}', data=prescription_form())

        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_diagnosis_is_mandatory(self, app, doctor_and_patient, client):
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(dx_code='', dx_description=''),
        )
        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_invalid_cie10_is_rejected(self, app, doctor_and_patient, client):
        """`validate_medical_code` existía y nunca se llamaba en este flujo."""
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(dx_code='NO-ES-UN-CODIGO'),
        )
        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_validity_beyond_limit_rejected(self, app, doctor_and_patient, client):
        """Sin tope, se aceptaban órdenes válidas durante años."""
        from time_utils import colombia_now

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(
                expires_date=(colombia_now() + timedelta(days=3650)).strftime('%Y-%m-%d'),
            ),
        )
        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_past_expiry_rejected(self, app, doctor_and_patient, client):
        from time_utils import colombia_now

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(
                expires_date=(colombia_now() - timedelta(days=1)).strftime('%Y-%m-%d'),
            ),
        )
        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0


class TestSuccessfulPrescription:

    def test_order_is_created_with_all_legal_fields(self, app, doctor_and_patient, client):
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        response = client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(),
        )
        assert response.status_code == 302

        from models import MedicalOrder
        with app.app_context():
            order = MedicalOrder.query.one()
            assert order.order_number.startswith('OM-1-')
            # `signature_hash` es columna del modelo y nunca se poblaba.
            assert order.signature_hash
            assert order.doctor_registration == 'RM-12345'
            assert order.signed_at is not None
            assert order.safety_kb_version
            assert order.status == 'pendiente'

    def test_order_number_derives_from_database_id(self, app, doctor_and_patient, client):
        """El número se derivaba de `último_id + 1`, con carrera entre médicos."""
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        for _ in range(3):
            client.post(
                f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                data=prescription_form(),
            )

        from models import MedicalOrder
        with app.app_context():
            orders = MedicalOrder.query.order_by(MedicalOrder.id.asc()).all()
            numbers = [order.order_number for order in orders]
            assert len(numbers) == len(set(numbers)), 'los numeros deben ser unicos'
            for order in orders:
                assert order.order_number == f'OM-1-{order.id:05d}'

    def test_signature_hash_covers_content(self, app, doctor_and_patient, client):
        """Alterar la orden después de firmada debe poder detectarse."""
        import hashlib

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(),
        )

        from models import MedicalOrder
        with app.app_context():
            order = MedicalOrder.query.one()
            recomputed = hashlib.sha256('|'.join([
                str(order.id), order.order_number, str(order.doctor_id),
                order.doctor_registration or '', order.meds_json,
                order.diagnosis_json, order.created_at.isoformat(),
            ]).encode('utf-8')).hexdigest()
            assert order.signature_hash == recomputed

            # Si alguien cambia el medicamento, el sello deja de corresponder.
            order.meds_json = '[{"nombre_med": "Morfina", "cantidad": 100}]'
            tampered = hashlib.sha256('|'.join([
                str(order.id), order.order_number, str(order.doctor_id),
                order.doctor_registration or '', order.meds_json,
                order.diagnosis_json, order.created_at.isoformat(),
            ]).encode('utf-8')).hexdigest()
            assert order.signature_hash != tampered


class TestSafetyGate:

    def test_allergy_blocks_prescription_without_justification(
            self, app, doctor_and_patient, client):
        """El caso que puede matar a un paciente."""
        from models import MedicalOrder, PatientAllergy, db

        with app.app_context():
            db.session.add(PatientAllergy(
                clinic_id=1,
                patient_id=doctor_and_patient['patient'].id,
                substance='Penicilina',
                substance_normalized='penicilina',
                reaction='Anafilaxia',
                severity='anafilaxia',
                status='confirmada',
            ))
            db.session.commit()

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        response = client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(med_name='Amoxicilina'),
        )

        # No se redirige: se vuelve a mostrar el formulario con las alertas.
        assert response.status_code == 200
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_override_with_justification_is_recorded(self, app, doctor_and_patient, client):
        """El profesional conserva la decisión, pero queda constancia escrita."""
        from models import MedicalOrder, PatientAllergy, db

        with app.app_context():
            db.session.add(PatientAllergy(
                clinic_id=1,
                patient_id=doctor_and_patient['patient'].id,
                substance='Penicilina',
                substance_normalized='penicilina',
                severity='leve',
                status='reportada',
            ))
            db.session.commit()

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        response = client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(
                med_name='Amoxicilina',
                safety_override_reason=(
                    'Reaccion previa fue exantema leve no inmediato, hace 15 anos. '
                    'Riesgo de anafilaxia bajo. Se indica observacion 30 minutos.'
                ),
            ),
        )
        assert response.status_code == 302

        with app.app_context():
            order = MedicalOrder.query.one()
            assert order.safety_override_reason
            assert order.safety_override_at is not None
            assert order.safety_report_json

            from models import AuditLog
            events = {entry.event for entry in AuditLog.query.all()}
            assert 'prescription_safety_override' in events

    def test_short_justification_is_not_accepted(self, app, doctor_and_patient, client):
        from models import MedicalOrder, PatientAllergy, db

        with app.app_context():
            db.session.add(PatientAllergy(
                clinic_id=1, patient_id=doctor_and_patient['patient'].id,
                substance='Penicilina', substance_normalized='penicilina',
                severity='grave', status='confirmada',
            ))
            db.session.commit()

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(med_name='Amoxicilina', safety_override_reason='ok'),
        )
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_safety_report_stored_even_when_clean(self, app, doctor_and_patient, client):
        """Guardar el informe permite auditar después con qué versión se evaluó."""
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(),
        )
        import json
        from models import MedicalOrder
        with app.app_context():
            order = MedicalOrder.query.one()
            report = json.loads(order.safety_report_json)
            assert report['requires_override'] is False
            assert report['knowledge_base_version'] == order.safety_kb_version


class TestLiveSafetyCheck:

    def test_endpoint_returns_findings(self, app, doctor_and_patient, client):
        from models import PatientAllergy, db

        with app.app_context():
            db.session.add(PatientAllergy(
                clinic_id=1, patient_id=doctor_and_patient['patient'].id,
                substance='Penicilina', substance_normalized='penicilina',
                severity='grave', status='confirmada',
            ))
            db.session.commit()

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        response = client.post('/doctor/api/prescription_check', json={
            'patient_id': doctor_and_patient['patient'].id,
            'medications': [{'nombre_med': 'Amoxicilina', 'cantidad': 21}],
        })
        payload = response.get_json()
        assert payload['requires_override'] is True
        assert payload['findings']

    def test_endpoint_rejects_patient_from_another_clinic(self, app, make_user, client):
        from models import Clinic, db

        make_user(role='doctor', username='doc_check', medical_registration='RM-CK',
                  signature_path='clinic_1/f.png')
        with app.app_context():
            other = Clinic(name='Otra', status='active')
            db.session.add(other)
            db.session.commit()
            other_id = other.id
        outsider = make_user(role='patient', username='pac_otro_check', clinic_id=other_id)

        client.post('/login', data={'username': 'doc_check', 'password': VALID_PASSWORD})
        response = client.post('/doctor/api/prescription_check', json={
            'patient_id': outsider.id,
            'medications': [{'nombre_med': 'Amoxicilina', 'cantidad': 21}],
        })
        assert response.status_code == 404


class TestAllergyManagement:

    def test_doctor_can_record_allergy(self, app, doctor_and_patient, client):
        from models import PatientAllergy

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(
            f'/doctor/patient/{doctor_and_patient["patient"].id}/alergias',
            data={
                'substance': 'Penicilina',
                'reaction': 'Urticaria',
                'severity': 'grave',
                'status': 'confirmada',
            },
        )
        with app.app_context():
            allergy = PatientAllergy.query.one()
            assert allergy.substance == 'Penicilina'
            assert allergy.substance_normalized == 'penicilina'
            assert allergy.recorded_by_id == doctor_and_patient['doctor'].id

    def test_discarding_keeps_the_record(self, app, doctor_and_patient, client):
        """Una alergia descartada se conserva: forma parte de la historia."""
        from models import PatientAllergy, db

        with app.app_context():
            allergy = PatientAllergy(
                clinic_id=1, patient_id=doctor_and_patient['patient'].id,
                substance='Penicilina', substance_normalized='penicilina',
                severity='leve', status='reportada',
            )
            db.session.add(allergy)
            db.session.commit()
            allergy_id = allergy.id

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/alergia/{allergy_id}/descartar',
                    data={'reason': 'Prueba cutanea negativa realizada el 2026-08-01.'})

        with app.app_context():
            allergy = db.session.get(PatientAllergy, allergy_id)
            assert allergy is not None, 'no debe eliminarse'
            assert allergy.status == 'descartada'
            assert 'Prueba cutanea negativa' in allergy.notes

    def test_discarded_allergy_no_longer_blocks(self, app, doctor_and_patient, client):
        from models import MedicalOrder, PatientAllergy, db

        with app.app_context():
            db.session.add(PatientAllergy(
                clinic_id=1, patient_id=doctor_and_patient['patient'].id,
                substance='Penicilina', substance_normalized='penicilina',
                severity='leve', status='descartada',
            ))
            db.session.commit()

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        response = client.post(
            f'/doctor/prescription/{doctor_and_patient["patient"].id}',
            data=prescription_form(med_name='Amoxicilina'),
        )
        assert response.status_code == 302
        with app.app_context():
            assert MedicalOrder.query.count() == 1
