"""Pruebas de los controles de seguridad.

Cada clase corresponde a un hallazgo concreto de la auditoría; el nombre del test
describe el comportamiento que debe cumplirse, no la implementación.
"""

import hashlib

import pytest

from conftest import VALID_PASSWORD


# --- Política de contraseñas -------------------------------------------------

class TestPasswordPolicy:

    @pytest.mark.parametrize('password,reason', [
        ('corta1A!', 'menos de 12 caracteres'),
        ('todominusculas1!', 'sin mayusculas'),
        ('TODOMAYUSCULAS1!', 'sin minusculas'),
        ('SinNumerosAqui!!', 'sin numeros'),
        ('SinSimbolos12345', 'sin simbolo'),
        ('Password123456!', 'contiene una palabra prohibida'),
        ('Aaaa1234567890!', 'caracter repetido cuatro veces'),
        ('Abc12345678901!', 'secuencia consecutiva'),
    ])
    def test_rejects_weak_passwords(self, app, password, reason):
        from security import validate_password
        with app.app_context():
            assert validate_password(password) is not None, reason

    def test_accepts_strong_password(self, app):
        from security import validate_password
        with app.app_context():
            assert validate_password('Rural-Health-2026#Seg') is None

    def test_rejects_password_containing_username(self, app):
        from security import validate_password
        with app.app_context():
            error = validate_password('MiClaveDoctor99#', username='doctor')
            assert error is not None
            assert 'usuario' in error.lower()

    def test_rejects_password_containing_name(self, app):
        from security import validate_password
        with app.app_context():
            error = validate_password('Martinez-2026#Ab', name='Ana Martinez')
            assert error is not None
            assert 'nombre' in error.lower()

    def test_rejects_leaked_repository_password(self, app):
        # Estas contraseñas estaban en el código fuente y en `.env`; ahora son
        # públicas y no deben poder volver a usarse.
        from security import validate_password
        with app.app_context():
            assert validate_password('0UFHhEAEwH2lEFH7IzFTGE1w') is not None


# --- Bloqueo por intentos fallidos -------------------------------------------

class TestLoginLockout:

    def test_locks_after_five_failures(self, app, make_user, client):
        make_user(role='patient', username='paciente_bloqueo')
        for _ in range(5):
            client.post('/login', data={
                'username': 'paciente_bloqueo', 'password': 'incorrecta-XYZ-1!',
            })
        response = client.post('/login', data={
            'username': 'paciente_bloqueo', 'password': VALID_PASSWORD,
        })
        # Bloqueada aun con la contraseña correcta.
        assert response.status_code == 429

    def test_lockout_survives_process_restart(self, app, make_user):
        """El bloqueo se persiste, no vive en memoria del proceso.

        Antes el contador era un diccionario de módulo: se perdía al reiniciar y
        no se compartía entre workers, así que el bloqueo era evitable.
        """
        from models import LoginAttempt, db
        from security import login_is_locked, record_login_failure

        make_user(role='patient', username='persistente')

        with app.test_request_context('/login', data={'username': 'persistente'}):
            for _ in range(5):
                record_login_failure('persistente')
            db.session.commit()
            assert login_is_locked('persistente')

        # Una petición nueva, que simula otro worker u otro proceso.
        with app.test_request_context('/login', data={'username': 'persistente'}):
            assert login_is_locked('persistente')
            assert LoginAttempt.query.count() == 1

    def test_successful_login_clears_counter(self, app, make_user, client):
        make_user(role='patient', username='recuperado')
        for _ in range(3):
            client.post('/login', data={
                'username': 'recuperado', 'password': 'mala-clave-1A!',
            })
        response = client.post('/login', data={
            'username': 'recuperado', 'password': VALID_PASSWORD,
        })
        assert response.status_code == 302

        from models import LoginAttempt
        with app.app_context():
            assert LoginAttempt.query.count() == 0


