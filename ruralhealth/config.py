"""Configuracion de la aplicacion.

La regla central de este modulo es *fail-closed*: en produccion, cualquier secreto
ausente, debil o igual a un valor de ejemplo impide el arranque. Un sistema que
guarda historia clinica no debe poder levantarse con una llave por defecto, porque
el fallo seria silencioso y los datos quedarian cifrados con una llave publica.
"""

import os
import secrets

# Valores que aparecieron en el `.env` versionado o en el codigo fuente antes de la
# auditoria. Si alguno vuelve a aparecer, el arranque en produccion se detiene: son
# publicos y ya no protegen nada.
KNOWN_COMPROMISED = frozenset({
    'dev-only-ruralhealth-flask-secure-key-12345',
    'dev-only-ruralhealth-jwt-secure-key-12345',
    'dev-only-ruralhealth-field-key-seed-12345',
    'dev-only-ruralhealth-flask-key',
    'dev-only-ruralhealth-field-key',
    'dev-only-ruralhealth-pepper',
    'dev-only-ruralhealth-order-key',
    '0UFHhEAEwH2lEFH7IzFTGE1w',
    '-WkLf6NXmdLk89a06l1Zzeu',
    'admin123',
    'changeme',
    'change-me',
})

MIN_SECRET_LENGTH = 32

REQUIRED_IN_PRODUCTION = (
    'RURALHEALTH_SECRET_KEY',
    'RURALHEALTH_JWT_SECRET_KEY',
    'RURALHEALTH_HASH_PEPPER',
)


class ConfigurationError(RuntimeError):
    """El entorno no permite arrancar de forma segura."""


def _normalize_db_uri(uri):
    """Normaliza el esquema y exige TLS en PostgreSQL."""
    if not uri:
        return uri
    if uri.startswith('postgres://'):
        uri = uri.replace('postgres://', 'postgresql://', 1)
    if uri.startswith('postgresql') and 'sslmode' not in uri:
        uri += ('&' if '?' in uri else '?') + 'sslmode=require'
    return uri


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'si'}


def _as_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def validate_secret(name, value, environment):
    """Devuelve la lista de problemas del secreto `name`.

    En desarrollo se admite un valor generado al vuelo; en produccion no, porque
    un secreto efimero invalida las sesiones en cada reinicio y, sobre todo,
    hace ilegibles los datos cifrados con el arranque anterior.
    """
    problems = []
    if environment != 'production':
        return problems

    if not value:
        problems.append(f"{name} no esta definida.")
        return problems
    if value in KNOWN_COMPROMISED:
        problems.append(
            f"{name} usa un valor que quedo expuesto en el repositorio. "
            "Rotalo siguiendo SECURITY.md."
        )
    if len(value) < MIN_SECRET_LENGTH:
        problems.append(
            f"{name} tiene {len(value)} caracteres; se requieren al menos {MIN_SECRET_LENGTH}."
        )
    return problems


class BaseConfig:
    ENV_NAME = 'development'

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,   # descarta conexiones muertas tras un corte de red
        'pool_recycle': 1800,
    }

    # 5 MB: suficiente para una foto de formula o un comprobante, y acotado para
    # el disco de un puesto de salud rural.
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_NAME = 'rh_session'

    # Vida absoluta de la sesion.
    PERMANENT_SESSION_LIFETIME = 8 * 3600
    # Caducidad por inactividad, aplicada en servidor en cada peticion.
    SESSION_IDLE_TIMEOUT = 30 * 60

    JSON_SORT_KEYS = False

    UPLOAD_FOLDER = 'uploads'
    CLINIC_UPLOAD_QUOTA_MB = 512

    RATELIMIT_STORAGE_URI = 'memory://'

    # Vigencia maxima de una orden medica, en dias. Sin este tope el formulario
    # aceptaba fechas de caducidad arbitrariamente lejanas.
    MAX_ORDER_VALIDITY_DAYS = 180

    # Vigencia del enlace de restablecimiento de contrasena, en minutos.
    PASSWORD_RESET_TTL_MINUTES = 30

    ALLOWED_HOSTS = ()

    @property
    def is_production(self):
        return self.ENV_NAME == 'production'


class DevelopmentConfig(BaseConfig):
    ENV_NAME = 'development'
    DEBUG = False           # nunca True: el depurador de Werkzeug es ejecucion remota de codigo
    SESSION_COOKIE_SECURE = False   # permite HTTP en la LAN del puesto de salud
    PREFERRED_URL_SCHEME = 'http'
    FORCE_HTTPS = False


class TestingConfig(BaseConfig):
    ENV_NAME = 'testing'
    TESTING = True
    DEBUG = False
    SESSION_COOKIE_SECURE = False
    PREFERRED_URL_SCHEME = 'http'
    FORCE_HTTPS = False
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    RATELIMIT_ENABLED = False


class ProductionConfig(BaseConfig):
    ENV_NAME = 'production'
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = 'https'
    FORCE_HTTPS = True


CONFIGS = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
}


