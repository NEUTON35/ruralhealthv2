"""Pruebas de la emisión de documentos de cobro.

Dos cosas que el modelo anterior no contemplaba: que el médico independiente
factura a su propio nombre (con su propia numeración autorizada) y que el dinero
no puede vivir en un `float`.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from conftest import VALID_PASSWORD


@pytest.fixture
def clinic_profile(app):
    from models import BillingProfile, ISSUER_CLINIC, db

    with app.app_context():
        perfil = BillingProfile(
            issuer_kind=ISSUER_CLINIC,
            clinic_id=1,
            legal_name='Puesto de Salud El Progreso',
            document_type='NIT',
            document_number='900123456',
            verification_digit='7',
            fiscal_address='Vereda El Progreso, Antioquia',
            resolution_number='18764000001234',
            resolution_date=date(2026, 1, 15),
            resolution_valid_until=date(2027, 1, 15),
            invoice_prefix='FE',
            range_from=1,
            range_to=100,
            last_number=0,
        )
        db.session.add(perfil)
        db.session.commit()
        return perfil.id


@pytest.fixture
def independent_profile(app, make_user):
    """Perfil del médico independiente: factura a su propio nombre."""
    from models import BillingProfile, ISSUER_INDEPENDENT, db

    doctor = make_user(role='doctor', username='doc_indep',
                       medical_registration='RM-IND', is_autonomous=True)
    with app.app_context():
        perfil = BillingProfile(
            issuer_kind=ISSUER_INDEPENDENT,
            doctor_id=doctor.id,
            clinic_id=1,
            legal_name='Laura Martinez Rios',
            document_type='CC',
            document_number='1020304050',
            fiscal_address='Calle 10 #5-20, Medellin',
            resolution_number='18764000009999',
            resolution_date=date(2026, 2, 1),
            resolution_valid_until=date(2027, 2, 1),
            invoice_prefix='LM',
            range_from=500,
            range_to=600,
            last_number=0,
        )
        db.session.add(perfil)
        db.session.commit()
        return {'doctor': doctor, 'profile_id': perfil.id}


LINEAS = [{'description': 'Consulta de medicina general',
           'cups_code': '890201', 'quantity': 1, 'unit_price': 45000}]


class TestMoney:
    """El dinero debe ser exacto por construcción."""

    def test_money_returns_decimal(self):
        from billing import money
        assert isinstance(money(45000), Decimal)
        assert money(45000) == Decimal('45000.00')

    def test_money_rounds_to_cents(self):
        from billing import money
        assert money('45000.456') == Decimal('45000.46')
        assert money(None) == Decimal('0.00')

    def test_totals_are_exact_over_many_lines(self, app, clinic_profile, make_user):
        """Cien renglones de un valor con decimales deben sumar exacto."""
        from billing import build_draft, money
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_exacto')

        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            lineas = [{'description': f'Servicio {i}', 'quantity': 1,
                       'unit_price': '333.33'} for i in range(100)]
            factura = build_draft(perfil, patient, lineas)
            db.session.commit()

            assert factura.total == money('33333.00')


class TestNumbering:
    """La numeración no admite huecos ni repeticiones."""

    def test_first_invoice_starts_at_range_start(self, app, clinic_profile, make_user):
        from billing import build_draft, issue
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_num1')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            factura = issue(build_draft(perfil, patient, LINEAS))
            db.session.commit()

            assert factura.consecutive == 1
            assert factura.number == 'FE1'

    def test_consecutives_do_not_repeat(self, app, clinic_profile, make_user):
        from billing import build_draft, issue
        from models import BillingProfile, Invoice, db

        patient = make_user(role='patient', username='pac_num2')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            for _ in range(5):
                issue(build_draft(perfil, patient, LINEAS))
            db.session.commit()

            numeros = [f.number for f in Invoice.query.all()]
            assert len(numeros) == 5
            assert len(set(numeros)) == 5, 'no puede repetirse un numero'
            assert numeros == ['FE1', 'FE2', 'FE3', 'FE4', 'FE5']

    def test_exhausted_range_blocks_issue(self, app, clinic_profile, make_user):
        """Emitir fuera del rango autorizado invalida el documento."""
        from billing import NumberingExhausted, build_draft, issue
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_agotado')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            perfil.last_number = 100      # el rango llega hasta 100
            db.session.commit()

            with pytest.raises(NumberingExhausted):
                issue(build_draft(perfil, patient, LINEAS))

    def test_expired_resolution_blocks_issue(self, app, clinic_profile, make_user):
        from billing import NumberingExhausted, build_draft, issue
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_vencida')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            perfil.resolution_valid_until = date.today() - timedelta(days=1)
            db.session.commit()

            with pytest.raises(NumberingExhausted):
                issue(build_draft(perfil, patient, LINEAS))


class TestIndependentDoctor:
    """El profesional independiente factura a su propio nombre."""

    def test_has_its_own_numbering(self, app, independent_profile, clinic_profile,
                                   make_user):
        """Los consecutivos de la clínica y del médico no se mezclan.

        Cada resolución de la DIAN autoriza un rango a un emisor concreto. Si un
        documento del médico consumiera un número del rango de la clínica, ambos
        quedarían inválidos.
        """
        from billing import build_draft, issue
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_indep')

        with app.app_context():
            clinica = db.session.get(BillingProfile, clinic_profile)
            medico = db.session.get(BillingProfile, independent_profile['profile_id'])

            factura_clinica = issue(build_draft(clinica, patient, LINEAS))
            factura_medico = issue(build_draft(medico, patient, LINEAS))
            db.session.commit()

            assert factura_clinica.number == 'FE1'
            assert factura_medico.number == 'LM500'
            assert factura_clinica.billing_profile_id != factura_medico.billing_profile_id

    def test_profile_resolution_uses_own_identity(self, app, independent_profile):
        """El médico independiente factura con su cédula, no con el NIT ajeno."""
        from billing import profile_for_doctor
        from models import User, db

        with app.app_context():
            doctor = db.session.get(User, independent_profile['doctor'].id)
            perfil = profile_for_doctor(doctor)

            assert perfil is not None
            assert perfil.document_type == 'CC'
            assert perfil.doctor_id == doctor.id
            assert perfil.legal_name == 'Laura Martinez Rios'

    def test_employed_doctor_uses_clinic_profile(self, app, clinic_profile, make_user):
        from billing import profile_for_doctor
        from models import ISSUER_CLINIC, User, db

        doctor = make_user(role='doctor', username='doc_vinculado',
                           medical_registration='RM-V', is_autonomous=False)
        with app.app_context():
            perfil = profile_for_doctor(db.session.get(User, doctor.id))
            assert perfil is not None
            assert perfil.issuer_kind == ISSUER_CLINIC

    def test_independent_without_profile_gets_none(self, app, make_user):
        """Sin perfil no se inventa un emisor."""
        from billing import BillingError, build_draft, profile_for_doctor
        from models import User, db

        doctor = make_user(role='doctor', username='doc_sin_perfil',
                           medical_registration='RM-SP', is_autonomous=True)
        patient = make_user(role='patient', username='pac_sin_perfil')

        with app.app_context():
            perfil = profile_for_doctor(db.session.get(User, doctor.id))
            assert perfil is None
            with pytest.raises(BillingError):
                build_draft(perfil, patient, LINEAS)


class TestVatExclusion:

    def test_health_services_carry_the_exclusion_note(self, app, clinic_profile,
                                                      make_user):
        """"Excluido" no es "exento": debe constar por qué no se cobra IVA."""
        from billing import build_draft
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_iva')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            factura = build_draft(perfil, patient, LINEAS)
            db.session.commit()

            assert factura.tax_amount == Decimal('0.00')
            assert factura.tax_exclusion_note
            assert '476' in factura.tax_exclusion_note


class TestAnnulment:

    def test_issued_invoice_is_annulled_with_credit_note(self, app, clinic_profile,
                                                         make_user):
        """Una factura emitida no se borra ni se edita: se compensa."""
        from billing import annul, build_draft, issue
        from models import DOC_CREDIT_NOTE, BillingProfile, Invoice, db

        patient = make_user(role='patient', username='pac_anula')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            factura = issue(build_draft(perfil, patient, LINEAS))
            db.session.commit()

            nota = annul(factura, 'Servicio no prestado, el paciente no asistio.')
            db.session.commit()

            assert factura.is_annulled
            assert factura.annulment_reason
            # La factura original permanece.
            assert Invoice.query.filter_by(id=factura.id).count() == 1
            # Y existe la nota crédito que la compensa.
            assert nota.document_type == DOC_CREDIT_NOTE
            assert nota.credit_note_for_id == factura.id
            assert nota.total == factura.total

    def test_annulment_requires_a_reason(self, app, clinic_profile, make_user):
        from billing import BillingError, annul, build_draft, issue
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_sin_motivo')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            factura = issue(build_draft(perfil, patient, LINEAS))
            db.session.commit()

            with pytest.raises(BillingError):
                annul(factura, '')

    def test_draft_is_withdrawn_without_credit_note(self, app, clinic_profile,
                                                    make_user):
        """Un borrador nunca se radicó: no necesita compensarse."""
        from billing import annul, build_draft
        from models import INVOICE_ANNULLED, BillingProfile, db

        patient = make_user(role='patient', username='pac_borrador')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            borrador = build_draft(perfil, patient, LINEAS)
            db.session.commit()

            nota = annul(borrador, 'Se creo por error.')
            assert nota is None
            assert borrador.status == INVOICE_ANNULLED


class TestProviderHonesty:

    def test_default_provider_does_not_claim_to_have_sent(self, app, clinic_profile,
                                                          make_user):
        """No se da por radicado lo que no se radicó.

        Fingir un envío que no ocurrió haría creer al prestador que cumplió una
        obligación que sigue pendiente.
        """
        from billing import build_draft, issue
        from models import INVOICE_ISSUED, BillingProfile, db

        patient = make_user(role='patient', username='pac_proveedor')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            factura = issue(build_draft(perfil, patient, LINEAS))
            db.session.commit()

            assert factura.status == INVOICE_ISSUED
            assert factura.sent_at is None
            assert factura.cufe is None
            assert 'pendiente de radicar' in factura.provider_response.lower()

    def test_readiness_lists_what_is_missing(self, app):
        from billing import describe_readiness
        faltantes = describe_readiness(None)
        assert faltantes
        assert 'perfil' in faltantes[0].lower()


class TestChain:

    def test_invoices_are_hash_chained(self, app, clinic_profile, make_user):
        from billing import build_draft, issue, verify_chain
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_cadena')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            for _ in range(4):
                issue(build_draft(perfil, patient, LINEAS))
            db.session.commit()

            assert verify_chain(clinic_profile) == []

    def test_totals_exclude_annulled(self, app, clinic_profile, make_user):
        """Lo anulado no cuenta como ingreso."""
        from billing import annul, build_draft, issue, money, totals_for_profile
        from models import BillingProfile, db

        patient = make_user(role='patient', username='pac_totales')
        with app.app_context():
            perfil = db.session.get(BillingProfile, clinic_profile)
            issue(build_draft(perfil, patient, LINEAS))
            segunda = issue(build_draft(perfil, patient, LINEAS))
            db.session.commit()

            assert totals_for_profile(clinic_profile)['total'] == money(90000)

            annul(segunda, 'El paciente no asistio a la consulta.')
            db.session.commit()

            resumen = totals_for_profile(clinic_profile)
            assert resumen['documentos'] == 1
            assert resumen['total'] == money(45000)


class TestBillingSettingsUI:

    def test_independent_doctor_sees_billing_section(self, app, make_user, client):
        make_user(role='doctor', username='doc_ui', medical_registration='RM-UI',
                  is_autonomous=True)
        client.post('/login', data={'username': 'doc_ui', 'password': VALID_PASSWORD})

        cuerpo = client.get('/settings/').get_data(as_text=True)
        assert 'Facturación' in cuerpo or 'Facturacion' in cuerpo
        assert 'resolución de numeración' in cuerpo.lower() or \
               'resolucion de numeracion' in cuerpo.lower()

    def test_employed_doctor_does_not_see_it(self, app, make_user, client):
        make_user(role='doctor', username='doc_ui2', medical_registration='RM-UI2',
                  is_autonomous=False)
        client.post('/login', data={'username': 'doc_ui2', 'password': VALID_PASSWORD})

        cuerpo = client.get('/settings/').get_data(as_text=True)
        assert 'Guardar perfil de facturación' not in cuerpo

    def test_doctor_can_save_own_profile(self, app, make_user, client):
        from models import BillingProfile, ISSUER_INDEPENDENT

        doctor = make_user(role='doctor', username='doc_guarda',
                           medical_registration='RM-G', is_autonomous=True)
        client.post('/login', data={'username': 'doc_guarda', 'password': VALID_PASSWORD})

        client.post('/settings/', data={
            'action': 'billing_profile',
            'legal_name': 'Carlos Ruiz Gomez',
            'document_type': 'CC',
            'document_number': '1098765432',
            'fiscal_address': 'Carrera 7 #12-30',
            'resolution_number': '18764000005555',
            'invoice_prefix': 'CR',
            'range_from': '1',
            'range_to': '500',
        })

        with app.app_context():
            perfil = BillingProfile.query.filter_by(
                doctor_id=doctor.id, issuer_kind=ISSUER_INDEPENDENT).one()
            assert perfil.legal_name == 'Carlos Ruiz Gomez'
            assert perfil.document_number == '1098765432'
            assert perfil.invoice_prefix == 'CR'

    def test_inverted_range_is_rejected(self, app, make_user, client):
        from models import BillingProfile

        make_user(role='doctor', username='doc_rango', medical_registration='RM-R',
                  is_autonomous=True)
        client.post('/login', data={'username': 'doc_rango', 'password': VALID_PASSWORD})

        client.post('/settings/', data={
            'action': 'billing_profile',
            'legal_name': 'Prueba Rango',
            'document_type': 'CC',
            'document_number': '111',
            'range_from': '900',
            'range_to': '100',
        })

        with app.app_context():
            perfil = BillingProfile.query.filter_by(doctor_id=None).first()
            assert perfil is None
