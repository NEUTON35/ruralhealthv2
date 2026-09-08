import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import timedelta
from functools import wraps

import jwt
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import abort, current_app, flash, redirect, request, session, url_for
from flask_login import current_user
from sqlalchemy import inspect
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.types import Text, TypeDecorator
import re
import magic
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from time_utils import colombia_now

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["1000 per day", "300 per hour", "30 per minute"],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
)


JWT_ISSUER = "ruralhealth"
JWT_AUDIENCE = "ruralhealth-api"
ALLOWED_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "pdf"}
ALLOWED_UPLOAD_MIMES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LOCK_SECONDS = 15 * 60

# Hash señuelo con el que se compara cuando el usuario no existe. Sin él, el
# login responde más rápido para usuarios inexistentes y esa diferencia de
# tiempo permite enumerar cuentas válidas.
_DUMMY_PASSWORD_HASH = (
    "pbkdf2:sha256:600000$ruralhealthdecoy$"
    "0000000000000000000000000000000000000000000000000000000000000000"
)


def _secret_material(name, fallback):
    """Devuelve el material de un secreto.

    En producción no hay valor por defecto: arrancar con una llave conocida deja
    la historia clínica cifrada con una clave pública, que es peor que no cifrarla,
    porque además genera la apariencia de protección.
    """
    value = os.environ.get(name)
    if not value and current_app:
        value = current_app.config.get(name)
    if not value:
        is_prod = os.environ.get("FLASK_ENV") == "production"
        if is_prod:
            raise RuntimeError(
                f"ERROR CRITICO DE SEGURIDAD: la variable '{name}' no esta definida en produccion. "
                "La aplicacion no arranca sin ella. Consulta SECURITY.md."
            )
        return str(fallback).encode("utf-8")
    return str(value).encode("utf-8")


# Coste de derivación de claves. 600.000 iteraciones es la recomendación actual
# de OWASP para PBKDF2-HMAC-SHA256.
#
# En la suite de pruebas se reduce, porque con el coste real cada usuario de
# prueba tarda cerca de un segundo en crearse y la suite deja de ejecutarse con
# la frecuencia necesaria para ser útil. La reducción se activa **solo** cuando
# `FLASK_ENV=testing`; en desarrollo y en producción se usa el coste completo.
KDF_ITERATIONS = 600_000
KDF_ITERATIONS_TESTING = 10_000


def _kdf_iterations():
    if os.environ.get("FLASK_ENV") == "testing":
        return KDF_ITERATIONS_TESTING
    return KDF_ITERATIONS


# Instancia Fernet en caché.
#
# Antes se derivaba la clave con 600.000 iteraciones de PBKDF2 en **cada**
# operación de cifrado o descifrado. Como `EncryptedText` interviene en cada
# lectura y escritura de un campo cifrado, cargar una lista de cien pacientes
# ejecutaba la derivación cientos de veces: décimas de segundo por campo, sobre
# el hardware de gama baja de un puesto de salud rural.
#
# La derivación es determinista a partir de la semilla, así que basta hacerla una
# vez por proceso. La caché se indexa por el material de la llave para que una
# rotación en caliente produzca una instancia nueva en lugar de seguir usando la
# anterior.
_FERNET_CACHE = {}
_AESGCM_CACHE = {}

# Prefijo del formato vigente. Permite distinguir a simple vista qué cifró cada
# valor y, sobre todo, hace posible una migración futura sin adivinar: un valor
# sin prefijo es del formato anterior.
CIPHER_V2_PREFIX = "v2."


def _field_key():
    """Llave de 32 bytes para AES-256-GCM.

    Se deriva de la misma semilla que antes y con el mismo salt, así que rotarla
    sigue siendo el procedimiento documentado en SECURITY.md. Lo que cambia es
    a qué cifrador se entregan esos 32 bytes.
    """
    secret = _secret_material("RURALHEALTH_FIELD_KEY_SEED", "dev-only-ruralhealth-field-key")
    cache_key = hashlib.sha256(secret).hexdigest()
    cached = _AESGCM_CACHE.get(cache_key)
    if cached is None:
        derived = hashlib.pbkdf2_hmac("sha256", secret, b"field-encryption", KDF_ITERATIONS)
        cached = AESGCM(derived)
        _AESGCM_CACHE[cache_key] = cached
    return cached


def _encrypt_field(texto):
    """Cifra con AES-256-GCM.

    Por qué se dejó Fernet
    ----------------------
    Fernet es AES-**128**-CBC más HMAC-SHA256. Es sólido, pero la historia
    clínica se conserva quince años (Resolución 839 de 2017) y hay dos razones
    para subir a 256:

    1. El algoritmo de Grover reduce a la mitad el nivel efectivo de una clave
       simétrica frente a un adversario cuántico. AES-128 quedaría en unos 64
       bits efectivos. En la práctica Grover es secuencial y no paraleliza bien,
       así que AES-128 sigue considerándose seguro; pero para un dato que debe
       seguir siendo secreto en 2041, el margen extra es gratis.
    2. El artículo 6.4 del Manual de operaciones IHCE v1.4 del Ministerio nombra
       AES-256 como el algoritmo esperado para los datos sensibles en reposo.

    GCM en lugar de CBC+HMAC porque es cifrado autenticado en una sola primitiva:
    menos piezas que combinar mal. El nonce es aleatorio de 96 bits, el tamaño
    que recomienda NIST SP 800-38D, y viaja delante del criptograma.
    """
    nonce = os.urandom(12)
    ciphertext = _field_key().encrypt(nonce, texto.encode("utf-8"), None)
    return CIPHER_V2_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def _decrypt_field(token):
    """Descifra. Acepta el formato vigente y el anterior.

    La lectura del formato Fernet se conserva para que una instalación que ya
    tuviera datos no quede ilegible al desplegar esta versión: sus valores se
    siguen leyendo y se reescriben en el formato nuevo cuando se guardan, o de
    una vez con `manage.py rotate-encryption-key`.
    """
    if token.startswith(CIPHER_V2_PREFIX):
        crudo = base64.urlsafe_b64decode(token[len(CIPHER_V2_PREFIX):].encode("ascii"))
        return _field_key().decrypt(crudo[:12], crudo[12:], None).decode("utf-8")
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def _fernet():
    """Cifrador anterior. Solo se usa ya para **leer** datos heredados."""
    key = os.environ.get("RURALHEALTH_FIELD_ENCRYPTION_KEY")
    if key:
        cached = _FERNET_CACHE.get(key)
        if cached is None:
            cached = Fernet(key.encode("utf-8"))
            _FERNET_CACHE[key] = cached
        return cached

    secret = _secret_material("RURALHEALTH_FIELD_KEY_SEED", "dev-only-ruralhealth-field-key")
    cache_key = hashlib.sha256(secret).hexdigest()
    cached = _FERNET_CACHE.get(cache_key)
    if cached is None:
        derived = hashlib.pbkdf2_hmac("sha256", secret, b"field-encryption", KDF_ITERATIONS)
        cached = Fernet(base64.urlsafe_b64encode(derived))
        _FERNET_CACHE[cache_key] = cached
    return cached


