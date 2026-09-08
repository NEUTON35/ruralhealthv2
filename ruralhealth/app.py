"""RuralHealth Connect, punto de entrada de la aplicacion.

Se construye con patron de fabrica (`create_app`) para que las pruebas puedan
levantar instancias aisladas con su propia base de datos, en lugar de compartir
el estado global de un modulo.
"""

import json
import logging
import os
import secrets
import sys
from logging.handlers import RotatingFileHandler

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import (
    Flask, abort, has_request_context, jsonify, redirect, render_template,
    request, send_from_directory, session, url_for,
)
from flask_login import LoginManager, current_user, login_required
from sqlalchemy import event, or_
from sqlalchemy.orm import Session, with_loader_criteria
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from config import ConfigurationError, load_config, warnings_for
from models import (
    ClinicScoped, DoctorTariff, Message, PaymentVerificationTicket, Stock, User,
    UserPolicyEnrollment, db,
)
from security import (
    ensure_security_schema, generate_csrf_token, hash_password, limiter,
    pii_hash, validate_csrf,
)
from time_utils import colombia_iso, colombia_now, colombia_strftime

try:
    from flask_migrate import Migrate
except ImportError:
    Migrate = None

from flask_talisman import Talisman
import bleach


# Rutas accesibles sin haber cambiado la contrasena inicial. Todo lo demas queda
# bloqueado hasta que la cuenta deja de usar la clave con la que fue creada.
PASSWORD_CHANGE_EXEMPT = frozenset({
    'auth.login', 'auth.logout', 'settings.index', 'static',
    'auth.terms', 'auth.privacy', 'auth.transparency',
    'health', 'readiness', 'manifest', 'service_worker',
})

# Rutas accesibles con la clinica suspendida.
SUSPENDED_CLINIC_EXEMPT = frozenset({
    'auth.logout', 'auth.login', 'static', 'health', 'readiness',
    'auth.terms', 'auth.privacy', 'auth.transparency',
})


def configure_logging(app):
    """Registro rotatorio a archivo, sin datos personales.

    El formateador no recibe nunca contenido clinico: los mensajes se escriben
    con identificadores, no con nombres ni documentos.
    """
    os.makedirs('logs', exist_ok=True)
    handler = RotatingFileHandler(
        'logs/ruralhealth.log', maxBytes=5_000_000, backupCount=10, encoding='utf-8'
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s [%(module)s:%(lineno)d] %(message)s'
    ))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


def build_csp():
    """Politica de seguridad de contenido.

    Todos los recursos (CSS, iconos, mapas, graficas, tipografia) se sirven
    desde este mismo servidor, asi que la politica ya no admite ningun origen
    externo para scripts ni estilos. Antes habia cuatro CDN autorizados; cada uno
    era un tercero que podia ejecutar JavaScript sobre paginas con historia
    clinica abierta.

    Las dos excepciones que quedan:

    - **Jitsi** para videollamada, que por naturaleza necesita conexion.
    - **OpenStreetMap** para las teselas del mapa, que no se pueden empaquetar.

    `unsafe-inline` en `script-src` sigue presente porque las plantillas llevan
    JavaScript en linea. Es una concesion consciente: mientras exista, la CSP no
    protege frente a XSS por script inyectado en linea, y la defensa real es el
    escapado de Jinja mas la sanitizacion con bleach. Eliminarla requiere extraer
    ese JavaScript a archivos de `/static`.
    """
    return {
        'default-src': ["'self'"],
        'script-src': ["'self'", "'unsafe-inline'", 'https://*.jitsi.net', 'https://8x8.vc'],
        'style-src': ["'self'", "'unsafe-inline'"],
        'img-src': ["'self'", 'data:', 'blob:', 'https://*.tile.openstreetmap.org'],
        'font-src': ["'self'", 'data:'],
        'connect-src': ["'self'", 'https://*.jitsi.net', 'https://8x8.vc'],
        'frame-src': ["'self'", 'https://meet.jit.si', 'https://*.jitsi.net', 'https://8x8.vc'],
        'frame-ancestors': ["'none'"],
        'base-uri': ["'self'"],
        'form-action': ["'self'"],
        'object-src': ["'none'"],
    }


