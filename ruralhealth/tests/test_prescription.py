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
    """Formulario mínimo válido para emitir una orden.

    Incluye los campos que exige la Resolución 1403 de 2007: concentración,
    forma farmacéutica, vía, dosis, frecuencia, duración y cantidad. Sin ellos
    la orden se rechaza, que es el comportamiento buscado: una prescripción a la
    que le falta la forma farmacéutica no se puede dispensar.
    """
    from time_utils import colombia_now
    data = {
        'med_name': 'Paracetamol',
        'generic_name': 'Paracetamol',
        'concentration': '500 mg',
        'dosage_form': 'tableta',
        'route': 'oral',
        'dosage': '1 tableta',
        'frequency': 'cada 8 horas',
        'duration_days': '5',
        'quantity': '15',
        'unit': 'tableta',
        'instructions': 'Tomar con alimentos',
        'dx_code': 'Z000',
        'dx_description': 'Examen medico general',
        'expires_date': (colombia_now() + timedelta(days=30)).strftime('%Y-%m-%d'),
        'expires_time': '23:59',
        'patient_level': '1',
        'care_modality': 'presencial',
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
            # El alergeno va en el principio activo, que es lo que evalua el
            # motor. Ponerlo solo en el nombre comercial fue el defecto.
            data=prescription_form(generic_name='Amoxicilina',
                                   med_name='Amoxal'),
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
                generic_name='Amoxicilina',
                med_name='Amoxal',
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
            data=prescription_form(generic_name='Amoxicilina', med_name='Amoxal',
                                   safety_override_reason='ok'),
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
            # El alergeno va en el principio activo, que es lo que evalua el
            # motor. Ponerlo solo en el nombre comercial fue el defecto.
            data=prescription_form(generic_name='Amoxicilina',
                                   med_name='Amoxal'),
        )
        assert response.status_code == 302
        with app.app_context():
            assert MedicalOrder.query.count() == 1


# =============================================================================
# Requisitos de la Resolución 1403 de 2007
# =============================================================================

class TestPrescriptionLegalContent:
    """La norma enumera qué debe contener cada renglón de una prescripción.

    Faltaban seis elementos. Una receta a la que le falta la forma farmacéutica
    no es dispensable: la farmacia no sabe si entregar cápsulas o suspensión, y
    esa diferencia importa sobre todo en pediatría.
    """

    @pytest.mark.parametrize('campo', [
        'concentration', 'dosage_form', 'dosage', 'frequency', 'duration_days',
    ])
    def test_missing_required_field_blocks_issue(self, app, doctor_and_patient,
                                                 client, campo):
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                    data=prescription_form(**{campo: ''}))

        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0, (
                f'la orden no debe emitirse sin {campo}'
            )

    def test_unknown_dosage_form_rejected(self, app, doctor_and_patient, client):
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                    data=prescription_form(dosage_form='inventada'))
        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_unknown_route_rejected(self, app, doctor_and_patient, client):
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                    data=prescription_form(route='telepatica'))
        from models import MedicalOrder
        with app.app_context():
            assert MedicalOrder.query.count() == 0

    def test_order_stores_every_required_element(self, app, doctor_and_patient, client):
        import json

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                    data=prescription_form())

        from models import MedicalOrder
        with app.app_context():
            order = MedicalOrder.query.one()

            # Datos del paciente congelados en la orden.
            assert order.patient_document, 'falta el numero de documento'
            assert order.patient_document_type, 'falta el tipo de documento'
            assert order.clinical_record_number, 'falta el numero de historia clinica'
            assert order.care_modality, 'falta la modalidad de atencion'

            # Datos del prescriptor.
            assert order.doctor_registration == 'RM-12345'
            assert order.signed_at is not None

            # Cada renglon.
            med = json.loads(order.meds_json)[0]
            for campo in ('denominacion_comun', 'concentracion', 'forma_farmaceutica',
                          'via', 'dosis', 'frecuencia', 'duracion_dias',
                          'cantidad', 'cantidad_en_letras'):
                assert med.get(campo), f'falta {campo} en el renglon'

            assert med['cantidad'] == 15
            assert med['cantidad_en_letras'] == 'quince'

    def test_brand_name_and_generic_are_distinguished(self, app, doctor_and_patient, client):
        """La norma exige prescribir por denominación común internacional."""
        import json

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                    data=prescription_form(med_name='Dolex', generic_name='Paracetamol'))

        from models import MedicalOrder
        with app.app_context():
            med = json.loads(MedicalOrder.query.one().meds_json)[0]
            assert med['denominacion_comun'] == 'Paracetamol'
            assert med['nombre_comercial'] == 'Dolex'
            # El motor de seguridad debe evaluar el principio activo, no la marca.
            assert med['nombre_med'] == 'Paracetamol'

    def test_quantity_in_words_matches_number(self, app, doctor_and_patient, client):
        """La cantidad en letras evita que una cifra se altere con un trazo."""
        import json

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                    data=prescription_form(quantity='30', duration_days='10'))

        from models import MedicalOrder
        with app.app_context():
            med = json.loads(MedicalOrder.query.one().meds_json)[0]
            assert med['cantidad'] == 30
            assert med['cantidad_en_letras'] == 'treinta'