def reset_fernet_cache():
    """Vacía la caché. Necesario tras rotar la llave dentro del mismo proceso."""
    _FERNET_CACHE.clear()
    _AESGCM_CACHE.clear()


def pii_hash(value):
    """Índice ciego de un dato identificador.

    Permite buscar por documento sin almacenarlo en claro ni indexarlo: se guarda
    solo el HMAC con pimienta, de modo que la tabla no revela los documentos aunque
    se filtre, y dos bases distintas no pueden correlacionarse por este campo.
    """
    if value is None:
        return None
    normalized = "".join(ch for ch in str(value).strip().lower() if ch.isalnum())
    if not normalized:
        return None
    digest_key = _secret_material("RURALHEALTH_HASH_PEPPER", "dev-only-ruralhealth-pepper")
    return hashlib.pbkdf2_hmac(
        "sha256", normalized.encode("utf-8"), digest_key, _kdf_iterations()
    ).hex()


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        return _encrypt_field(str(value))

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return value
        try:
            return _decrypt_field(str(value))
        except (InvalidToken, InvalidTag, ValueError, TypeError):
            # Un valor que no se puede descifrar se devuelve tal cual en lugar
            # de reventar la consulta: puede ser un dato escrito antes de que la
            # columna se cifrara. Perder la pantalla entera por un registro
            # heredado seria peor que mostrarlo.
            return value