def register_clinic_scope():
    """Filtra por clinica toda consulta a un modelo `ClinicScoped`.

    Es una red de seguridad de ultimo recurso frente a una fuga de datos entre
    sedes. No sustituye a la comprobacion explicita de pertenencia en cada
    endpoint: el ambito se toma de la sesion, y hay caminos legitimos que lo
    desactivan con `include_all_clinics`.
    """
    @event.listens_for(Session, 'do_orm_execute')
    def add_clinic_scope(execute_state):
        if not execute_state.is_select:
            return
        if not has_request_context():
            return
        if execute_state.execution_options.get('include_all_clinics'):
            return
        role = session.get('role')
        clinic_id = session.get('clinic_id')
        if not clinic_id or role == 'super':
            return
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                ClinicScoped,
                lambda cls: cls.clinic_id == clinic_id,
                include_aliases=True,
                track_closure_variables=False,
            )
        )


_scope_registered = False


def create_app(environment=None, config_override=None):
    """Construye y configura la aplicacion."""
    global _scope_registered

    app = Flask(__name__)

    config = load_config(environment)
    app.config.from_object(config)
    app.config['ENV_NAME'] = config.ENV_NAME
    if config_override:
        app.config.update(config_override)

    is_production = app.config['ENV_NAME'] == 'production'
    is_testing = app.config['ENV_NAME'] == 'testing'

    # ProxyFix solo tiene sentido detras de un proxy de confianza. Aplicado sin
    # proxy, permite falsear la IP de origen mediante X-Forwarded-For, lo que
    # dejaria sin efecto el bloqueo por intentos fallidos.
    if is_production:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    if not is_testing:
        configure_logging(app)

    for note in warnings_for(config):
        app.logger.warning('CONFIGURACION: %s', note)

    db.init_app(app)
    # Migrate registra su propia configuracion en app.extensions['migrate'];
    # no hay que sobrescribirla, o `flask db` deja de encontrarla.
    if Migrate:
        Migrate(app, db, directory='migrations')

    if not is_testing:
        limiter.init_app(app)

    Talisman(
        app,
        content_security_policy=build_csp(),
        force_https=is_production,
        strict_transport_security=is_production,
        strict_transport_security_max_age=31_536_000,
        strict_transport_security_include_subdomains=True,
        session_cookie_secure=is_production,
        referrer_policy='strict-origin-when-cross-origin',
    )

    if not _scope_registered:
        register_clinic_scope()
        _scope_registered = True

    register_jinja(app)
    register_login(app)
    register_hooks(app)
    register_error_handlers(app)
    register_core_routes(app)
    register_blueprints(app)

    return app


def register_jinja(app):
    import ui
    from estados import traducir as traducir_estado
    from monetization import pesos
    from legal_markup import render as render_legal_markup

    # El vocabulario visual, disponible en todas las plantillas sin importar.
    ui.registrar(app.jinja_env)

    app.jinja_env.filters['colombia_iso'] = colombia_iso
    app.jinja_env.filters['colombia_time'] = lambda dt: colombia_strftime(dt, '%H:%M')
    app.jinja_env.filters['colombia_date'] = lambda dt: colombia_strftime(dt, '%d/%m/%Y')
    app.jinja_env.filters['from_json'] = lambda s: json.loads(s) if s else []
    app.jinja_env.filters['legal_markup'] = render_legal_markup
    app.jinja_env.filters['estado'] = traducir_estado
    app.jinja_env.filters['pesos'] = pesos
    app.jinja_env.globals['csrf_token'] = generate_csrf_token
    app.jinja_env.globals['app_env'] = app.config['ENV_NAME']


def legal_values():
    """Datos del prestador que completan los textos legales."""
    from models import LegalConfiguration
    try:
        return {row.key: row.value for row in LegalConfiguration.query.all() if row.value}
    except Exception:
        # La tabla puede no existir todavia en un arranque previo a la migracion.
        return {}


def legal_context(active_tab):
    """Contexto comun de las paginas legales."""
    from legal_documents import DOCUMENTS, pending_configuration, render_document

    valores = legal_values()
    documentos = {clave: render_document(clave, valores) for clave in DOCUMENTS}

    return {
        'active_tab': active_tab,
        'documents': documentos,
        'pending_config': pending_configuration(valores),
        'tabs': [
            ('terms', 'Términos de uso', url_for('terminos')),
            ('privacy', 'Datos personales', url_for('privacidad')),
            ('privacy_notice', 'Aviso de privacidad', url_for('aviso_privacidad')),
            ('telemedicine', 'Telemedicina', url_for('telemedicina')),
            ('transparency', 'Transparencia', url_for('transparencia')),
        ],
    }