class TestTelemedicineConsent:
    """La Resolución 2654 de 2019 exige consentimiento específico."""

    def test_telemedicine_order_requires_specific_consent(self, app, doctor_and_patient,
                                                          client):
        from models import Chat, MedicalOrder, db

        with app.app_context():
            chat = Chat(clinic_id=1,
                        patient_id=doctor_and_patient['patient'].id,
                        doctor_id=doctor_and_patient['doctor'].id, status='open')
            db.session.add(chat)
            db.session.commit()
            chat_id = chat.id

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                    data=prescription_form(chat_id=str(chat_id)))

        with app.app_context():
            assert MedicalOrder.query.count() == 0, (
                'sin consentimiento de telemedicina no debe emitirse la orden'
            )

    def test_order_records_modality_and_consent(self, app, doctor_and_patient, client):
        from models import CARE_TELEMEDICINE, Chat, InformedConsentLog, MedicalOrder, db
        from time_utils import colombia_now
        from legal_documents import consent_reference

        version, huella = consent_reference('telemedicine')

        with app.app_context():
            chat = Chat(clinic_id=1,
                        patient_id=doctor_and_patient['patient'].id,
                        doctor_id=doctor_and_patient['doctor'].id, status='open')
            db.session.add(chat)
            db.session.add(InformedConsentLog(
                patient_id=doctor_and_patient['patient'].id,
                clinic_id=1, consent_type='telemedicine',
                document_version=version, document_hash=huella,
                granted=True, digital_signature_hash='firma',
                timestamp=colombia_now(),
            ))
            db.session.commit()
            chat_id = chat.id

        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{doctor_and_patient["patient"].id}',
                    data=prescription_form(chat_id=str(chat_id)))

        with app.app_context():
            order = MedicalOrder.query.one()
            assert order.care_modality == CARE_TELEMEDICINE
            assert order.telemedicine_consent_id is not None

    def test_patient_can_grant_consent(self, app, make_user, client):
        from models import InformedConsentLog
        from legal_documents import consent_reference

        make_user(role='patient', username='pac_telemed')
        client.post('/login', data={'username': 'pac_telemed', 'password': VALID_PASSWORD})

        assert client.get('/privacidad-datos/consentimiento-telemedicina').status_code == 200

        client.post('/privacidad-datos/consentimiento-telemedicina',
                    data={'accept_telemedicine': 'on'})

        version, huella = consent_reference('telemedicine')
        with app.app_context():
            log = InformedConsentLog.query.filter_by(consent_type='telemedicine').one()
            assert log.granted is True
            # Queda registrado QUE texto acepto, no solo que acepto.
            assert log.document_version == version
            assert log.document_hash == huella
            assert log.digital_signature_hash


class TestOrderAnnulment:
    """Una orden mal emitida debe poder retirarse."""

    def _emitir(self, client, patient_id):
        client.post('/login', data={'username': 'doc_receta', 'password': VALID_PASSWORD})
        client.post(f'/doctor/prescription/{patient_id}', data=prescription_form())

    def test_doctor_can_annul_with_reason(self, app, doctor_and_patient, client):
        from models import MedicalOrder

        self._emitir(client, doctor_and_patient['patient'].id)
        with app.app_context():
            order_id = MedicalOrder.query.one().id

        client.post(f'/doctor/order/{order_id}/anular',
                    data={'reason': 'Dosis equivocada, se emite orden corregida.'})

        with app.app_context():
            order = MedicalOrder.query.one()
            assert order.annulled_at is not None
            assert order.status == 'anulada'
            assert order.annulled_by_id == doctor_and_patient['doctor'].id
            assert 'Dosis equivocada' in order.annulment_reason

    def test_annulment_requires_a_real_reason(self, app, doctor_and_patient, client):
        from models import MedicalOrder

        self._emitir(client, doctor_and_patient['patient'].id)
        with app.app_context():
            order_id = MedicalOrder.query.one().id

        client.post(f'/doctor/order/{order_id}/anular', data={'reason': 'error'})

        with app.app_context():
            assert MedicalOrder.query.one().annulled_at is None

    def test_order_is_never_deleted(self, app, doctor_and_patient, client):
        """Anular no borra: la orden es parte de la historia clinica."""
        from models import MedicalOrder

        self._emitir(client, doctor_and_patient['patient'].id)
        with app.app_context():
            order_id = MedicalOrder.query.one().id

        client.post(f'/doctor/order/{order_id}/anular',
                    data={'reason': 'Se prescribio al paciente equivocado.'})

        with app.app_context():
            assert MedicalOrder.query.count() == 1
            assert MedicalOrder.query.one().meds_json

    def test_annulled_order_cannot_be_dispensed(self, app, doctor_and_patient,
                                                make_user, client):
        """El fallo que esto previene: dispensar una receta retirada."""
        from models import MedicalOrder, MedicationPickupTicket

        self._emitir(client, doctor_and_patient['patient'].id)
        with app.app_context():
            order_id = MedicalOrder.query.one().id

        client.post(f'/doctor/order/{order_id}/anular',
                    data={'reason': 'Medicamento contraindicado, se retira la orden.'})

        make_user(role='staff', username='staff_anulada')
        client.post('/login', data={'username': 'staff_anulada', 'password': VALID_PASSWORD})
        client.post(f'/staff/dispense/{order_id}', data={})

        with app.app_context():
            assert MedicationPickupTicket.query.count() == 0, (
                'una orden anulada no puede generar ticket de entrega'
            )

    def test_only_the_signing_doctor_may_annul(self, app, doctor_and_patient,
                                               make_user, client):
        from models import MedicalOrder

        self._emitir(client, doctor_and_patient['patient'].id)
        with app.app_context():
            order_id = MedicalOrder.query.one().id

        make_user(role='doctor', username='doc_ajeno', medical_registration='RM-999',
                  signature_path='clinic_1/f.png')
        client.post('/login', data={'username': 'doc_ajeno', 'password': VALID_PASSWORD})
        client.post(f'/doctor/order/{order_id}/anular',
                    data={'reason': 'Intento de anulacion por otro profesional.'})

        with app.app_context():
            assert MedicalOrder.query.one().annulled_at is None