def load_config(environment=None):
    """Construye la configuracion y verifica que el entorno permita arrancar.

    Lanza `ConfigurationError` con *todos* los problemas encontrados a la vez,
    para que quien despliega los corrija en una sola pasada en vez de descubrirlos
    uno por reinicio.
    """
    environment = (environment or os.environ.get('FLASK_ENV') or 'development').strip().lower()
    if environment not in CONFIGS:
        raise ConfigurationError(
            f"FLASK_ENV='{environment}' no es valido. Usa: {', '.join(sorted(CONFIGS))}."
        )

    config = CONFIGS[environment]()
    problems = []

    # --- Secretos ---
    secret_key = os.environ.get('RURALHEALTH_SECRET_KEY')
    jwt_secret = os.environ.get('RURALHEALTH_JWT_SECRET_KEY')
    pepper = os.environ.get('RURALHEALTH_HASH_PEPPER')
    field_seed = os.environ.get('RURALHEALTH_FIELD_KEY_SEED')
    field_key = os.environ.get('RURALHEALTH_FIELD_ENCRYPTION_KEY')

    problems += validate_secret('RURALHEALTH_SECRET_KEY', secret_key, environment)
    problems += validate_secret('RURALHEALTH_JWT_SECRET_KEY', jwt_secret, environment)
    problems += validate_secret('RURALHEALTH_HASH_PEPPER', pepper, environment)

    if config.is_production:
        if not (field_key or field_seed):
            problems.append(
                'Falta la llave de cifrado de campo: define '
                'RURALHEALTH_FIELD_ENCRYPTION_KEY (preferido) o RURALHEALTH_FIELD_KEY_SEED. '
                'Sin ella la historia clinica no se puede cifrar.'
            )
        elif field_seed and not field_key:
            problems += validate_secret('RURALHEALTH_FIELD_KEY_SEED', field_seed, environment)

        if secret_key and jwt_secret and secret_key == jwt_secret:
            problems.append(
                'RURALHEALTH_SECRET_KEY y RURALHEALTH_JWT_SECRET_KEY son identicas. '
                'Comprometer la cookie de sesion comprometeria tambien la API.'
            )

    config.SECRET_KEY = secret_key or secrets.token_urlsafe(48)
    config.JWT_SECRET_KEY = jwt_secret or config.SECRET_KEY

    # --- Base de datos ---
    db_uri = _normalize_db_uri(
        os.environ.get('SQLALCHEMY_DATABASE_URI')
        or getattr(config, 'SQLALCHEMY_DATABASE_URI', None)
        or 'sqlite:///ruralhealth.db'
    )
    config.SQLALCHEMY_DATABASE_URI = db_uri

    if config.is_production and db_uri.startswith('sqlite'):
        # SQLite no implementa SELECT ... FOR UPDATE. Sin bloqueo de fila, dos
        # expendedores concurrentes pueden entregar el mismo inventario.
        problems.append(
            'SQLite no es apto para produccion: no soporta el bloqueo de filas que '
            'impide la doble dispensacion de medicamentos. Usa PostgreSQL.'
        )

    # --- Limitacion de trafico ---
    config.RATELIMIT_STORAGE_URI = (
        os.environ.get('RATELIMIT_STORAGE_URI') or config.RATELIMIT_STORAGE_URI
    )

    # --- Dominios permitidos ---
    raw_hosts = os.environ.get('RURALHEALTH_ALLOWED_HOSTS', '')
    config.ALLOWED_HOSTS = tuple(
        host.strip().lower() for host in raw_hosts.split(',') if host.strip()
    )

    # --- Almacenamiento ---
    config.UPLOAD_FOLDER = os.environ.get('RURALHEALTH_UPLOAD_FOLDER') or config.UPLOAD_FOLDER
    config.CLINIC_UPLOAD_QUOTA_MB = _as_int(
        os.environ.get('RURALHEALTH_CLINIC_UPLOAD_QUOTA_MB'), config.CLINIC_UPLOAD_QUOTA_MB
    )

    config.SESSION_IDLE_TIMEOUT = _as_int(
        os.environ.get('RURALHEALTH_SESSION_IDLE_TIMEOUT'), config.SESSION_IDLE_TIMEOUT
    )

    if problems:
        raise ConfigurationError(
            'La aplicacion no puede arrancar de forma segura:\n\n'
            + '\n'.join(f'  - {problem}' for problem in problems)
            + '\n\nConsulta .env.example y SECURITY.md.'
        )

    return config


def warnings_for(config):
    """Avisos que no impiden arrancar pero que quien opera debe conocer."""
    notes = []
    if config.is_production and config.RATELIMIT_STORAGE_URI.startswith('memory://'):
        notes.append(
            'RATELIMIT_STORAGE_URI usa memoria del proceso. Con varios workers cada uno '
            'lleva su propia cuenta, asi que el limite efectivo se multiplica por el numero '
            'de workers. Configura Redis.'
        )
    if config.is_production and not config.ALLOWED_HOSTS:
        notes.append(
            'RURALHEALTH_ALLOWED_HOSTS esta vacio. Sin lista de dominios permitidos, una '
            'cabecera Host manipulada puede alterar los enlaces de restablecimiento de contrasena.'
        )
    return notes