def register_login(app):
    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Inicie sesion para continuar.'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            user = db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None
        if user and not user.is_active_account:
            # Una cuenta desactivada no debe poder seguir usando una sesion abierta.
            return None

        # Toda sesion abierta antes del ultimo cambio de contrasena queda
        # invalidada. Sin esto, restablecer la clave de una cuenta comprometida
        # no echaba a quien la habia tomado: su cookie seguia funcionando.
        if user and has_request_context() and user.password_changed_at:
            emitida = session.get('pwd_epoch')
            if emitida is None or int(emitida) < int(user.password_changed_at.timestamp()):
                session.clear()
                return None

        if user and has_request_context():
            session['clinic_id'] = user.clinic_id
            session['role'] = user.role
        return user


def register_hooks(app):
    @app.before_request
    def enforce_request_security():
        # 1. HTTPS obligatorio en produccion.
        if app.config.get('FORCE_HTTPS') and not request.is_secure:
            return redirect(request.url.replace('http://', 'https://', 1), code=308)

        # 2. Cabecera Host permitida. Sin esta comprobacion, una Host manipulada
        #    puede alterar los enlaces absolutos, incluido el de restablecimiento
        #    de contrasena, y desviarlo a un dominio del atacante.
        allowed_hosts = app.config.get('ALLOWED_HOSTS') or ()
        if allowed_hosts:
            host = (request.host or '').split(':')[0].lower()
            if host not in allowed_hosts:
                app.logger.warning('Host rechazado: %s', host)
                abort(400)

        # 3. CSRF.
        validate_csrf()

        # 4. Caducidad de sesion por inactividad. La vida absoluta de la cookie
        #    no basta: una sesion abierta en un equipo compartido del puesto de
        #    salud queda disponible para quien lo use despues.
        if current_user.is_authenticated:
            idle_timeout = app.config.get('SESSION_IDLE_TIMEOUT')
            now = colombia_now().timestamp()
            last_seen = session.get('_last_seen')
            if idle_timeout and last_seen and (now - last_seen) > idle_timeout:
                from flask_login import logout_user
                logout_user()
                session.clear()
                return redirect(url_for('auth.login', expired=1))
            session['_last_seen'] = now

        # 5. Clinica suspendida.
        if current_user.is_authenticated and current_user.role != 'super':
            clinic = current_user.clinic
            if (not clinic or clinic.status != 'active') and \
                    request.endpoint not in SUSPENDED_CLINIC_EXEMPT:
                return redirect(url_for('auth.login'))

        # 6. Cambio de contrasena obligatorio. Las cuentas creadas con una clave
        #    inicial no pueden operar hasta cambiarla.
        if current_user.is_authenticated and getattr(current_user, 'must_change_password', False):
            if request.endpoint not in PASSWORD_CHANGE_EXEMPT:
                return redirect(url_for('settings.index', force_password_change=1))

    @app.after_request
    def add_security_headers(response):
        response.headers['Permissions-Policy'] = (
            'geolocation=(self), payment=(), camera=(self), microphone=(self), '
            'usb=(), magnetometer=(), accelerometer=()'
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
        if current_user.is_authenticated:
            # Ninguna respuesta autenticada debe quedar en el cache del navegador
            # ni en un proxy intermedio: contiene datos clinicos.
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response.headers['Pragma'] = 'no-cache'
        return response


def register_error_handlers(app):
    def wants_json():
        return (
            request.path.startswith('/api/')
            or '/api/' in request.path
            or request.accept_mimetypes.best == 'application/json'
        )

    # Contenido de cada pagina de error.
    #
    # Un mensaje de error sirve para dos cosas: decir que paso y decir que hacer
    # ahora. Lo segundo es lo que casi siempre falta. En una plataforma de salud
    # rural pesa mas de lo normal: quien esta al otro lado puede haber caminado
    # dos horas para llegar a la senal.
    #
    # Ninguno explica de mas. Un 403 que detalle por que se denego el acceso le
    # esta diciendo a quien no deberia estar ahi como funciona el permiso.
    PAGINAS = {
        400: (
            'Solicitud invalida',
            'El formulario llego incompleto o con datos que el sistema no pudo leer.',
            ['Vuelva atras y revise que los campos obligatorios esten llenos.',
             'Si copio y pego algun dato, verifique que no arrastre espacios.'],
        ),
        401: (
            'Sesion no iniciada',
            'Para ver esta pagina necesita haber iniciado sesion.',
            ['Su sesion pudo haber caducado por inactividad.'],
        ),
        403: (
            'Acceso denegado',
            'Su cuenta no tiene permiso para ver este recurso.',
            ['Si cree que deberia tenerlo, comuniquese con el administrador de su clinica.',
             'Este intento queda registrado en la auditoria.'],
        ),
        404: (
            'Pagina no encontrada',
            'La direccion no corresponde a ninguna pagina del sistema.',
            ['Revise el enlace por si llego incompleto.',
             'Si lo guardo en favoritos, la pagina pudo haber cambiado de sitio.'],
        ),
        405: (
            'Operacion no permitida',
            'Esa accion no esta disponible en esta pagina.',
            ['Vuelva al inicio y navegue desde el menu.'],
        ),
        413: (
            'El archivo es demasiado grande',
            'El archivo supera el limite permitido.',
            ['Si es una foto, tomela con menor resolucion o reduzca su tamano.',
             'Los documentos escaneados pesan menos en blanco y negro.'],
        ),
        429: (
            'Demasiadas solicitudes',
            'Se recibieron demasiadas peticiones desde su conexion en poco tiempo.',
            ['Espere un minuto y vuelva a intentarlo.',
             'Si esta en una red compartida, el limite pudo alcanzarlo otra persona.'],
        ),
        503: (
            'Servicio no disponible',
            'El sistema esta temporalmente fuera de servicio.',
            ['Intente de nuevo en unos minutos.'],
        ),
    }

    def pagina_error(code, incident=None):
        titulo, mensaje, pasos = PAGINAS.get(
            code, ('Algo salio mal', 'Ocurrio un error inesperado.', []))
        return render_template('error.html', code=code, title=titulo,
                               message=mensaje, pasos=pasos,
                               incident=incident), code

    @app.errorhandler(400)
    def bad_request(error):
        if wants_json():
            return jsonify({'error': 'bad_request'}), 400
        return pagina_error(400)

    @app.errorhandler(401)
    def unauthorized(error):
        if wants_json():
            return jsonify({'error': 'unauthorized'}), 401
        return pagina_error(401)

    @app.errorhandler(403)
    def forbidden(error):
        if wants_json():
            return jsonify({'error': 'forbidden'}), 403
        return pagina_error(403)

    @app.errorhandler(404)
    def not_found(error):
        if wants_json():
            return jsonify({'error': 'not_found'}), 404
        return pagina_error(404)

    @app.errorhandler(405)
    def method_not_allowed(error):
        if wants_json():
            return jsonify({'error': 'method_not_allowed'}), 405
        return pagina_error(405)

    @app.errorhandler(413)
    def too_large(error):
        if wants_json():
            return jsonify({'error': 'payload_too_large'}), 413
        return pagina_error(413)

    @app.errorhandler(429)
    def rate_limited(error):
        if wants_json():
            return jsonify({'error': 'too_many_requests'}), 429
        return pagina_error(429)

    @app.errorhandler(503)
    def unavailable(error):
        if wants_json():
            return jsonify({'error': 'service_unavailable'}), 503
        return pagina_error(503)

    @app.errorhandler(Exception)
    def handle_exception(error):
        if isinstance(error, HTTPException):
            return error

        # La traza va al registro; al usuario solo le llega un identificador con
        # el que soporte puede localizarla. Una traza en pantalla revelaria
        # estructura interna y, en un fallo durante una consulta, datos clinicos.
        incident = secrets.token_hex(8)
        app.logger.exception(
            'Excepcion no controlada [incidente %s] en %s %s',
            incident, request.method, request.path,
        )
        db.session.rollback()

        if request.path.startswith('/api/'):
            return jsonify({
                'error': 'internal_server_error',
                'incident': incident,
                'message': 'Ocurrio un error inesperado.',
            }), 500

        return render_template(
            'error.html',
            code=500,
            title='Error del sistema',
            message='Algo fallo de nuestro lado, no en lo que usted hizo. '
                    'El equipo tecnico ya fue notificado.',
            pasos=['Su informacion no se perdio: lo que ya estaba guardado sigue ahi.',
                   'Si estaba llenando un formulario, revise antes de volver a '
                   'enviarlo para no duplicarlo.'],
            incident=incident,
        ), 500


def register_core_routes(app):

    @app.route('/')
    def index():
        return redirect(url_for('auth.login'))

    @app.route('/health')
    @limiter.exempt
    def health():
        """Sonda de vivacidad: responde si el proceso esta en pie."""
        return jsonify({'status': 'ok', 'time': colombia_now().isoformat()})

    @app.route('/ready')
    @limiter.exempt
    def readiness():
        """Sonda de disponibilidad: responde si puede atender trafico real.

        Comprueba la base de datos y el estado de la base de conocimiento
        clinico. Un balanceador debe retirar la instancia si esto falla.
        """
        checks = {}
        healthy = True

        try:
            db.session.execute(db.text('SELECT 1'))
            checks['database'] = 'ok'
        except Exception as error:
            checks['database'] = f'error: {type(error).__name__}'
            healthy = False

        try:
            from clinical_safety import (
                KNOWLEDGE_BASE_VERSION, knowledge_base_age_months, knowledge_base_is_stale,
            )
            checks['clinical_kb_version'] = KNOWLEDGE_BASE_VERSION
            checks['clinical_kb_age_months'] = knowledge_base_age_months()
            if knowledge_base_is_stale():
                # No impide atender: seguir operando sin verificacion seria peor
                # que operar con una version que lleva tiempo sin revisarse.
                checks['clinical_kb'] = 'desactualizada: requiere revision clinica'
            else:
                checks['clinical_kb'] = 'ok'
        except Exception as error:
            checks['clinical_kb'] = f'error: {type(error).__name__}'

        return jsonify({'status': 'ok' if healthy else 'degraded', 'checks': checks}), \
            (200 if healthy else 503)

    @app.route('/manual')
    @login_required
    def manual():
        return render_template('manual.html')

    @app.route('/terminos')
    def terminos():
        return render_template('legal.html', **legal_context('terms'))

    @app.route('/privacidad')
    def privacidad():
        return render_template('legal.html', **legal_context('privacy'))

    @app.route('/aviso-de-privacidad')
    def aviso_privacidad():
        return render_template('legal.html', **legal_context('privacy_notice'))

    @app.route('/telemedicina')
    def telemedicina():
        return render_template('legal.html', **legal_context('telemedicine'))

    @app.route('/transparencia')
    def transparencia():
        return render_template('legal.html', **legal_context('transparency'))

    @app.route('/manifest.json')
    @limiter.exempt
    def manifest():
        return send_from_directory('static', 'manifest.json',
                                   mimetype='application/manifest+json')

    @app.route('/service-worker.js')
    @limiter.exempt
    def service_worker():
        response = send_from_directory('static', 'service-worker.js',
                                       mimetype='application/javascript')
        # El Service Worker no debe quedar cacheado: si se corrige un fallo de
        # privacidad en el, los navegadores tienen que recibir la version nueva.
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Service-Worker-Allowed'] = '/'
        return response

    @app.route('/uploads/<path:filename>')
    def uploaded_file(filename):
        """Sirve un archivo subido solo a quien tiene derecho a verlo.

        Cada tipo de archivo se autoriza contra su propietario. Un archivo que no
        corresponda a ningun registro conocido se rechaza: no basta con conocer
        el nombre para descargarlo.
        """
        if not current_user.is_authenticated:
            abort(403)

        # `send_from_directory` bloquea el recorrido de directorios, pero se
        # rechaza aqui de forma explicita para no depender de un solo control.
        if '..' in filename or filename.startswith('/') or '\\' in filename:
            abort(404)

        folder = app.config['UPLOAD_FOLDER']

        def serve():
            return send_from_directory(folder, filename)

        owner = User.query.filter_by(profile_pic=filename).execution_options(
            include_all_clinics=True).first()
        if owner:
            if current_user.role == 'super' or owner.clinic_id == current_user.clinic_id:
                return serve()
            abort(403)

        messages = Message.query.filter_by(file_path=filename).execution_options(
            include_all_clinics=True).all()
        if messages:
            allowed = current_user.role == 'super' or any(
                msg.chat and current_user.id in {msg.chat.patient_id, msg.chat.doctor_id}
                for msg in messages
            )
            if allowed:
                return serve()
            abort(403)

        tariff = DoctorTariff.query.filter_by(payment_methods_image=filename).execution_options(
            include_all_clinics=True).first()
        if tariff:
            if current_user.role in {'super', 'patient'} or current_user.id == tariff.doctor_id:
                return serve()
            abort(403)

        ticket = PaymentVerificationTicket.query.filter_by(proof_image=filename).execution_options(
            include_all_clinics=True).first()
        if ticket:
            if current_user.role == 'super' or current_user.id in {ticket.patient_id, ticket.doctor_id}:
                return serve()
            abort(403)

        enrollment = UserPolicyEnrollment.query.filter_by(
            policy_document_path=filename).first()
        if enrollment:
            if current_user.role == 'super' or current_user.id == enrollment.user_id:
                return serve()
            abort(403)

        signer = User.query.filter_by(signature_path=filename).execution_options(
            include_all_clinics=True).first()
        if signer:
            if current_user.role == 'super' or current_user.clinic_id == signer.clinic_id:
                return serve()
            abort(403)

        abort(404)

    @app.route('/api/omnisearch')
    @limiter.limit('30 per minute')
    def api_omnisearch():
        """Busqueda transversal, filtrada en base de datos.

        La version anterior cargaba en memoria *todos* los medicos, pacientes y
        existencias de la clinica y filtraba en Python. Con una clinica de tamano
        real eso es una descarga completa de la tabla de pacientes en cada
        pulsacion de tecla. Aqui el filtro y el limite van en la consulta.
        """
        if not current_user.is_authenticated:
            return jsonify({'results': []}), 401

        raw_query = bleach.clean(request.args.get('q', '')).strip()
        if len(raw_query) < 2 or len(raw_query) > 80:
            return jsonify({'results': []})

        pattern = f'%{raw_query.lower()}%'
        results = []

        doctors = User.query.filter(
            User.role == 'doctor',
            User.clinic_id == current_user.clinic_id,
            User.is_active_account.is_(True),
            or_(db.func.lower(User.specialty).like(pattern), User.specialty.is_(None)),
        ).limit(40).all()

        # `name` esta cifrado en reposo, asi que no se puede filtrar en SQL: se
        # descifra el conjunto acotado que ya devolvio la consulta.
        for doctor in doctors:
            haystack = f'{doctor.name or ""} {doctor.specialty or ""}'.lower()
            if raw_query.lower() in haystack:
                results.append({
                    'title': f'Dr. {doctor.name}',
                    'subtitle': doctor.specialty or 'Medico General',
                    'url': (f'/patient/dashboard?q={doctor.name}'
                            if current_user.role == 'patient'
                            else f'/doctor/book_appointment/{doctor.id}'),
                    'icon': 'stethoscope',
                })

        if current_user.role in {'doctor', 'staff', 'receptionist', 'admin', 'super'}:
            # Busqueda por documento mediante indice ciego: exacta y sin exponer
            # el documento. La busqueda parcial por documento se omite a proposito,
            # porque obligaria a descifrar la tabla completa de pacientes.
            if raw_query.isdigit():
                match = User.query.filter_by(
                    cedula_hash=pii_hash(raw_query),
                    clinic_id=current_user.clinic_id,
                    role='patient',
                ).first()
                if match:
                    results.append({
                        'title': match.name,
                        'subtitle': 'Coincidencia exacta por documento',
                        'url': (f'/doctor/patient_history/{match.id}'
                                if current_user.role == 'doctor' else '#'),
                        'icon': 'user',
                    })
            else:
                patients = User.query.filter(
                    User.role == 'patient',
                    User.clinic_id == current_user.clinic_id,
                ).order_by(User.id.desc()).limit(200).all()
                for patient in patients:
                    if raw_query.lower() in (patient.name or '').lower():
                        results.append({
                            'title': patient.name,
                            'subtitle': 'Paciente',
                            'url': (f'/doctor/patient_history/{patient.id}'
                                    if current_user.role == 'doctor' else '#'),
                            'icon': 'user',
                        })
                        if len(results) >= 20:
                            break

        stock_items = Stock.query.filter(
            Stock.clinic_id == current_user.clinic_id,
            or_(
                db.func.lower(Stock.medicamento).like(pattern),
                db.func.lower(Stock.nombre_med).like(pattern),
            ),
        ).limit(10).all()
        for item in stock_items:
            results.append({
                'title': item.medicamento or item.nombre_med,
                'subtitle': f'Existencias: {item.cantidad}',
                'url': ('/staff/inventory'
                        if current_user.role in {'staff', 'receptionist', 'admin', 'super'}
                        else '#'),
                'icon': 'pill',
            })

        return jsonify({'results': results[:10]})


def register_blueprints(app):
    from routes_admin import admin_bp
    from routes_analytics import analytics_bp
    from routes_auth import auth_bp
    from routes_doctor import doctor_bp
    from routes_expendedor import expendedor_bp
    from routes_patient import patient_bp
    from routes_pqrs import pqrs_bp
    from routes_privacy import privacy_bp
    from routes_settings import settings_bp
    from routes_staff import staff_bp
    from routes_superadmin import superadmin_bp

    app.register_blueprint(auth_bp, url_prefix='/')
    app.register_blueprint(patient_bp, url_prefix='/patient')
    app.register_blueprint(doctor_bp, url_prefix='/doctor')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(staff_bp, url_prefix='/staff')
    app.register_blueprint(expendedor_bp, url_prefix='/expendedor')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(superadmin_bp, url_prefix='/superadmin')
    app.register_blueprint(analytics_bp, url_prefix='/admin')
    app.register_blueprint(privacy_bp, url_prefix='/privacidad-datos')
    app.register_blueprint(pqrs_bp, url_prefix='/pqrs')


def bootstrap_database(app):
    """Prepara el esquema y las cuentas iniciales.

    En produccion el esquema lo gobierna Alembic (`flask db upgrade`), no esta
    funcion: crear tablas al arrancar deja la base sin version conocida y hace
    imposible revertir un cambio.
    """
    is_production = app.config['ENV_NAME'] == 'production'

    with app.app_context():
        ensure_security_schema(db, create_tables=not is_production)
        created = create_initial_accounts(app)

    return created


def create_initial_accounts(app):
    """Crea `superadmin` y `admin` si no existen.

    Diferencias con la version anterior:

    - No hay contrasenas literales en el codigo. Si no se define la variable de
      entorno, se genera una aleatoria.
    - La contrasena generada **no se escribe en el registro**. Antes iba a
      `logs/ruralhealth.log` en texto plano, donde queda indefinidamente y suele
      copiarse a sistemas de agregacion.
    - Ambas cuentas nacen con `must_change_password`, de modo que la clave inicial
      solo sirve para el primer inicio de sesion.
    """
    created = []

    definitions = (
        ('superadmin', 'super', 'SuperAdmin', '9999999999', 'RURALHEALTH_SUPER_PASSWORD'),
        ('admin', 'admin', 'Administrador', '0000000000', 'RURALHEALTH_ADMIN_PASSWORD'),
    )

    for username, role, display_name, cedula, env_var in definitions:
        if User.query.filter_by(username=username).first():
            continue

        supplied = os.environ.get(env_var)
        password = supplied or secrets.token_urlsafe(18)
        now = colombia_now()

        user = User(
            clinic_id=1,
            username=username,
            password=hash_password(password),
            role=role,
            name=display_name,
            cedula=cedula,
            cedula_hash=pii_hash(cedula),
            created_at=now,
            must_change_password=True,
            password_changed_at=None,
            accepted_terms_at=now,
            accepted_privacy_at=now,
            accepted_transparency_at=now,
            sensitive_data_consent_at=now if role == 'super' else None,
        )
        db.session.add(user)
        db.session.commit()
        created.append((username, password, bool(supplied)))

    if created:
        print('\n' + '+' + '-' * 68 + '+')
        print('|' + ' CUENTAS INICIALES CREADAS '.center(68) + '|')
        print('+' + '-' * 68 + '+')
        for username, password, from_env in created:
            if from_env:
                line = f' USUARIO: {username:<12} | contrasena tomada de {"la variable de entorno"}'
            else:
                line = f' USUARIO: {username:<12} | CLAVE: {password}'
            print('|' + line.ljust(68) + '|')
        print('|' + ' '.center(68) + '|')
        print('|' + ' Se exigira cambiarla en el primer inicio de sesion. '.center(68) + '|')
        print('|' + ' Esta clave NO queda registrada en los archivos de log. '.center(68) + '|')
        print('+' + '-' * 68 + '+\n')

        # Al registro va la constancia del hecho, nunca la contrasena.
        for username, _password, _from_env in created:
            app.logger.info(
                'Cuenta inicial creada: %s (cambio de contrasena obligatorio).', username
            )

    return created


def get_local_ip():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('10.254.254.254', 1))
        return sock.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        sock.close()