class TestJWTLockout:
    """La API JWT evadía por completo el bloqueo de cuenta (hallazgo P0-3)."""

    def test_jwt_endpoint_respects_lockout(self, app, make_user, client):
        make_user(role='doctor', username='medico_api', medical_registration='RM-1')

        for _ in range(5):
            client.post('/api/auth/token', json={
                'username': 'medico_api', 'password': 'incorrecta-1A!',
            })

        response = client.post('/api/auth/token', json={
            'username': 'medico_api', 'password': VALID_PASSWORD,
        })
        assert response.status_code == 429
        assert response.get_json()['error'] == 'account_locked'

    def test_web_failures_lock_the_api_too(self, app, make_user, client):
        """Un canal no debe servir para evadir el bloqueo del otro."""
        make_user(role='doctor', username='medico_mixto', medical_registration='RM-2')

        for _ in range(5):
            client.post('/login', data={
                'username': 'medico_mixto', 'password': 'mala-clave-1A!',
            })

        response = client.post('/api/auth/token', json={
            'username': 'medico_mixto', 'password': VALID_PASSWORD,
        })
        assert response.status_code == 429

    def test_generic_error_does_not_reveal_account_existence(self, app, make_user, client):
        make_user(role='patient', username='existe')

        missing = client.post('/api/auth/token', json={
            'username': 'no_existe_esta_cuenta', 'password': 'x' * 20,
        })
        wrong = client.post('/api/auth/token', json={
            'username': 'existe', 'password': 'clave-incorrecta-1A!',
        })

        assert missing.status_code == wrong.status_code == 401
        assert missing.get_json() == wrong.get_json()


# --- CSRF --------------------------------------------------------------------

class TestCSRF:

    def test_api_prefix_no_longer_grants_blanket_exemption(self, app):
        """Antes bastaba con que la ruta empezara por /api/ para quedar exenta."""
        from security import CSRF_EXEMPT_ENDPOINTS
        assert CSRF_EXEMPT_ENDPOINTS == {
            'auth.issue_token', 'auth.refresh_token', 'auth.revoke_token',
        }

    def test_post_without_token_is_rejected(self, app, make_user):
        from security import validate_csrf
        from werkzeug.exceptions import BadRequest

        with app.test_request_context('/patient/location', method='POST', data={}):
            with pytest.raises(BadRequest):
                validate_csrf()

    def test_get_requests_are_not_checked(self, app):
        from security import validate_csrf
        with app.test_request_context('/patient/dashboard', method='GET'):
            assert validate_csrf() is None


# --- Auditoría ---------------------------------------------------------------

class TestAuditChain:

    def test_entries_are_chained(self, app):
        from models import AuditLog, db
        from security import audit, verify_audit_chain

        with app.test_request_context('/x'):
            for index in range(5):
                audit(f'evento_{index}')
            db.session.commit()

            entries = AuditLog.query.order_by(AuditLog.id.asc()).all()
            assert len(entries) == 5
            assert entries[0].previous_hash is None
            for previous, current in zip(entries, entries[1:]):
                assert current.previous_hash == previous.entry_hash
            assert verify_audit_chain() == []

    def test_tampering_breaks_the_chain(self, app):
        """Alterar una entrada debe ser detectable."""
        from models import AuditLog, db
        from security import audit, verify_audit_chain

        with app.test_request_context('/x'):
            for index in range(5):
                audit(f'evento_{index}')
            db.session.commit()

            # Un atacante con acceso a la base cambia el hash de una entrada.
            victim = AuditLog.query.order_by(AuditLog.id.asc()).offset(2).first()
            victim.entry_hash = hashlib.sha256(b'falsificado').hexdigest()
            db.session.commit()

            breaks = verify_audit_chain()
            assert breaks, 'la manipulacion debe detectarse'

    def test_deletion_breaks_the_chain(self, app):
        from models import AuditLog, db
        from security import audit, verify_audit_chain

        with app.test_request_context('/x'):
            for index in range(5):
                audit(f'evento_{index}')
            db.session.commit()

            # Se elimina una entrada intermedia para ocultar una acción.
            victim = AuditLog.query.order_by(AuditLog.id.asc()).offset(2).first()
            db.session.delete(victim)
            db.session.commit()

            assert verify_audit_chain(), 'la eliminacion debe detectarse'


# --- Salidas tabulares -------------------------------------------------------

class TestCSVInjection:

    @pytest.mark.parametrize('payload', [
        '=1+1',
        '+1234',
        '-2+3',
        '@SUM(A1:A9)',
        '=cmd|\' /C calc\'!A0',
    ])
    def test_formula_prefixes_are_neutralized(self, payload):
        from security import csv_safe
        assert csv_safe(payload).startswith("'")

    def test_normal_values_untouched(self):
        from security import csv_safe
        assert csv_safe('Maria Jose Perez') == 'Maria Jose Perez'
        assert csv_safe(42) == '42'
        assert csv_safe(None) == ''

    def test_newlines_removed(self):
        from security import csv_safe
        assert '\n' not in csv_safe('linea1\nlinea2')
        assert '\r' not in csv_safe('linea1\r\nlinea2')