def generate_jwt(user, token_type="access", expires_minutes=15):
    now = int(time.time())
    jti = secrets.token_urlsafe(24)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "type": token_type,
        "jti": jti,
        "iat": now,
        "nbf": now,
        "exp": now + int(timedelta(minutes=expires_minutes).total_seconds()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    token = jwt.encode(payload, current_app.config["JWT_SECRET_KEY"], algorithm="HS256")
    return token, jti


def decode_jwt(token, required_type="access"):
    payload = jwt.decode(
        token,
        current_app.config["JWT_SECRET_KEY"],
        algorithms=["HS256"],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        leeway=30,
    )
    if payload.get("type") != required_type:
        raise jwt.InvalidTokenError("Invalid token type")
    return payload


def require_jwt(required_type="access"):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                abort(401)
            from models import JWTRevokedToken

            payload = decode_jwt(header.split(" ", 1)[1], required_type)
            if JWTRevokedToken.query.filter_by(jti=payload["jti"]).first():
                abort(401)
            request.jwt_payload = payload
            return func(*args, **kwargs)

        return wrapper

    return decorator


# Contraseñas prohibidas: las que aparecen primero en cualquier ataque de
# diccionario, más las variantes locales previsibles y las que quedaron expuestas
# en el repositorio.
_FORBIDDEN_PASSWORDS = frozenset({
    "password", "password1", "passw0rd", "contrasena", "contraseña", "contrasena1",
    "12345678", "123456789", "1234567890", "qwertyuiop", "1q2w3e4r", "abc12345",
    "admin123", "administrador", "superadmin", "ruralhealth", "ruralhealth1",
    "clinica123", "hospital123", "medico123", "doctor123", "salud123",
    "colombia123", "bogota123", "iloveyou", "welcome1", "letmein1",
    "0ufhheaewh2lefh7izftge1w", "-wklf6nxmdlk89a06l1zzeu",
})


def validate_password(password, username=None, name=None):
    """Valida una contraseña. Devuelve el mensaje de error, o None si es válida.

    El umbral es más alto que el habitual porque estas cuentas abren historias
    clínicas: una cuenta de médico comprometida expone a todos sus pacientes,
    no solo a quien la usa.
    """
    password = password or ""

    if len(password) < 12:
        return "La contrasena debe tener al menos 12 caracteres."
    if len(password) > 128:
        return "La contrasena no puede superar los 128 caracteres."
    if password.lower() == password or password.upper() == password:
        return "La contrasena debe combinar mayusculas y minusculas."
    if not any(ch.isdigit() for ch in password):
        return "La contrasena debe incluir al menos un numero."
    if not any(not ch.isalnum() for ch in password):
        return "La contrasena debe incluir al menos un simbolo (por ejemplo . , - _ # @)."

    lowered = password.lower()
    if lowered in _FORBIDDEN_PASSWORDS:
        return "Esa contrasena es demasiado comun. Elija una diferente."
    for forbidden in _FORBIDDEN_PASSWORDS:
        if len(forbidden) >= 8 and forbidden in lowered:
            return "La contrasena contiene una secuencia demasiado predecible."

    # El nombre de usuario o el nombre propio dentro de la contraseña la vuelve
    # adivinable por cualquiera que conozca a la persona.
    if username and len(username) >= 4 and username.lower() in lowered:
        return "La contrasena no puede contener el nombre de usuario."
    if name:
        for part in str(name).lower().split():
            if len(part) >= 4 and part in lowered:
                return "La contrasena no puede contener su nombre."

    # Repeticiones y secuencias: "aaaaaaaaaaaa", "123456789012".
    if re.search(r"(.)\1{3,}", password):
        return "La contrasena no puede repetir el mismo caracter cuatro veces seguidas."
    for sequence in ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiop"):
        for start in range(len(sequence) - 4):
            if sequence[start:start + 5] in lowered:
                return "La contrasena no puede contener secuencias consecutivas."

    return None


def validate_medical_code(code, code_type='CIE10'):
    """Validate CIE10 or CUPS codes against regex and existing database entries."""
    import re
    from models import CIE10, CUPS

    code = (code or '').strip().upper()
    if not code:
        return False, "El código no puede estar vacío."

    if code_type == 'CIE10':
        # CIE10: Letter followed by 2-3 digits, optional dot and more digits (e.g., A00, Z001, Z00.1)
        if not re.match(r'^[A-Z][0-9]{2,3}(\.[0-9]{1,2})?$', code):
            return False, f"Formato de código CIE10 '{code}' inválido."
        # Check if exists in DB (if the table has data)
        if CIE10.query.limit(1).first() and not CIE10.query.filter_by(code=code).first():
            return False, f"El código CIE10 '{code}' no existe en la base de datos oficial."
    
    elif code_type == 'CUPS':
        # CUPS: Usually 6 digits or alphanumeric string
        if not re.match(r'^[A-Z0-9]{6,10}$', code):
            return False, f"Formato de código CUPS '{code}' inválido."
        if CUPS.query.limit(1).first() and not CUPS.query.filter_by(code=code).first():
            return False, f"El código CUPS '{code}' no existe en la base de datos oficial."
            
    return True, code


def hash_password(password):
    return generate_password_hash(password, method=f"pbkdf2:sha256:{_kdf_iterations()}")


PASSWORD_HISTORY_LIMIT = 5


def check_password_reuse(user_id, new_password):
    """Return True if the password has been used recently (last 5 passwords)."""
    from models import PasswordHistory
    from werkzeug.security import check_password_hash as _check

    recent = PasswordHistory.query.filter_by(user_id=user_id).order_by(
        PasswordHistory.created_at.desc()
    ).limit(PASSWORD_HISTORY_LIMIT).all()
    return any(_check(entry.password_hash, new_password) for entry in recent)


def record_password_change(user_id, password_hash):
    """Store the current password hash in history."""
    from models import PasswordHistory, db

    db.session.add(PasswordHistory(user_id=user_id, password_hash=password_hash))
    # Prune old entries beyond the limit
    old_entries = PasswordHistory.query.filter_by(user_id=user_id).order_by(
        PasswordHistory.created_at.desc()
    ).offset(PASSWORD_HISTORY_LIMIT).all()
    for entry in old_entries:
        db.session.delete(entry)


def client_key(username=None):
    """Identificador del intento: origen + usuario, con hash.

    Se combinan ambos para que el bloqueo de una cuenta no deje fuera a otros
    usuarios legítimos que compartan la conexión del puesto de salud, y para que
    un atacante no pueda bloquear cuentas ajenas a voluntad desde una sola IP.

    Se guarda con hash porque `LoginAttempt` conserva filas de intentos fallidos:
    almacenar los nombres de usuario probados sería, en sí mismo, una lista de
    objetivos.
    """
    if username is None:
        username = request.form.get("username", "") if request else ""
    normalized = (username or "").strip().lower()
    origin = (request.remote_addr if request else None) or "unknown"
    pepper = _secret_material("RURALHEALTH_HASH_PEPPER", "dev-only-ruralhealth-pepper")
    return hmac.new(pepper, f"{origin}:{normalized}".encode("utf-8"), hashlib.sha256).hexdigest()


def _attempt_row(username=None, create=False):
    from models import LoginAttempt, db

    key = client_key(username)
    row = LoginAttempt.query.filter_by(attempt_key=key).first()
    if row is None and create:
        row = LoginAttempt(
            attempt_key=key,
            ip_address=(request.remote_addr if request else None),
            failure_count=0,
            first_failure_at=colombia_now(),
            last_failure_at=colombia_now(),
        )
        db.session.add(row)
    return row


def login_is_locked(username=None):
    """True si este origen y usuario están bloqueados ahora mismo.

    A diferencia del contador en memoria anterior, esto sobrevive a los reinicios
    y es común a todos los workers: reiniciar el proceso ya no reinicia el bloqueo.
    """
    row = _attempt_row(username)
    if not row or not row.locked_until:
        return False
    return colombia_now() < row.locked_until


def lock_remaining_seconds(username=None):
    row = _attempt_row(username)
    if not row or not row.locked_until:
        return 0
    remaining = (row.locked_until - colombia_now()).total_seconds()
    return max(0, int(remaining))


def record_login_failure(username=None):
    from models import db

    now = colombia_now()
    row = _attempt_row(username, create=True)

    # La ventana se reinicia si el último fallo quedó fuera del periodo de
    # observación: cinco fallos separados por meses no son un ataque.
    if (now - (row.first_failure_at or now)).total_seconds() > LOGIN_WINDOW_SECONDS:
        row.failure_count = 0
        row.first_failure_at = now
        row.locked_until = None

    row.failure_count = (row.failure_count or 0) + 1
    row.last_failure_at = now
    if row.failure_count >= LOGIN_LIMIT:
        row.locked_until = now + timedelta(seconds=LOGIN_LOCK_SECONDS)

    db.session.flush()
    return row


def clear_login_failures(username=None):
    from models import db

    row = _attempt_row(username)
    if row:
        db.session.delete(row)
        db.session.flush()


def purge_stale_login_attempts(older_than_days=30):
    """Elimina intentos antiguos ya desbloqueados. Llamado por mantenimiento."""
    from models import LoginAttempt, db

    cutoff = colombia_now() - timedelta(days=older_than_days)
    deleted = LoginAttempt.query.filter(
        LoginAttempt.last_failure_at < cutoff,
        (LoginAttempt.locked_until.is_(None)) | (LoginAttempt.locked_until < colombia_now()),
    ).delete(synchronize_session=False)
    db.session.commit()
    return deleted


def verify_password_constant_time(user, password):
    """Comprueba la contraseña en tiempo comparable exista o no el usuario.

    Cuando `user` es None se calcula igualmente un hash señuelo, para que el
    tiempo de respuesta no revele si la cuenta existe.
    """
    from werkzeug.security import check_password_hash as _check

    if user is None:
        try:
            _check(_DUMMY_PASSWORD_HASH, password or "")
        except Exception:
            # El hash señuelo no tiene por qué ser válido como tal; lo que importa
            # es haber consumido un tiempo comparable al de una verificación real.
            hashlib.pbkdf2_hmac(
                "sha256", (password or "").encode("utf-8"), b"decoy", _kdf_iterations()
            )
        return False
    return _check(user.password, password or "")


def generate_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def generate_order_hash(doctor_id, patient_id, meds_json=None, clinic_id=None, issued_at=None, expires_at=None):
    return generate_signed_order_hash(doctor_id, patient_id, meds_json, clinic_id, issued_at, expires_at)


def generate_signed_order_hash(doctor_id, patient_id, meds_json=None, clinic_id=None, issued_at=None, expires_at=None):
    secret = _secret_material("SECRET_KEY", "dev-only-ruralhealth-order-key")
    payload = {
        "doctor_id": int(doctor_id),
        "patient_id": int(patient_id),
        "clinic_id": int(clinic_id or 0),
        "issued_at": str(issued_at or colombia_now().isoformat()),
        "expires_at": str(expires_at or ""),
        "meds_digest": hashlib.sha256(str(meds_json or "").encode("utf-8")).hexdigest(),
        "nonce": secrets.token_urlsafe(12),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    signature = hmac.new(secret, encoded_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded_payload}.{signature}"


def verify_order_hash(doctor_id, patient_id, verification_hash, meds_json=None, clinic_id=None):
    token = str(verification_hash or "")
    if "." not in token:
        # Legacy support removed for security hardening
        return False

    secret = _secret_material("SECRET_KEY", "dev-only-ruralhealth-order-key")
    encoded_payload, supplied_signature = token.rsplit(".", 1)
    expected_signature = hmac.new(secret, encoded_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(expected_signature, supplied_signature):
        return False

    try:
        padded_payload = encoded_payload + "=" * (-len(encoded_payload) % 4)
        decoded = base64.urlsafe_b64decode(padded_payload.encode("utf-8")).decode("utf-8")
        
        if not decoded.startswith("{"):
            return False
            
        payload = json.loads(decoded)
        expected_meds_digest = hashlib.sha256(str(meds_json or "").encode("utf-8")).hexdigest()
        
        return (
            int(payload.get("doctor_id")) == int(doctor_id)
            and int(payload.get("patient_id")) == int(patient_id)
            and int(payload.get("clinic_id")) == int(clinic_id or 0)
            and hmac.compare_digest(payload.get("meds_digest", ""), expected_meds_digest)
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


def generate_pickup_hash(ticket_id, order_id, patient_id, clinic_id, pickup_code):
    secret = _secret_material("SECRET_KEY", "dev-only-ruralhealth-order-key")
    payload = {
        "ticket_id": int(ticket_id),
        "order_id": int(order_id or 0),
        "patient_id": int(patient_id),
        "clinic_id": int(clinic_id),
        "pickup_code": str(pickup_code),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    signature = hmac.new(secret, encoded_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded_payload}.{signature}"


def verify_pickup_hash(ticket, pickup_hash):
    if not ticket or "." not in str(pickup_hash or ""):
        return False
    encoded_payload, supplied_signature = str(pickup_hash).rsplit(".", 1)
    secret = _secret_material("SECRET_KEY", "dev-only-ruralhealth-order-key")
    expected_signature = hmac.new(secret, encoded_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, supplied_signature):
        return False
    try:
        padded_payload = encoded_payload + "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded_payload.encode("utf-8")).decode("utf-8"))
        return (
            int(payload.get("ticket_id")) == int(ticket.id)
            and int(payload.get("order_id")) == int(ticket.order_id or 0)
            and int(payload.get("patient_id")) == int(ticket.patient_id)
            and int(payload.get("clinic_id")) == int(ticket.clinic_id)
            and hmac.compare_digest(str(payload.get("pickup_code")), ticket.pickup_code)
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


# Endpoints exentos de CSRF, nombrados uno a uno.
#
# Antes bastaba con que la ruta empezara por "/api/" para quedar exenta, de modo
# que cualquier endpoint nuevo bajo ese prefijo nacía sin protección sin que nadie
# lo decidiera. Estos tres se autentican con credenciales o con un token Bearer
# enviado explícitamente, no con la cookie de sesión, así que no son alcanzables
# por CSRF.
CSRF_EXEMPT_ENDPOINTS = frozenset({
    "auth.issue_token",
    "auth.refresh_token",
    "auth.revoke_token",
})


def validate_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return

    expected = session.get("_csrf_token")
    supplied = request.form.get("_csrf_token") or request.headers.get("X-CSRFToken")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, description="Solicitud rechazada por proteccion CSRF.")


def rotate_csrf_token():
    """Emite un token CSRF nuevo. Se llama al iniciar y cerrar sesión."""
    token = secrets.token_urlsafe(32)
    session["_csrf_token"] = token
    return token


def audit(event, user_id=None, details=None, clinic_id=None):
    """Escribe una entrada de auditoría encadenada por hash.

    Cada entrada incluye el hash de la anterior, de modo que borrar o alterar una
    rompe la verificación de todas las siguientes. No impide la manipulación —
    nada lo hace desde dentro de la misma base de datos— pero la vuelve detectable,
    que es el requisito real de un registro de auditoría clínico.
    """
    from models import AuditLog, db

    actor_id = user_id if user_id is not None else (
        current_user.id if current_user and current_user.is_authenticated else None
    )
    if clinic_id is None and current_user and current_user.is_authenticated:
        clinic_id = getattr(current_user, "clinic_id", None)

    timestamp = colombia_now()
    user_agent = request.user_agent.string[:300] if request else None

    previous = (
        db.session.query(AuditLog.entry_hash)
        .order_by(AuditLog.id.desc())
        .limit(1)
        .first()
    )
    previous_hash = previous[0] if previous else None

    entry = AuditLog(
        user_id=actor_id,
        clinic_id=clinic_id,
        event=event,
        path=request.path if request else None,
        ip_address=request.remote_addr if request else None,
        user_agent=user_agent,
        details=(details or "")[:500] or None,
        timestamp=timestamp,
        previous_hash=previous_hash,
    )

    material = json.dumps(
        {
            "user_id": actor_id,
            "clinic_id": clinic_id,
            "event": event,
            "path": entry.path,
            "ip": entry.ip_address,
            "at": timestamp.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    entry.entry_hash = hashlib.sha256(
        f"{previous_hash or 'GENESIS'}|{material}".encode("utf-8")
    ).hexdigest()

    db.session.add(entry)
    return entry


def verify_audit_chain(limit=None):
    """Recorre el registro de auditoría y devuelve las entradas rotas."""
    from models import AuditLog

    query = AuditLog.query.order_by(AuditLog.id.asc())
    if limit:
        query = query.limit(limit)

    broken = []
    expected_previous = None
    for entry in query.all():
        if expected_previous is not None and entry.previous_hash != expected_previous:
            broken.append({
                "id": entry.id,
                "event": entry.event,
                "timestamp": entry.timestamp,
                "expected_previous": expected_previous,
                "found_previous": entry.previous_hash,
            })
        expected_previous = entry.entry_hash
    return broken


# --- Salidas tabulares -------------------------------------------------------

# Excel y LibreOffice interpretan como fórmula toda celda que empiece por estos
# caracteres. Un paciente cuyo nombre empiece por "=" convierte la exportación
# en código que se ejecuta en el equipo del auditor que la abre.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Neutraliza una celda para exportación tabular."""
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        text = "'" + text
    # CR y LF dentro de una celda rompen el alineamiento de filas en formatos planos.
    return text.replace("\r", " ").replace("\n", " ")


def csv_safe_row(values):
    return [csv_safe(value) for value in values]


def role_required(*roles):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            if not user_clinic_is_active():
                flash('La clínica está suspendida. Comuníquese con el administrador de la suscripción.')
                return redirect(url_for('auth.login'))
            return func(*args, **kwargs)

        return wrapper

    return decorator


def user_clinic_is_active():
    if not current_user.is_authenticated or current_user.role == "super":
        return True
    clinic = getattr(current_user, "clinic", None)
    return bool(clinic and clinic.status == "active")


def clinic_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)
        if current_user.role == "super":
            return func(*args, **kwargs)
        if not current_user.clinic_id:
            abort(403)
        if not user_clinic_is_active():
            flash('La clínica está suspendida por suscripción. Algunas funciones están bloqueadas.')
            return redirect(url_for('auth.login'))
        return func(*args, **kwargs)

    return wrapper


def clinic_storage_bytes(folder, clinic_id):
    """Bytes ocupados por los archivos de una clínica."""
    if not clinic_id:
        return 0
    clinic_folder = os.path.join(folder, f"clinic_{int(clinic_id)}")
    if not os.path.isdir(clinic_folder):
        return 0
    total = 0
    for root, _dirs, files in os.walk(clinic_folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def save_secure_upload(file_storage, folder, clinic_id=None, quota_mb=None):
    """Guarda un archivo subido tras validarlo.

    Comprueba, en este orden: extensión declarada, tipo MIME declarado, contenido
    real del archivo (los primeros bytes, no la extensión), tamaño y cuota de la
    clínica. El nombre original se descarta por completo: el archivo se almacena
    con un nombre aleatorio, así que un nombre malicioso no puede escapar del
    directorio ni sobrescribir nada.
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if ext not in ALLOWED_UPLOAD_EXTENSIONS or file_storage.mimetype not in ALLOWED_UPLOAD_MIMES:
        raise ValueError("Tipo de archivo no permitido. Se admiten JPG, PNG, WEBP y PDF.")

    header = file_storage.stream.read(2048)
    file_storage.stream.seek(0)
    actual_mime = magic.from_buffer(header, mime=True)
    if actual_mime not in ALLOWED_UPLOAD_MIMES:
        raise ValueError("El contenido del archivo no corresponde a un formato permitido.")

    # La extensión debe corresponder al contenido real: un ejecutable renombrado
    # a .png pasa la primera comprobación pero no esta.
    mime_extensions = {
        "image/jpeg": {"jpg", "jpeg"},
        "image/png": {"png"},
        "image/webp": {"webp"},
        "application/pdf": {"pdf"},
    }
    if ext not in mime_extensions.get(actual_mime, set()):
        raise ValueError("La extension del archivo no corresponde a su contenido real.")

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"El archivo supera el limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if size == 0:
        raise ValueError("El archivo esta vacio.")

    # Cuota por clínica: en un puesto de salud rural el disco es finito y llenarlo
    # deja al servicio sin capacidad de operar.
    if clinic_id and quota_mb is None and current_app:
        quota_mb = current_app.config.get("CLINIC_UPLOAD_QUOTA_MB")
    if clinic_id and quota_mb:
        used = clinic_storage_bytes(folder, clinic_id)
        if used + size > int(quota_mb) * 1024 * 1024:
            raise ValueError(
                f"La clinica alcanzo su cuota de almacenamiento de {quota_mb} MB. "
                "Comuniquese con el administrador para liberar espacio."
            )

    stored_name = f"{secrets.token_urlsafe(16)}.{ext}"
    if clinic_id:
        clinic_folder = f"clinic_{int(clinic_id)}"
        target_folder = os.path.join(folder, clinic_folder)
        os.makedirs(target_folder, exist_ok=True)
        stored_path = f"{clinic_folder}/{stored_name}"
    else:
        target_folder = folder
        stored_path = stored_name

    file_storage.save(os.path.join(target_folder, stored_name))
    return stored_path


def bootstrap_schema(db, create_tables=True):
    """Prepara el esquema y los datos de referencia mínimos.

    Lo que este arranque **ya no hace**, y antes sí:

    La versión anterior recorría en cada arranque las tablas `User`, `Chat`,
    `Message`, `Appointment`, `Rating`, `MedicalHistory`, `Stock`, `MedicalOrder`
    y `MedicationPickupTicket` **completas**, marcando cada campo cifrado como
    modificado para forzar su recifrado. Con datos reales eso convierte el
    arranque en una operación de minutos u horas, y cada worker de Gunicorn la
    repetía en paralelo contra la misma base. Además, el esquema evolucionaba con
    `ALTER TABLE` construido a mano, sin control de versión ni posibilidad de
    revertir.

    Ese trabajo se movió a `manage.py backfill`, que se ejecuta una vez, de forma
    explícita y con la aplicación detenida. La evolución del esquema pasó a
    Alembic (`flask db upgrade`).

    Args:
        create_tables: crear el esquema si falta. Solo para desarrollo y pruebas;
            en producción el esquema lo gobierna Alembic.
    """
    from models import CIE10, CUPS, Clinic, Pharmacy, RIPSReferenceCode, RetentionPolicy

    if create_tables:
        db.create_all()

    default_clinic = Clinic.query.order_by(Clinic.id.asc()).first()
    if not default_clinic:
        default_clinic = Clinic(
            name="Clinica Principal",
            legal_name="RuralHealth Connect",
            plan="starter",
            status="active",
        )
        db.session.add(default_clinic)
        db.session.flush()

    if not Pharmacy.query.filter_by(clinic_id=default_clinic.id).first():
        db.session.add(Pharmacy(
            clinic_id=default_clinic.id,
            name="Farmacia principal",
            address=default_clinic.location,
            is_active=True,
        ))

    seed_reference_data(db)
    db.session.commit()
    return default_clinic


def seed_reference_data(db):
    """Carga los catálogos mínimos si están vacíos.

    Los dos códigos CIE-10 y los dos CUPS son un arranque mínimo, no el catálogo
    oficial. `validate_medical_code` solo exige pertenencia al catálogo cuando la
    tabla tiene contenido, así que un catálogo incompleto rechazaría códigos
    válidos. Cargar el catálogo real es un paso de despliegue:

        python manage.py load-cie10 <archivo.csv>
        python manage.py load-cups  <archivo.csv>
    """
    from models import (CIE10, CUPS, NotifiableEvent, RIPSReferenceCode,
                        RetentionPolicy, SIVIGILA_INMEDIATA, SIVIGILA_SEMANAL)

    if not CIE10.query.limit(1).first():
        db.session.add_all([
            CIE10(code="Z000", description="Examen medico general"),
            CIE10(code="A00", description="Colera"),
        ])

    if not CUPS.query.limit(1).first():
        db.session.add_all([
            CUPS(code="890201", description="Consulta de primera vez por medicina general"),
            CUPS(code="890301", description="Consulta de control o seguimiento por medicina general"),
        ])

    # Tablas de referencia del RIPS. ATENCION: esta semilla es PARCIAL. Son las
    # entradas que pudieron verificarse contra el portal del Ministerio; cada
    # tabla tiene mas codigos. La carga completa se hace con
    # `manage.py load-rips-tables` contra el archivo oficial.
    #
    # Se incluyen aqui las de causa externa que importan clinicamente: sin
    # ellas no se puede distinguir un accidente de trabajo, uno de transito o
    # una lesion por agresion, que es justo lo que el generador anterior
    # borraba al reportar todo como enfermedad general.
    if not RIPSReferenceCode.query.limit(1).first():
        semilla = {
            'RIPSCausaExternaVersion2': [
                ('21', 'Accidente de trabajo'),
                ('22', 'Accidente en el hogar'),
                ('23', 'Accidente de transito de origen comun'),
                ('24', 'Accidente de transito de origen laboral'),
                ('25', 'Accidente en el entorno educativo'),
                ('26', 'Otro tipo de accidente'),
                ('27', 'Evento catastrofico de origen natural'),
                ('28', 'Lesion por agresion'),
                ('29', 'Lesion auto infligida'),
                ('30', 'Sospecha de violencia fisica'),
            ],
            'RIPSFinalidadConsultaVersion2': [
                ('11', 'Valoracion integral para la promocion y mantenimiento'),
                ('12', 'Deteccion temprana de enfermedad general'),
                ('13', 'Deteccion temprana de enfermedad laboral'),
                ('14', 'Proteccion especifica'),
                ('15', 'Diagnostico'),
                ('16', 'Tratamiento'),
                ('17', 'Rehabilitacion'),
                ('18', 'Paliacion'),
                ('19', 'Planificacion familiar y anticoncepcion'),
                ('20', 'Promocion y apoyo a la lactancia materna'),
            ],
            'ModalidadAtencion': [
                ('01', 'Intramural'),
                ('02', 'Extramural unidad movil'),
                ('03', 'Extramural domiciliaria'),
                ('04', 'Extramural jornada de salud'),
                ('06', 'Telemedicina interactiva'),
                ('07', 'Telemedicina no interactiva'),
                ('08', 'Telemedicina telexperticia'),
                ('09', 'Telemedicina telemonitoreo'),
            ],
            'RIPSTipoUsuarioVersion2': [
                ('01', 'Contributivo cotizante'),
                ('02', 'Contributivo beneficiario'),
                ('03', 'Contributivo adicional'),
                ('04', 'Subsidiado'),
                ('05', 'No afiliado'),
                ('06', 'Especial o excepcion cotizante'),
                ('07', 'Especial o excepcion beneficiario'),
                ('08', 'Persona privada de la libertad a cargo del Fondo Nacional de Salud'),
                ('09', 'Tomador o amparado ARL'),
                ('10', 'Tomador o amparado SOAT'),
            ],
            # OJO: la zona del RIPS va al reves que la del RDA. Aqui 01 es
            # Rural; en ColombianResidenceZone del IHCE, 01 es Urbana.
            'ZonaVersion2': [
                ('01', 'Rural'),
                ('02', 'Urbano'),
            ],
            'conceptoRecaudo': [
                ('01', 'Copago'),
                ('02', 'Cuota moderadora'),
                ('03', 'Pagos compartidos en planes voluntarios de salud'),
                ('04', 'Anticipo'),
                ('05', 'No aplica'),
            ],
            # Version2, de dos caracteres. La tabla anterior usaba un solo
            # digito; el anexo tecnico de la Resolucion 948 fija C 2.
            'RIPSTipoDiagnosticoPrincipalVersion2': [
                ('01', 'Impresion diagnostica'),
                ('02', 'Confirmado nuevo'),
                ('03', 'Confirmado repetido'),
            ],
        }
        for tabla, filas in semilla.items():
            for codigo, descripcion in filas:
                db.session.add(RIPSReferenceCode(
                    table_name=tabla, code=codigo, description=descripcion))

    # Catalogo de eventos de interes en salud publica (Decreto 3518 de 2006).
    #
    # ATENCION: semilla PARCIAL. El Instituto Nacional de Salud publica cada ano
    # el catalogo completo en sus lineamientos; aqui van los eventos de mayor
    # frecuencia en atencion primaria rural, que son los que no pueden pasar
    # inadvertidos. Cargue el catalogo completo antes de operar.
    #
    # Los prefijos CIE-10 son deliberadamente amplios: un falso positivo cuesta
    # que alguien revise y descarte; un falso negativo cuesta un brote sin
    # notificar.
    if not NotifiableEvent.query.limit(1).first():
        eventos = [
            # codigo, nombre, periodicidad, prefijos CIE-10
            ('210', 'Dengue', SIVIGILA_SEMANAL, 'A90,A91'),
            ('220', 'Dengue grave', SIVIGILA_INMEDIATA, 'A91'),
            ('465', 'Malaria', SIVIGILA_SEMANAL, 'B50,B51,B52,B53,B54'),
            ('813', 'Tuberculosis', SIVIGILA_SEMANAL, 'A15,A16,A17,A18,A19'),
            ('550', 'Mortalidad materna', SIVIGILA_INMEDIATA, 'O95,O96,O97'),
            ('591', 'Muerte en menor de cinco anos por IRA', SIVIGILA_INMEDIATA, 'J00,J06,J12,J18,J21,J22'),
            ('356', 'Intento de suicidio', SIVIGILA_INMEDIATA, 'X60,X61,X62,X63,X64,X65,X66,X67,X68,X69,X70,X71,X72,X73,X74,X75,X76,X77,X78,X79,X80,X81,X82,X83,X84'),
            ('875', 'Violencia de genero e intrafamiliar', SIVIGILA_INMEDIATA, 'T74,Y04,Y05,Y06,Y07,Z044'),
            ('300', 'Rabia humana y agresion por animal potencialmente transmisor', SIVIGILA_INMEDIATA, 'A82,W54,W55,W64'),
            ('110', 'Desnutricion aguda en menores de cinco anos', SIVIGILA_INMEDIATA, 'E40,E41,E42,E43,E44,E45,E46'),
            ('580', 'Morbilidad materna extrema', SIVIGILA_INMEDIATA, 'O14,O15,O72,O85'),
            ('420', 'Intoxicacion por sustancias quimicas', SIVIGILA_INMEDIATA, 'T36,T37,T38,T39,T40,T41,T42,T43,T44,T45,T46,T47,T48,T49,T50,T51,T52,T53,T54,T55,T56,T57,T58,T59,T60,T61,T62,T63,T64,T65'),
            ('370', 'Enfermedad transmitida por alimentos o agua', SIVIGILA_SEMANAL, 'A00,A01,A02,A03,A04,A05,A09'),
            ('340', 'Leishmaniasis', SIVIGILA_SEMANAL, 'B55'),
            ('217', 'Chikungunya', SIVIGILA_SEMANAL, 'A920'),
            ('895', 'Zika', SIVIGILA_SEMANAL, 'A928'),
            ('850', 'VIH/sida', SIVIGILA_SEMANAL, 'B20,B21,B22,B23,B24,Z21'),
            ('340', 'Sifilis gestacional y congenita', SIVIGILA_SEMANAL, 'A50,A51,A52,A53'),
        ]
        vistos = set()
        for codigo, nombre, periodicidad, prefijos in eventos:
            if codigo in vistos:
                continue
            vistos.add(codigo)
            db.session.add(NotifiableEvent(
                code=codigo, name=nombre, periodicity=periodicidad,
                cie10_prefixes=prefijos,
                notes='Semilla parcial. Verifique contra los lineamientos '
                      'vigentes del INS.'))

    # Plazos de conservación documental. Tenerlos en datos, y no en la memoria de
    # alguien, es lo que permite auditar que se respetan.
    if not RetentionPolicy.query.limit(1).first():
        db.session.add_all([
            RetentionPolicy(
                record_type="historia_clinica",
                retention_years=15,
                legal_basis="Resolucion 839 de 2017, articulo 2",
                notes=(
                    "Minimo 15 anos contados desde la ultima atencion: los "
                    "primeros 5 en archivo de gestion y los 10 siguientes en "
                    "archivo central."
                ),
            ),
            # La Resolucion 839 de 2017 duplica el termino para las historias de
            # victimas de violaciones de derechos humanos o de infracciones
            # graves al DIH. En una plataforma de salud rural colombiana esto no
            # es un caso de borde: es poblacion que va a estar en la base.
            # Cualquier rutina de depuracion que se escriba mas adelante tiene
            # que consultar estas filas antes de borrar nada.
            RetentionPolicy(
                record_type="historia_clinica_victima_ddhh",
                retention_years=30,
                legal_basis="Resolucion 839 de 2017, articulo 2, paragrafo",
                notes=(
                    "Victimas de violaciones de DDHH o de infracciones graves "
                    "al DIH: los terminos de retencion se duplican."
                ),
            ),
            RetentionPolicy(
                record_type="historia_clinica_lesa_humanidad",
                retention_years=0,
                legal_basis="Resolucion 839 de 2017, articulo 2, paragrafo",
                notes=(
                    "Conservacion PERMANENTE. Historia que forma parte de un "
                    "proceso por delitos de lesa humanidad. retention_years=0 "
                    "significa que no vence, no que se pueda borrar."
                ),
            ),
            RetentionPolicy(
                record_type="orden_medica",
                retention_years=15,
                legal_basis="Resolucion 839 de 2017",
                notes="Forma parte de la historia clinica.",
            ),
            RetentionPolicy(
                record_type="registro_dispensacion",
                retention_years=5,
                legal_basis="Resolucion 1403 de 2007",
                notes="Trazabilidad del servicio farmaceutico.",
            ),
            RetentionPolicy(
                record_type="registro_auditoria",
                retention_years=5,
                legal_basis="Ley 1581 de 2012, deber de demostrar cumplimiento",
            ),
            RetentionPolicy(
                record_type="consentimiento_informado",
                retention_years=15,
                legal_basis="Resolucion 839 de 2017",
            ),
        ])


def ensure_security_schema(db, create_tables=True):
    """Nombre conservado por compatibilidad. Delega en `bootstrap_schema`."""
    return bootstrap_schema(db, create_tables=create_tables)


def backfill_legacy_data(db, batch_size=500, progress=None):
    """Rellena los datos heredados de versiones anteriores del esquema.

    Se ejecuta **una vez**, de forma explícita, con `python manage.py backfill`.
    Procesa por lotes para no cargar toda la tabla en memoria y confirma cada
    lote, de modo que una interrupción no obliga a empezar de cero.

    Devuelve un resumen con lo corregido en cada entidad.
    """
    from models import (
        Appointment, Chat, Clinic, Favorite, MedicalHistory, MedicalOrder,
        MedicationPickupTicket, Message, Pharmacy, QuestionFlow, Rating,
        DoctorSchedule, Stock, User,
    )

    report = {}
    default_clinic = Clinic.query.order_by(Clinic.id.asc()).first()
    if not default_clinic:
        raise RuntimeError("No hay ninguna clinica registrada; ejecuta primero el arranque.")

    pharmacies = {}
    for clinic in Clinic.query.all():
        pharmacy = Pharmacy.query.filter_by(clinic_id=clinic.id).order_by(Pharmacy.id.asc()).first()
        if not pharmacy:
            pharmacy = Pharmacy(clinic_id=clinic.id, name="Farmacia principal",
                                address=clinic.location, is_active=True)
            db.session.add(pharmacy)
            db.session.flush()
        pharmacies[clinic.id] = pharmacy

    def announce(message):
        if progress:
            progress(message)

    # --- Usuarios ---
    fixed = 0
    last_id = 0
    announce("  Usuarios...")
    while True:
        rows = (User.query.filter(User.id > last_id).order_by(User.id.asc())
                .limit(batch_size).execution_options(include_all_clinics=True).all())
        if not rows:
            break
        for user in rows:
            last_id = user.id
            changed = False
            if user.clinic_id is None:
                user.clinic_id = default_clinic.id
                changed = True
            expected = pii_hash(user.cedula)
            if user.cedula and user.cedula_hash != expected:
                user.cedula_hash = expected
                changed = True
            if user.role == "expendedor" and not user.pharmacy_id and user.clinic_id in pharmacies:
                user.pharmacy_id = pharmacies[user.clinic_id].id
                changed = True
            if not user.affiliation_type:
                user.affiliation_type = "autonomous" if user.is_autonomous else "clinic"
                changed = True
            if user.created_at is None:
                user.created_at = colombia_now()
                changed = True
            if changed:
                fixed += 1
        db.session.commit()
    report["usuarios"] = fixed
    announce(f"  Usuarios: {fixed} corregido(s).")

    # --- Entidades con clinic_id derivable del paciente ---
    def backfill_clinic_id(model, label, owner_attr="patient_id"):
        fixed_local = 0
        cursor = 0
        announce(f"  {label}...")
        while True:
            rows = (model.query.filter(model.id > cursor, model.clinic_id.is_(None))
                    .order_by(model.id.asc()).limit(batch_size)
                    .execution_options(include_all_clinics=True).all())
            if not rows:
                break
            for row in rows:
                cursor = row.id
                owner_id = getattr(row, owner_attr, None)
                owner = User.query.filter_by(id=owner_id).execution_options(
                    include_all_clinics=True).first() if owner_id else None
                row.clinic_id = owner.clinic_id if owner else default_clinic.id
                fixed_local += 1
            db.session.commit()
        report[label] = fixed_local
        announce(f"  {label}: {fixed_local} corregido(s).")

    backfill_clinic_id(Chat, "chats")
    backfill_clinic_id(Appointment, "citas")
    backfill_clinic_id(Rating, "calificaciones")
    backfill_clinic_id(Favorite, "favoritos")
    backfill_clinic_id(MedicalHistory, "historias")
    backfill_clinic_id(MedicalOrder, "ordenes")
    backfill_clinic_id(MedicationPickupTicket, "tickets")
    backfill_clinic_id(QuestionFlow, "flujos", owner_attr="doctor_id")
    backfill_clinic_id(DoctorSchedule, "horarios", owner_attr="doctor_id")

    # --- Mensajes: heredan la clínica del chat ---
    fixed = 0
    cursor = 0
    announce("  Mensajes...")
    while True:
        rows = (Message.query.filter(Message.id > cursor, Message.clinic_id.is_(None))
                .order_by(Message.id.asc()).limit(batch_size)
                .execution_options(include_all_clinics=True).all())
        if not rows:
            break
        for message in rows:
            cursor = message.id
            if message.chat:
                message.clinic_id = message.chat.clinic_id
                fixed += 1
        db.session.commit()
    report["mensajes"] = fixed
    announce(f"  Mensajes: {fixed} corregido(s).")

    # --- Existencias ---
    fixed = 0
    cursor = 0
    announce("  Existencias...")
    while True:
        rows = (Stock.query.filter(Stock.id > cursor).order_by(Stock.id.asc())
                .limit(batch_size).execution_options(include_all_clinics=True).all())
        if not rows:
            break
        for item in rows:
            cursor = item.id
            changed = False
            if item.clinic_id is None:
                item.clinic_id = default_clinic.id
                changed = True
            if not item.medicamento:
                item.medicamento = item.nombre_med
                changed = True
            if item.cantidad_comprometida is None:
                item.cantidad_comprometida = 0
                changed = True
            if not item.pharmacy_id and item.clinic_id in pharmacies:
                item.pharmacy_id = pharmacies[item.clinic_id].id
                changed = True
            if changed:
                fixed += 1
        db.session.commit()
    report["existencias"] = fixed
    announce(f"  Existencias: {fixed} corregido(s).")

    # --- Órdenes: campos duplicados heredados ---
    fixed = 0
    cursor = 0
    announce("  Ordenes medicas...")
    while True:
        rows = (MedicalOrder.query.filter(MedicalOrder.id > cursor).order_by(MedicalOrder.id.asc())
                .limit(batch_size).execution_options(include_all_clinics=True).all())
        if not rows:
            break
        for order in rows:
            cursor = order.id
            changed = False
            if not order.hash_seguridad:
                order.hash_seguridad = order.verification_hash
                changed = True
            if not order.fecha_expiracion:
                order.fecha_expiracion = order.expires_at
                changed = True
            if changed:
                fixed += 1
        db.session.commit()
    report["ordenes_campos"] = fixed
    announce(f"  Ordenes medicas: {fixed} corregido(s).")

    return report


def encrypted_columns():
    """Descubre en el modelo qué columnas están cifradas.

    Antes esta lista estaba escrita a mano dentro de `reencrypt_all`, y se había
    quedado corta: cubría 10 campos de 30. Los otros 20 —entre ellos las
    alergias del paciente, los datos de la orden médica, el registro de
    dispensación y los nombres y apellidos— **no se recifraban**. Rotar la llave
    los habría dejado ilegibles en silencio, que es la peor forma de perder
    historia clínica: sin error, sin aviso, y descubierta meses después.

    Derivarla del propio modelo hace que no pueda volver a desincronizarse:
    añadir una columna cifrada la incluye automáticamente.
    """
    from sqlalchemy import inspect as sa_inspect

    import models

    encontrados = []
    for nombre in dir(models):
        modelo = getattr(models, nombre)
        if not isinstance(modelo, type) or not hasattr(modelo, "__tablename__"):
            continue
        try:
            mapper = sa_inspect(modelo)
        except Exception:
            continue
        campos = [c.key for c in mapper.columns
                  if isinstance(c.type, EncryptedText)]
        if campos and hasattr(modelo, "id"):
            encontrados.append((modelo, tuple(sorted(campos))))
    return sorted(encontrados, key=lambda par: par[0].__name__)


def reencrypt_all(db, batch_size=200, progress=None):
    """Recifra todos los campos cifrados con la llave activa.

    Necesario tras rotar `RURALHEALTH_FIELD_KEY_SEED`, y también para migrar del
    formato Fernet anterior a AES-256-GCM. Debe ejecutarse con la aplicación
    detenida y con copia de seguridad previa: si se interrumpe a medias, parte de
    los datos queda en el formato o la llave anteriores.

    Recifrar el `details` de la auditoría no rompe su cadena: el hash encadenado
    se calcula sobre el evento, el actor, la ruta, la IP y la marca de tiempo, no
    sobre ese campo.
    """
    targets = encrypted_columns()

    total = 0
    for model, fields in targets:
        label = model.__name__
        if progress:
            progress(f"  Recifrando {label}...")
        cursor = 0
        while True:
            rows = (model.query.filter(model.id > cursor).order_by(model.id.asc())
                    .limit(batch_size).execution_options(include_all_clinics=True).all())
            if not rows:
                break
            for row in rows:
                cursor = row.id
                touched = False
                for field in fields:
                    if getattr(row, field, None):
                        flag_modified(row, field)
                        touched = True
                if touched:
                    total += 1
            db.session.commit()
    return total


def flash_security_error(error):
    flash(str(error))
    return redirect(request.referrer or url_for("auth.login"))