# `--local` se procesa aqui, antes de construir la aplicacion.
#
# Flask-SQLAlchemy crea el motor cuando se inicializa la extension, asi que
# cambiar `SQLALCHEMY_DATABASE_URI` despues no tiene efecto: el motor seguiria
# apuntando a la base anterior y el fallo de conexion sería el mismo con un
# mensaje distinto.
if __name__ == '__main__' and '--local' in sys.argv:
    _instancia = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
    os.makedirs(_instancia, exist_ok=True)
    _ruta_local = os.path.join(_instancia, 'ruralhealth.db')
    os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + _ruta_local.replace(os.sep, '/')
    os.environ.setdefault('FLASK_ENV', 'development')
    print(f'\nModo local: base de datos en {_ruta_local}')


# Instancia de modulo, para compatibilidad con `flask` y con los scripts que
# importan `from app import app`.
try:
    app = create_app()
except ConfigurationError as error:
    print('\n' + '=' * 72)
    print(' RURALHEALTH CONNECT, ARRANQUE DETENIDO')
    print('=' * 72)
    print(f'\n{error}\n')
    raise SystemExit(1)


def explain_database_failure(error):
    """Traduce un fallo de conexion a algo accionable.

    SQLAlchemy escupe sesenta lineas de traza cuando no puede conectar, y
    ninguna de ellas dice que hacer. Quien opera un puesto de salud no tiene por
    que leer un traceback para enterarse de que la base de datos no responde.
    """
    uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    detalle = str(error)
    # El destino, sin la contrasena.
    destino = uri.split('@')[-1].split('?')[0] if '@' in uri else uri

    print('\n' + '=' * 72)
    print(' NO SE PUDO CONECTAR A LA BASE DE DATOS')
    print('=' * 72)
    print(f'\n  Destino: {destino}\n')

    if 'SSL connection has been closed' in detalle or 'server closed the connection' in detalle:
        print('  El servidor responde pero cierra la conexion.')
        print('  Suele significar que la base de datos fue suspendida o eliminada.')
        print('  Las instancias gratuitas de Render caducan a los 30 dias.\n')
    elif 'could not translate host name' in detalle or 'Name or service not known' in detalle:
        print('  El nombre del servidor no resuelve. Revisa la direccion o tu conexion.\n')
    elif 'password authentication failed' in detalle:
        print('  Usuario o contrasena incorrectos. Es lo esperado si ya rotaste\n'
              '  las credenciales siguiendo SECURITY.md.\n')
    elif 'timeout' in detalle.lower() or 'timed out' in detalle.lower():
        print('  El servidor no responde a tiempo. Puede estar caido o bloqueado\n'
              '  por un cortafuegos.\n')
    elif 'no such table' in detalle.lower():
        print('  La base existe pero le falta el esquema. Ejecuta:\n')
        print('      flask db upgrade\n')
        return
    else:
        print(f'  Detalle: {detalle.strip()[:200]}\n')

    print('-' * 72)
    print(' PARA TRABAJAR EN LOCAL AHORA MISMO')
    print('-' * 72)
    print('\n  Usa una base local en tu propio equipo:\n')
    print('      python app.py --local\n')
    print('  Crea el archivo ruralhealth.db en la carpeta instance/ y arranca')
    print('  sin depender de ningun servidor externo.\n')
    print('-' * 72)
    print(' PARA VOLVER A UNA BASE REMOTA')
    print('-' * 72)
    print('\n  Edita SQLALCHEMY_DATABASE_URI en el archivo .env y aplica el')
    print('  esquema con:\n')
    print('      flask db upgrade\n')
    print('=' * 72 + '\n')


if __name__ == '__main__':
    try:
        bootstrap_database(app)
    except Exception as error:
        explain_database_failure(error)
        raise SystemExit(1)

    port = int(os.environ.get('PORT', 5000))
    local_ip = get_local_ip()

    if app.config['ENV_NAME'] == 'production':
        print(
            '\nADVERTENCIA: el servidor de desarrollo de Flask no es apto para '
            'produccion.\nUsa: gunicorn -c gunicorn.conf.py wsgi:app\n'
        )

    print('\n' + '+' + '-' * 58 + '+')
    print('|' + ' RURALHEALTH CONNECT - SERVIDOR LOCAL '.center(58) + '|')
    print('+' + '-' * 58 + '+')
    print('|' + f' Acceso desde esta PC:    http://localhost:{port}'.center(58) + '|')
    print('|' + f' Acceso desde el celular: http://{local_ip}:{port}'.center(58) + '|')
    print('|' + ' '.center(58) + '|')
    print('|' + ' (Ambos dispositivos en la misma red Wi-Fi) '.center(58) + '|')
    print('+' + '-' * 58 + '+\n')

    app.run(host='0.0.0.0', port=port, debug=False)