# --- Firma de órdenes --------------------------------------------------------

class TestOrderSignature:

    def test_valid_hash_verifies(self, app):
        from security import generate_signed_order_hash, verify_order_hash

        with app.app_context():
            meds = '[{"nombre_med":"Amoxicilina","cantidad":21}]'
            token = generate_signed_order_hash(1, 2, meds, clinic_id=1)
            assert verify_order_hash(1, 2, token, meds, clinic_id=1)

    def test_tampering_with_medications_invalidates(self, app):
        """Cambiar el contenido de la orden debe invalidar la firma."""
        from security import generate_signed_order_hash, verify_order_hash

        with app.app_context():
            original = '[{"nombre_med":"Amoxicilina","cantidad":21}]'
            token = generate_signed_order_hash(1, 2, original, clinic_id=1)

            altered = '[{"nombre_med":"Morfina","cantidad":100}]'
            assert not verify_order_hash(1, 2, token, altered, clinic_id=1)

    def test_wrong_patient_invalidates(self, app):
        from security import generate_signed_order_hash, verify_order_hash

        with app.app_context():
            meds = '[{"nombre_med":"Amoxicilina","cantidad":21}]'
            token = generate_signed_order_hash(1, 2, meds, clinic_id=1)
            assert not verify_order_hash(1, 999, token, meds, clinic_id=1)

    def test_wrong_clinic_invalidates(self, app):
        from security import generate_signed_order_hash, verify_order_hash

        with app.app_context():
            meds = '[]'
            token = generate_signed_order_hash(1, 2, meds, clinic_id=1)
            assert not verify_order_hash(1, 2, token, meds, clinic_id=2)

    def test_malformed_token_rejected(self, app):
        from security import verify_order_hash
        with app.app_context():
            for bad in ('', 'sin-punto', 'a.b', 'x' * 100):
                assert not verify_order_hash(1, 2, bad, '[]', clinic_id=1)


# --- Índice ciego ------------------------------------------------------------

class TestBlindIndex:

    def test_same_document_same_hash(self, app):
        from security import pii_hash
        with app.app_context():
            assert pii_hash('1098765432') == pii_hash('1098765432')

    def test_normalization_is_applied(self, app):
        # Puntos y espacios no deben producir hashes distintos del mismo documento.
        from security import pii_hash
        with app.app_context():
            assert pii_hash('1.098.765.432') == pii_hash('1098765432')
            assert pii_hash(' 1098765432 ') == pii_hash('1098765432')

    def test_different_documents_differ(self, app):
        from security import pii_hash
        with app.app_context():
            assert pii_hash('1098765432') != pii_hash('1098765433')

    def test_empty_returns_none(self, app):
        from security import pii_hash
        with app.app_context():
            assert pii_hash(None) is None
            assert pii_hash('   ') is None


# --- Cabeceras y sesión ------------------------------------------------------

class TestResponseHeaders:

    def test_authenticated_responses_are_not_cacheable(self, app, make_user, client):
        make_user(role='patient', username='sin_cache')
        client.post('/login', data={'username': 'sin_cache', 'password': VALID_PASSWORD})
        response = client.get('/patient/dashboard')
        cache_control = response.headers.get('Cache-Control', '')
        assert 'no-store' in cache_control

    def test_security_headers_present(self, client):
        response = client.get('/login')
        assert response.headers.get('X-Content-Type-Options') == 'nosniff'
        assert 'Permissions-Policy' in response.headers

    def test_service_worker_is_not_cached(self, client):
        response = client.get('/service-worker.js')
        assert 'no-cache' in response.headers.get('Cache-Control', '')


class TestForcedPasswordChange:

    def test_seeded_account_must_change_password(self, app, make_user, client):
        make_user(role='admin', username='admin_inicial', must_change_password=True)
        client.post('/login', data={
            'username': 'admin_inicial', 'password': VALID_PASSWORD,
        })
        response = client.get('/admin/dashboard')
        assert response.status_code == 302
        assert '/settings' in response.headers['Location']

    def test_settings_remains_reachable(self, app, make_user, client):
        make_user(role='admin', username='admin_ajustes', must_change_password=True)
        client.post('/login', data={
            'username': 'admin_ajustes', 'password': VALID_PASSWORD,
        })
        assert client.get('/settings/').status_code == 200
