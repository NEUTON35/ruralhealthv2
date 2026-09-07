import hashlib
from datetime import datetime, timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from models import (
    ACCESS_ACTIVE, Clinic, ClinicAccessCode, DOCUMENT_TYPES, DOC_CEDULA,
    DataSubjectRequest, JWTRevokedToken, Lead, PasswordResetToken, ROLE_CLINIC_ADMIN,
    ROLE_DOCTOR, ROLE_EXPENDOR, ROLE_PATIENT, ROLE_RECEPTIONIST, ROLE_STAFF, ROLE_SUPER,
    User, UserClinicAccess, db,
)
from time_utils import colombia_now
from monetization import expiration_for_plan, normalize_code
from security import (
    audit,
    check_password_reuse,
    clear_login_failures,
    decode_jwt,
    generate_jwt,
    hash_password,
    lock_remaining_seconds,
    login_is_locked,
    pii_hash,
    record_login_failure,
    record_password_change,
    require_jwt,
    rotate_csrf_token,
    validate_password,
    verify_password_constant_time,
    limiter,
)
import bleach

auth_bp = Blueprint('auth', __name__)


def redirect_for_role(role):
    destinations = {
        ROLE_SUPER: 'superadmin.dashboard',
        ROLE_CLINIC_ADMIN: 'admin.dashboard',
        ROLE_STAFF: 'staff.dashboard',
        ROLE_RECEPTIONIST: 'staff.dashboard',
        ROLE_EXPENDOR: 'expendedor.dashboard',
        ROLE_DOCTOR: 'doctor.dashboard',
        ROLE_PATIENT: 'patient.dashboard',
    }
    endpoint = destinations.get(role)
    return redirect(url_for(endpoint or 'auth.login'))


@auth_bp.route('/terminos')
def terms():
    from app import legal_context
    return render_template('legal.html', **legal_context('terms'))


@auth_bp.route('/privacidad')
def privacy():
    from app import legal_context
    return render_template('legal.html', **legal_context('privacy'))


@auth_bp.route('/transparencia')
def transparency():
    from app import legal_context
    return render_template('legal.html', **legal_context('transparency'))

@auth_bp.route('/manual')
def manual():
    return render_template('manual.html')


@auth_bp.route('/offline')
def offline():
    return render_template('offline.html')


