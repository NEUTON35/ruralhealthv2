"""Cambiar la contraseña expulsa de verdad a quien tuviera la cuenta.

El procedimiento de recuperación es el único que tiene el sistema para una
cuenta comprometida, y no expulsaba a nadie. El comentario del código afirmaba
que «las sesiones y tokens anteriores dejan de valer»; lo único que se
ejecutaba debajo era borrar el contador de intentos fallidos.

El escenario: le roban la cookie a una médica en el equipo compartido del
puesto de salud. La administradora emite un código de restablecimiento, la
médica fija una clave nueva. La sesión del atacante seguía abriendo la historia
clínica de todos sus pacientes. Con el token de refresco era peor: se renovaba
solo, siete días cada vez, indefinidamente.

La clínica cree haber cortado el acceso. No lo ha cortado.
"""

import pytest

from tests.conftest import VALID_PASSWORD

NUEVA = 'Rural-Health-2027#Nue'


class TestLaSesionCae:

    def test_la_sesion_abierta_deja_de_valer_al_cambiar_la_clave(
            self, app, client, login, make_user):
        """La sesión robada deja de servir en cuanto la titular cambia la clave."""
        from models import User, db
        from security import hash_password
        from time_utils import colombia_now

        make_user(role='doctor', username='doc_exp',
                  medical_registration='RM-EXP')

        # `client` hace de sesión ya abierta — la del atacante.
        login('doc_exp')
        assert client.get('/doctor/dashboard').status_code == 200

        # La titular restablece su contraseña por otro canal.
        with app.app_context():
            usuario = User.query.filter_by(username='doc_exp').first()
            usuario.password = hash_password(NUEVA)
            usuario.password_changed_at = colombia_now()
            db.session.commit()

        respuesta = client.get('/doctor/dashboard', follow_redirects=False)
        assert respuesta.status_code == 302, (
            'la sesión anterior al cambio de contraseña siguió abierta')

    def test_quien_cambia_su_clave_no_se_expulsa_a_si_mismo(
            self, app, client, login, make_user):
        make_user(role='patient', username='pac_exp')
        login('pac_exp')

        respuesta = client.post('/settings/', data={
            'action': 'change_password',
            'current_password': VALID_PASSWORD,
            'new_password': NUEVA,
            'confirm_password': NUEVA,
        }, follow_redirects=True)
        assert respuesta.status_code == 200
        assert client.get('/patient/dashboard').status_code == 200, (
            'cambiar la propia contraseña cerró la propia sesión')


class TestElTokenCae:

    def test_el_token_emitido_antes_del_cambio_ya_no_vale(self, app, make_user):
        from models import User, db
        from security import decode_jwt, generate_jwt, hash_password
        from time_utils import colombia_now
        import jwt as pyjwt

        make_user(role='doctor', username='doc_tok',
                  medical_registration='RM-TOK')
        with app.app_context():
            usuario = User.query.filter_by(username='doc_tok').first()
            token, _ = generate_jwt(usuario, 'refresh', 60 * 24 * 7)
            # Vale ahora.
            assert decode_jwt(token, 'refresh')['sub'] == str(usuario.id)

            usuario.password = hash_password(NUEVA)
            usuario.password_changed_at = colombia_now()
            db.session.commit()

            with pytest.raises(pyjwt.InvalidTokenError):
                decode_jwt(token, 'refresh')

    def test_el_token_de_una_cuenta_desactivada_deja_de_valer(
            self, app, make_user):
        """El árbol de refresco sobrevivía a la desactivación de la cuenta."""
        from models import User, db
        from security import decode_jwt, generate_jwt
        import jwt as pyjwt

        make_user(role='doctor', username='doc_baja',
                  medical_registration='RM-BAJA')
        with app.app_context():
            usuario = User.query.filter_by(username='doc_baja').first()
            token, _ = generate_jwt(usuario, 'refresh', 60 * 24 * 7)
            assert decode_jwt(token, 'refresh')

            usuario.is_active_account = False
            db.session.commit()

            with pytest.raises(pyjwt.InvalidTokenError):
                decode_jwt(token, 'refresh')


class TestLosDocumentosLegalesSonGlobales:
    """`LegalConfiguration` no tiene clínica: quien escribe, escribe para todos."""

    def test_un_administrador_de_clinica_no_puede_tocarlos(
            self, app, client, login, make_user):
        from models import LegalConfiguration, db

        make_user(role='admin', username='adm_legal')
        login('adm_legal')

        client.post('/settings/', data={
            'action': 'legal_configuration',
            'legal_NIT_OPERADOR': '999-SECUESTRADO',
            'legal_RAZON_SOCIAL_OPERADOR': 'Clinica A SAS',
        }, follow_redirects=True)

        with app.app_context():
            fila = LegalConfiguration.query.filter_by(
                key='NIT_OPERADOR').first()
        assert fila is None or fila.value != '999-SECUESTRADO', (
            'el admin de una clínica reescribió el responsable del tratamiento '
            'de todas las demás y de la página pública')

    def test_la_superadministracion_si_puede(self, app, client, login, make_user):
        from models import LegalConfiguration, db

        make_user(role='super', username='sup_legal')
        login('sup_legal')

        client.post('/settings/', data={
            'action': 'legal_configuration',
            'legal_NIT_OPERADOR': '900123456-7',
        }, follow_redirects=True)

        with app.app_context():
            fila = LegalConfiguration.query.filter_by(
                key='NIT_OPERADOR').first()
        assert fila is not None and fila.value == '900123456-7'