@auth_bp.route('/socio', methods=['GET', 'POST'])
def socio():
    if request.method == 'POST':
        nombre = bleach.clean((request.form.get('nombre') or '').strip()[:180])
        institucion = bleach.clean((request.form.get('institucion') or '').strip()[:220])
        telefono = bleach.clean((request.form.get('telefono') or '').strip()[:80])
        email = bleach.clean((request.form.get('email') or '').strip()[:180])
        ciudad = bleach.clean((request.form.get('ciudad') or '').strip()[:120])
        mensaje = bleach.clean((request.form.get('mensaje') or '').strip()[:2000])
        if not nombre or not (telefono or email):
            flash('Indique su nombre y al menos un telefono o correo para poder responderle.')
            return redirect(url_for('auth.socio'))

        lead = Lead(
            nombre=nombre,
            institucion=institucion or None,
            telefono=telefono or None,
            email=email or None,
            ciudad=ciudad or None,
            contacto=' | '.join(part for part in [telefono, email, ciudad] if part),
            mensaje=mensaje or None,
        )
        db.session.add(lead)
        audit('partner_lead_created', user_id=None, details=f'institucion={institucion}; ciudad={ciudad}')
        db.session.commit()
        flash('Recibimos sus datos. Nos comunicaremos con usted a la brevedad.')
        return redirect(url_for('auth.socio'))
    return render_template('socio.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if request.method == 'GET':
        if current_user.is_authenticated:
            return redirect_for_role(current_user.role)
        if request.args.get('expired'):
            flash('La sesion se cerro por inactividad. Inicie sesion nuevamente.')
        return render_template('login.html')

    if current_user.is_authenticated:
        logout_user()
    session.clear()

    username = request.form.get('username', '').strip()
    password = request.form.get('password') or ''

    # El bloqueo se consulta antes de tocar la contrasena, y se aplica por igual
    # aqui y en la API JWT. Antes solo protegia este formulario, de modo que
    # `/api/auth/token` ofrecia un canal de fuerza bruta sin limite.
    if login_is_locked(username):
        remaining = max(1, lock_remaining_seconds(username) // 60)
        audit('login_blocked_by_lockout', details=f'minutos_restantes={remaining}')
        db.session.commit()
        flash(f'Demasiados intentos fallidos. Intenta de nuevo en {remaining} minuto(s).')
        return render_template('login.html'), 429

    user = User.query.filter_by(username=username).first()

    # Se calcula el hash aunque el usuario no exista, para que el tiempo de
    # respuesta no revele que cuentas estan registradas.
    password_ok = verify_password_constant_time(user, password)

    if user and password_ok and user.is_active_account:
        # Regeneracion completa de la sesion. La version anterior conservaba las
        # claves que no empezaban por "_", lo que dejaba pasar valores fijados
        # por un atacante antes del inicio de sesion.
        session.clear()

        login_user(user)
        session.permanent = True
        session['clinic_id'] = user.clinic_id
        session['role'] = user.role
        session['_last_seen'] = colombia_now().timestamp()
        rotate_csrf_token()

        user.last_login_at = colombia_now()
        clear_login_failures(username)
        audit('login_success', user_id=user.id)
        db.session.commit()

        if user.must_change_password:
            flash('Debe cambiar la contrasena inicial antes de continuar.')
            return redirect(url_for('settings.index', force_password_change=1))

        return redirect_for_role(user.role)

    if user and password_ok and not user.is_active_account:
        audit('login_rejected_inactive_account', user_id=user.id)
        db.session.commit()
        flash('Esta cuenta esta desactivada. Contacta al administrador.')
        return render_template('login.html'), 403

    record_login_failure(username)
    # El registro guarda el hecho, no el usuario probado: la lista de nombres
    # ensayados en un ataque es, en si misma, informacion util para el atacante.
    audit('login_failed', details='credenciales invalidas')
    db.session.commit()
    flash('Usuario o contrasena incorrectos.')
    return render_template('login.html'), 401


@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def register():
    if request.method == 'POST':
        username = bleach.clean(request.form.get('username', '').strip())
        name = bleach.clean(request.form.get('name', '').strip())
        cedula = bleach.clean(request.form.get('cedula', '').strip())
        phone = bleach.clean((request.form.get('phone') or '').strip())
        email = bleach.clean((request.form.get('email') or '').strip())
        address = bleach.clean((request.form.get('address') or '').strip())
        latitude = request.form.get('latitude', type=float)
        longitude = request.form.get('longitude', type=float)
        password = request.form.get('password') or ''
        accepted_terms = request.form.get('accept_terms') == 'on'
        accepted_privacy = request.form.get('accept_privacy') == 'on'
        accepted_transparency = request.form.get('accept_transparency') == 'on'
        accepted_location = request.form.get('accept_location') == 'on'
        clinic_code_value = normalize_code(request.form.get('clinic_code'))
        password_error = validate_password(password, username=username, name=name)
        cedula_hash = pii_hash(cedula)

        # Identificacion desagregada. RIPS exige los apellidos y nombres por
        # separado; partir la cadena `name` produce datos incorrectos en cuanto
        # alguien tiene un apellido compuesto, que es lo habitual.
        document_type = (request.form.get('document_type') or DOC_CEDULA).strip().upper()
        if document_type not in DOCUMENT_TYPES:
            document_type = DOC_CEDULA
        first_surname = bleach.clean((request.form.get('first_surname') or '').strip())[:80]
        second_surname = bleach.clean((request.form.get('second_surname') or '').strip())[:80]
        first_name = bleach.clean((request.form.get('first_name') or '').strip())[:80]
        second_name = bleach.clean((request.form.get('second_name') or '').strip())[:80]
        sex = (request.form.get('sex') or '').strip().upper()[:1]
        if sex not in {'M', 'F'}:
            sex = None
        birth_date = None
        raw_birth = (request.form.get('birth_date') or '').strip()
        if raw_birth:
            try:
                birth_date = datetime.strptime(raw_birth, '%Y-%m-%d').date()
            except ValueError:
                flash('La fecha de nacimiento no tiene un formato valido.')
                return redirect(url_for('auth.register'))
            if birth_date > colombia_now().date():
                flash('La fecha de nacimiento no puede estar en el futuro.')
                return redirect(url_for('auth.register'))

        if not (cedula or '').strip():
            flash('El numero de documento es obligatorio.')
            return redirect(url_for('auth.register'))

        if not (accepted_terms and accepted_privacy and accepted_transparency):
            flash('Debe confirmar que leyó y acepta los términos, la política de datos y el aviso de transparencia.')
            return redirect(url_for('auth.register'))

        if password_error:
            flash(password_error)
            return redirect(url_for('auth.register'))

        if User.query.filter_by(username=username).first():
            flash('El nombre de usuario ya existe')
            return redirect(url_for('auth.register'))

        if User.query.filter_by(cedula_hash=cedula_hash).first():
            flash('Esta cédula ya está registrada en el sistema')
            return redirect(url_for('auth.register'))

        access_code = None
        clinic = Clinic.query.order_by(Clinic.id.asc()).first()
        if clinic_code_value:
            access_code = ClinicAccessCode.query.filter_by(code=clinic_code_value).first()
            now = colombia_now()
            if not access_code or not access_code.is_active:
                flash('Codigo de clinica invalido.')
                return redirect(url_for('auth.register'))
            if access_code.expires_at and access_code.expires_at <= now:
                flash('El codigo de clinica ya vencio.')
                return redirect(url_for('auth.register'))
            if access_code.uses >= access_code.max_uses:
                flash('El codigo de clinica alcanzo su limite de usos.')
                return redirect(url_for('auth.register'))
            clinic = access_code.clinic
        new_user = User(
            username=username,
            clinic_id=clinic.id if clinic else None,
            name=name,
            cedula=cedula,
            cedula_hash=cedula_hash,
            phone=phone or None,
            email=email or None,
            address=address if accepted_location else None,
            latitude=latitude if accepted_location else None,
            longitude=longitude if accepted_location else None,
            location_consent_at=colombia_now() if accepted_location else None,
            password=hash_password(password),
            role='patient',
            document_type=document_type,
            first_surname=first_surname or None,
            second_surname=second_surname or None,
            first_name=first_name or None,
            second_name=second_name or None,
            sex=sex,
            birth_date=birth_date,
            created_at=colombia_now(),
            password_changed_at=colombia_now(),
            accepted_terms_at=colombia_now(),
            accepted_privacy_at=colombia_now(),
            accepted_transparency_at=colombia_now(),
        )
        db.session.add(new_user)
        db.session.flush()
        # La contrasena inicial entra al historial: sin esto, el control de
        # reutilizacion no la tiene en cuenta y el usuario puede "cambiarla"
        # por la misma con la que se registro.
        record_password_change(new_user.id, new_user.password)
        if access_code:
            db.session.add(UserClinicAccess(
                user_id=new_user.id,
                clinic_id=access_code.clinic_id,
                source='registration_code',
                status=ACCESS_ACTIVE,
                code_id=access_code.id,
                starts_at=colombia_now(),
                expires_at=expiration_for_plan(None, duration_days=access_code.duration_days),
            ))
            access_code.uses += 1
        audit('patient_registered', user_id=None, details=f'username={username}')
        db.session.commit()
        flash('Registro exitoso. Por favor inicia sesión.')
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    audit('logout')
    db.session.commit()
    logout_user()
    session.clear()
    # `purge=1` indica al cliente que vacie el almacenamiento del Service Worker.
    # En un equipo compartido del puesto de salud, dejar paginas clinicas en la
    # cache del navegador las expone a quien lo use despues.
    return redirect(url_for('auth.login', purge=1))


# =============================================================================
# Restablecimiento de contrasena
# =============================================================================
#
# Antes no existia ninguna via de recuperacion. Un medico rural que olvidara su
# clave quedaba fuera del sistema de forma permanente, y la unica salida era que
# un administrador editara la base de datos a mano.
#
# El flujo no envia correo: en las zonas donde opera el sistema no puede darse
# por supuesto ni el correo ni la cobertura. En su lugar, un administrador emite
# un codigo de un solo uso que entrega por el canal que corresponda —presencial,
# telefonico o el que la clinica tenga establecido— y que caduca en minutos.
# =============================================================================

@auth_bp.route('/recuperar', methods=['GET', 'POST'])
@limiter.limit("5 per hour", methods=["POST"])
def request_password_reset():
    """Solicitud de restablecimiento por parte del usuario."""
    if request.method == 'POST':
        username = bleach.clean(request.form.get('username', '').strip())[:150]
        user = User.query.filter_by(username=username).first()

        if user:
            db.session.add(DataSubjectRequest(
                user_id=user.id,
                clinic_id=user.clinic_id,
                request_type='restablecimiento',
                status='recibida',
                detail='Solicitud de restablecimiento de contrasena iniciada por el titular.',
                requested_at=colombia_now(),
                due_at=colombia_now() + timedelta(days=2),
                requester_ip=request.remote_addr,
            ))
            audit('password_reset_requested', user_id=user.id)
            db.session.commit()

        # La respuesta es la misma exista o no la cuenta: lo contrario permitiria
        # comprobar que usuarios estan registrados.
        flash(
            'Si la cuenta existe, el administrador de su clinica recibio la solicitud. '
            'Acerquese al puesto de salud o comuniquese con el para obtener su codigo '
            'de restablecimiento.'
        )
        return redirect(url_for('auth.login'))

    return render_template('password_reset_request.html')


@auth_bp.route('/restablecer/<token>', methods=['GET', 'POST'])
@limiter.limit("10 per hour")
def complete_password_reset(token):
    """Consumo del codigo de un solo uso y fijacion de la nueva contrasena."""
    token_hash = hashlib.sha256((token or '').encode('utf-8')).hexdigest()
    reset = PasswordResetToken.query.filter_by(token_hash=token_hash).first()

    if not reset or not reset.is_usable():
        audit('password_reset_token_invalid')
        db.session.commit()
        flash('El codigo de restablecimiento no es valido o ya vencio. Solicite uno nuevo.')
        return redirect(url_for('auth.request_password_reset'))

    user = reset.user
    if not user:
        flash('El codigo no corresponde a ninguna cuenta activa.')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        new_password = request.form.get('password') or ''
        confirm = request.form.get('confirm_password') or ''

        error = validate_password(new_password, username=user.username, name=user.name)
        if error:
            flash(error)
            return render_template('password_reset_complete.html', token=token)
        if new_password != confirm:
            flash('Las contrasenas no coinciden.')
            return render_template('password_reset_complete.html', token=token)
        if check_password_reuse(user.id, new_password):
            flash('No puede reutilizar ninguna de sus ultimas 5 contrasenas.')
            return render_template('password_reset_complete.html', token=token)

        record_password_change(user.id, user.password)
        user.password = hash_password(new_password)
        user.password_changed_at = colombia_now()
        user.must_change_password = False

        reset.used_at = colombia_now()
        reset.used_ip = request.remote_addr

        # Todo token pendiente de la misma cuenta se invalida: si se emitieron
        # varios, solo debe servir el que se acaba de usar.
        for other in PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).all():
            if other.id != reset.id:
                other.used_at = colombia_now()

        # Las sesiones y tokens anteriores dejan de valer: si la contrasena se
        # restablece porque la cuenta estaba comprometida, mantenerlos vivos
        # dejaria dentro a quien la tomo.
        clear_login_failures(user.username)
        audit('password_reset_completed', user_id=user.id)
        db.session.commit()

        flash('Contrasena actualizada. Ya puede iniciar sesion.')
        return redirect(url_for('auth.login'))

    return render_template('password_reset_complete.html', token=token)


@auth_bp.route('/api/auth/token', methods=['POST'])
@limiter.limit("10 per minute")
def issue_token():
    """Emite un par de tokens a cambio de credenciales.

    Este endpoint aplica exactamente el mismo bloqueo por intentos fallidos que
    el formulario web. En la version anterior no lo hacia: un atacante podia
    probar contrasenas aqui sin limite contra cualquier cuenta —incluida la de
    superadministrador— mientras el formulario quedaba bloqueado a los cinco
    intentos, y a cambio recibia un token de refresco valido por siete dias.
    """
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if login_is_locked(username):
        audit('jwt_blocked_by_lockout')
        db.session.commit()
        return jsonify({
            'error': 'account_locked',
            'retry_after': lock_remaining_seconds(username),
        }), 429

    user = User.query.filter_by(username=username).first()
    password_ok = verify_password_constant_time(user, password)

    if not user or not password_ok or not user.is_active_account:
        record_login_failure(username)
        audit('jwt_issue_failed', details='credenciales invalidas')
        db.session.commit()
        # Respuesta identica en los tres casos: no revela si la cuenta existe,
        # si la contrasena es incorrecta o si la cuenta esta desactivada.
        return jsonify({'error': 'invalid_credentials'}), 401

    if user.must_change_password:
        audit('jwt_issue_blocked_password_change', user_id=user.id)
        db.session.commit()
        return jsonify({
            'error': 'password_change_required',
            'message': 'Debe cambiar la contrasena inicial desde la aplicacion web.',
        }), 403

    clear_login_failures(username)
    access_token, _ = generate_jwt(user, 'access', expires_minutes=15)
    refresh_token, refresh_jti = generate_jwt(user, 'refresh', expires_minutes=60 * 24 * 7)
    user.last_login_at = colombia_now()
    audit('jwt_issued', user_id=user.id, details=f'refresh_jti={refresh_jti[:8]}')
    db.session.commit()
    return jsonify({
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'Bearer',
        'expires_in': 900,
    })


@auth_bp.route('/api/auth/refresh', methods=['POST'])
@limiter.limit("10 per minute")
def refresh_token():
    data = request.get_json(silent=True) or {}
    token = data.get('refresh_token') or ''
    try:
        payload = decode_jwt(token, required_type='refresh')
    except Exception:
        return jsonify({'error': 'invalid_refresh_token'}), 401

    user = db.session.get(User, int(payload['sub']))
    if not user or JWTRevokedToken.query.filter_by(jti=payload['jti']).first():
        return jsonify({'error': 'invalid_refresh_token'}), 401

    db.session.add(JWTRevokedToken(jti=payload['jti'], user_id=user.id, token_type='refresh'))
    access_token, _ = generate_jwt(user, 'access', expires_minutes=15)
    refresh_token_value, _ = generate_jwt(user, 'refresh', expires_minutes=60 * 24 * 7)
    audit('jwt_refreshed', user_id=user.id)
    db.session.commit()
    return jsonify({
        'access_token': access_token,
        'refresh_token': refresh_token_value,
        'token_type': 'Bearer',
        'expires_in': 900,
    })


@auth_bp.route('/api/auth/revoke', methods=['POST'])
@require_jwt('access')
def revoke_token():
    payload = request.jwt_payload
    db.session.add(JWTRevokedToken(jti=payload['jti'], user_id=payload['sub'], token_type=payload['type']))
    audit('jwt_revoked', user_id=payload['sub'])
    db.session.commit()
    return jsonify({'status': 'revoked'})
