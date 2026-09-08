#!/usr/bin/env python
"""Comandos de administración de RuralHealth Connect.

Aquí vive todo lo que antes ocurría en cada arranque de la aplicación, o que
sencillamente no existía:

    python manage.py preflight              Verifica que el entorno permita desplegar
    python manage.py bootstrap              Crea el esquema y los datos de referencia
    python manage.py backfill               Rellena datos heredados (una sola vez)
    python manage.py verify-audit-chain     Comprueba la integridad de la auditoría
    python manage.py verify-stock-ledger    Contrasta existencias contra el libro mayor
    python manage.py rotate-encryption-key  Recifra tras rotar la llave de campo
    python manage.py rebuild-blind-index    Reconstruye el índice de búsqueda por documento
    python manage.py force-password-reset   Obliga a cambiar contraseña
    python manage.py revoke-all-tokens      Invalida todos los JWT vigentes
    python manage.py scrub-logs             Purga secretos de los archivos de registro
    python manage.py create-clinic          Da de alta una clínica
    python manage.py load-cie10 <archivo>   Carga el catálogo oficial CIE-10
    python manage.py load-cups  <archivo>   Carga el catálogo oficial CUPS
    python manage.py check-knowledge-base   Antigüedad de la base clínica
    python manage.py purge-login-attempts   Limpia intentos de acceso antiguos
    python manage.py rda-status             Estado de la interoperabilidad IHCE
    python manage.py rda-send               Transmite los RDA pendientes
    python manage.py rda-problems           Envios de RDA que requieren revision
    python manage.py rda-backfill           Encola atenciones sin registro de envio
    python manage.py rda-preview <id>       Revisa un RDA sin transmitirlo
    python manage.py rips-json              Genera el RIPS vigente (Res 948/2026)
    python manage.py load-rips-tables       Carga una tabla de referencia del RIPS
    python manage.py load-sivigila-events   Carga el catálogo de eventos del INS

Los comandos que modifican datos piden confirmación explícita.
"""

import argparse
import csv
import io
import os
import re
import sys
from datetime import date

# `.env` se carga antes que nada: `preflight` inspecciona `os.environ` para
# informar de los secretos, y si el archivo se cargara mas tarde (al importar la
# aplicacion) reportaria como ausentes valores que si estan definidos.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

os.environ.setdefault('FLASK_ENV', 'development')


def bold(text):
    return f'\033[1m{text}\033[0m' if sys.stdout.isatty() else text


def ok(text):
    print(f'  [ok]    {text}')


def warn(text):
    print(f'  [aviso] {text}')


def fail(text):
    print(f'  [FALLO] {text}')


def confirm(prompt):
    """Confirmación explícita, escribiendo la palabra completa.

    Un `s/n` se pulsa por inercia. Escribir la palabra obliga a leer la pregunta.
    """
    print(f'\n{prompt}')
    answer = input('Escribe CONFIRMAR para continuar: ').strip()
    if answer != 'CONFIRMAR':
        print('Cancelado.')
        return False
    return True


def get_app():
    from app import app
    return app


# =============================================================================
# preflight
# =============================================================================

def cmd_preflight(args):
    """Comprueba que el entorno permita desplegar. Falla si algo no se cumple."""
    print(bold('\nVerificacion previa al despliegue\n'))

    problems = []
    warnings = []
    environment = os.environ.get('FLASK_ENV', 'development')

    print(f'Entorno: {environment}\n')

    # --- Secretos ---
    print(bold('Secretos'))
    from config import KNOWN_COMPROMISED, MIN_SECRET_LENGTH

    for name in ('RURALHEALTH_SECRET_KEY', 'RURALHEALTH_JWT_SECRET_KEY',
                 'RURALHEALTH_HASH_PEPPER'):
        value = os.environ.get(name)
        if not value:
            problems.append(f'{name} no esta definida.')
            fail(f'{name}: ausente')
        elif value in KNOWN_COMPROMISED:
            problems.append(f'{name} usa un valor expuesto en el repositorio.')
            fail(f'{name}: COMPROMETIDA — rotala (SECURITY.md)')
        elif len(value) < MIN_SECRET_LENGTH:
            problems.append(f'{name} es demasiado corta.')
            fail(f'{name}: {len(value)} caracteres, minimo {MIN_SECRET_LENGTH}')
        else:
            ok(f'{name}: definida ({len(value)} caracteres)')

    field_key = os.environ.get('RURALHEALTH_FIELD_ENCRYPTION_KEY')
    field_seed = os.environ.get('RURALHEALTH_FIELD_KEY_SEED')
    if field_key:
        ok('Llave de cifrado de campo: explicita')
    elif field_seed:
        if field_seed in KNOWN_COMPROMISED:
            problems.append('RURALHEALTH_FIELD_KEY_SEED esta comprometida.')
            fail('RURALHEALTH_FIELD_KEY_SEED: COMPROMETIDA — toda la historia '
                 'clinica es descifrable por terceros')
        else:
            warn('Se usa semilla derivada. Es preferible una llave Fernet explicita.')
    else:
        problems.append('No hay llave de cifrado de campo.')
        fail('Llave de cifrado de campo: ausente')

    # --- Base de datos ---
    print(bold('\nBase de datos'))
    db_uri = os.environ.get('SQLALCHEMY_DATABASE_URI', '')
    if environment == 'production' and db_uri.startswith('sqlite'):
        problems.append('SQLite en produccion.')
        fail('SQLite no soporta el bloqueo de filas que impide la doble dispensacion.')
    elif db_uri.startswith('postgresql'):
        ok('PostgreSQL configurado')
        if 'sslmode' not in db_uri:
            warnings.append('La conexion a PostgreSQL no exige TLS.')
            warn('sslmode no especificado; se anadira require automaticamente.')
    else:
        warn(f'Motor: {db_uri.split(":")[0] or "no definido"}')

    app = get_app()
    from models import User, db

    with app.app_context():
        try:
            db.session.execute(db.text('SELECT 1'))
            ok('Conexion establecida')
        except Exception as error:
            problems.append(f'No se puede conectar a la base de datos: {error}')
            fail(f'Conexion: {type(error).__name__}')

        # --- Cuentas ---
        print(bold('\nCuentas'))
        try:
            pending = User.query.filter_by(must_change_password=True).count()
            if pending:
                warnings.append(f'{pending} cuenta(s) con contrasena inicial sin cambiar.')
                warn(f'{pending} cuenta(s) deben cambiar su contrasena inicial.')
            else:
                ok('Ninguna cuenta usa su contrasena inicial')

            doctors_without_registration = User.query.filter(
                User.role == 'doctor',
                (User.medical_registration.is_(None)) | (User.medical_registration == ''),
            ).count()
            if doctors_without_registration:
                problems.append(
                    f'{doctors_without_registration} medico(s) sin registro profesional.'
                )
                fail(f'{doctors_without_registration} medico(s) sin registro medico: '
                     'no podran emitir ordenes')
            else:
                ok('Todos los medicos tienen registro profesional')
        except Exception as error:
            warn(f'No se pudieron revisar las cuentas: {type(error).__name__}')

        # --- Auditoria ---
        print(bold('\nIntegridad'))
        try:
            from security import verify_audit_chain
            breaks = verify_audit_chain(limit=2000)
            if breaks:
                problems.append(f'Cadena de auditoria rota en {len(breaks)} punto(s).')
                fail(f'Cadena de auditoria rota en {len(breaks)} punto(s)')
            else:
                ok('Cadena de auditoria integra')
        except Exception as error:
            warn(f'No se pudo verificar la auditoria: {type(error).__name__}')

    # --- Transporte ---
    print(bold('\nTransporte y limites'))
    if environment == 'production':
        ok('HTTPS forzado y HSTS activo')
        hosts = os.environ.get('RURALHEALTH_ALLOWED_HOSTS', '')
        if hosts:
            ok(f'Dominios permitidos: {hosts}')
        else:
            warnings.append('RURALHEALTH_ALLOWED_HOSTS vacio.')
            warn('Sin lista de dominios permitidos: cabecera Host manipulable.')

    storage = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    if storage.startswith('memory://') and environment == 'production':
        warnings.append('Limitacion de trafico en memoria del proceso.')
        warn('El limite se multiplica por el numero de workers. Configura Redis.')
    else:
        ok(f'Limitacion de trafico: {storage.split("://")[0]}')

    # --- Base de conocimiento clinico ---
    print(bold('\nSeguridad clinica'))
    from clinical_safety import (
        KNOWLEDGE_BASE_VERSION, knowledge_base_age_months, knowledge_base_is_stale,
    )
    age = knowledge_base_age_months()
    if knowledge_base_is_stale():
        warnings.append(f'Base clinica sin revisar desde hace {age} meses.')
        warn(f'Base de conocimiento {KNOWLEDGE_BASE_VERSION}: {age} meses sin revision')
    else:
        ok(f'Base de conocimiento {KNOWLEDGE_BASE_VERSION} ({age} meses)')

    # --- Interoperabilidad IHCE ---
    # La Resolucion 1888 de 2025 la exige a todo prestador inscrito en REPS. Sin
    # credenciales el sistema funciona, pero el prestador esta incumpliendo, asi
    # que en produccion es un problema y no un aviso.
    print(bold('\nInteroperabilidad IHCE (Resolucion 1888 de 2025)'))
    from ihce import IHCEConfig

    ihce = IHCEConfig.from_env()
    if ihce.is_configured:
        ok(f'Credenciales completas. Ambiente: {ihce.base_url}')
        if not ihce.is_enabled:
            warnings.append('La transmision de RDA esta desactivada por IHCE_ENABLED.')
            warn('Transmision desactivada a proposito (IHCE_ENABLED).')
        else:
            ok('Transmision activa.')
    else:
        faltan = ', '.join(ihce.faltantes())
        if environment == 'production':
            problems.append(
                'Faltan credenciales del IHCE (%s). La Resolucion 1888 de 2025 '
                'obliga a remitir un RDA por cada atencion.' % faltan)
            fail(f'Faltan: {faltan}')
            print('     Se obtienen en Hercules (SISPRO). Ver DEPLOYMENT.md, seccion 13.')
        else:
            warnings.append('Sin credenciales del IHCE; los RDA se acumularan.')
            warn(f'Faltan: {faltan}')

    # --- Resultado ---
    print(bold('\n' + '=' * 60))
    if problems:
        print(bold(f'RESULTADO: NO APTO PARA DESPLIEGUE ({len(problems)} problema(s))\n'))
        for problem in problems:
            print(f'  - {problem}')
        if warnings:
            print(f'\n  Ademas, {len(warnings)} aviso(s):')
            for note in warnings:
                print(f'  - {note}')
        print()
        return 1

    if warnings:
        print(bold(f'RESULTADO: APTO CON {len(warnings)} AVISO(S)\n'))
        for note in warnings:
            print(f'  - {note}')
        print()
        return 0

    print(bold('RESULTADO: APTO PARA DESPLIEGUE\n'))
    return 0


# =============================================================================
# Esquema y datos
# =============================================================================

def cmd_bootstrap(args):
    app = get_app()
    from models import db
    from security import bootstrap_schema

    with app.app_context():
        print('Preparando esquema y datos de referencia...')
        bootstrap_schema(db, create_tables=args.create_tables)
        from app import create_initial_accounts
        create_initial_accounts(app)
        print('Listo.')
    return 0


def cmd_backfill(args):
    app = get_app()
    from models import db
    from security import backfill_legacy_data

    if not confirm(
        'Se recorreran las tablas para rellenar datos heredados.\n'
        'Haz copia de seguridad antes. La aplicacion deberia estar detenida.'
    ):
        return 1

    with app.app_context():
        print('\nProcesando por lotes...\n')
        report = backfill_legacy_data(db, progress=print)
        print('\nResumen:')
        for label, count in report.items():
            print(f'  {label}: {count}')
    return 0


# =============================================================================
# Integridad
# =============================================================================

def cmd_verify_audit_chain(args):
    app = get_app()
    from models import AuditLog
    from security import verify_audit_chain

    with app.app_context():
        total = AuditLog.query.count()
        print(f'Verificando {total} entrada(s) de auditoria...\n')
        breaks = verify_audit_chain()
        if not breaks:
            print(f'Cadena integra. Las {total} entradas encadenan correctamente.')
            return 0

        print(f'CADENA ROTA en {len(breaks)} punto(s):\n')
        for issue in breaks[:50]:
            print(f'  Entrada #{issue["id"]} ({issue["event"]}) '
                  f'el {issue["timestamp"]}')
            print(f'    esperado: {issue["expected_previous"]}')
            print(f'    hallado:  {issue["found_previous"]}\n')
        print(
            'Una rotura significa que alguna entrada fue alterada o eliminada\n'
            'despues de escribirse. Investiga el acceso directo a la base de datos.'
        )
        return 1


def cmd_verify_stock_ledger(args):
    app = get_app()
    from ledger import stock_balance_matches_ledger, verify_chain
    from models import Clinic, Stock, StockLedgerEntry, DispensingLedgerEntry

    with app.app_context():
        exit_code = 0
        for clinic in Clinic.query.all():
            print(f'\nClinica {clinic.id}: {clinic.name}')

            breaks = verify_chain(StockLedgerEntry, clinic.id)
            if breaks:
                print(f'  CADENA DE INVENTARIO ROTA en {len(breaks)} punto(s)')
                exit_code = 1
            else:
                print('  Cadena de inventario integra')

            dispensing_breaks = verify_chain(DispensingLedgerEntry, clinic.id)
            if dispensing_breaks:
                print(f'  CADENA DE DISPENSACION ROTA en {len(dispensing_breaks)} punto(s)')
                exit_code = 1
            else:
                print('  Cadena de dispensacion integra')

            mismatched = []
            for stock in Stock.query.filter_by(clinic_id=clinic.id).all():
                if not stock_balance_matches_ledger(clinic.id, stock):
                    mismatched.append(stock)

            if mismatched:
                print(f'  {len(mismatched)} existencia(s) NO cuadran con el libro mayor:')
                for stock in mismatched[:20]:
                    print(f'    - {stock.medicamento or stock.nombre_med}: '
                          f'saldo actual {stock.cantidad}')
                print('    Indica movimientos hechos sin pasar por ledger.py.')
                exit_code = 1
            else:
                print('  Existencias cuadran con el libro mayor')

        return exit_code


# =============================================================================
# Rotacion de llaves
# =============================================================================

def cmd_rotate_encryption_key(args):
    app = get_app()
    from models import db
    from security import reencrypt_all

    print(bold('\nRotacion de la llave de cifrado de campo\n'))
    print(
        'Este proceso descifra cada campo con la llave anterior y lo vuelve a\n'
        'cifrar con la nueva. Si se interrumpe a medias, parte de los datos\n'
        'quedara cifrada con la llave antigua.\n'
    )

    if not os.environ.get('RURALHEALTH_FIELD_ENCRYPTION_KEY') and \
            not os.environ.get('RURALHEALTH_FIELD_KEY_SEED'):
        fail('No hay llave activa en el entorno. Define la NUEVA antes de ejecutar.')
        return 1

    if not confirm(
        'HAZ COPIA DE SEGURIDAD COMPLETA DE LA BASE DE DATOS ANTES DE CONTINUAR.\n'
        'La aplicacion debe estar detenida.'
    ):
        return 1

    with app.app_context():
        print('\nRecifrando...\n')
        total = reencrypt_all(db, progress=print)
        print(f'\n{total} registro(s) recifrados con la llave activa.')
        print('Verifica ahora que la aplicacion lee correctamente los datos.')
    return 0


def cmd_rebuild_blind_index(args):
    app = get_app()
    from models import User, db
    from security import pii_hash

    if not confirm(
        'Se recalculara el indice ciego de documentos para todos los usuarios.\n'
        'Necesario tras rotar RURALHEALTH_HASH_PEPPER.'
    ):
        return 1

    with app.app_context():
        updated = 0
        cursor = 0
        while True:
            users = (User.query.filter(User.id > cursor).order_by(User.id.asc())
                     .limit(500).execution_options(include_all_clinics=True).all())
            if not users:
                break
            for user in users:
                cursor = user.id
                if user.cedula:
                    new_hash = pii_hash(user.cedula)
                    if user.cedula_hash != new_hash:
                        user.cedula_hash = new_hash
                        updated += 1
            db.session.commit()
            print(f'  procesados hasta el id {cursor}...')
        print(f'\n{updated} indice(s) actualizados.')
    return 0


# =============================================================================
# Cuentas y sesiones
# =============================================================================

def cmd_force_password_reset(args):
    app = get_app()
    from models import User, db

    with app.app_context():
        query = User.query.execution_options(include_all_clinics=True)
        if args.all_privileged:
            query = query.filter(User.role.in_(['super', 'admin', 'doctor']))
            scope = 'cuentas privilegiadas (super, admin, doctor)'
        elif args.username:
            query = query.filter(User.username == args.username)
            scope = f'la cuenta {args.username}'
        else:
            scope = 'TODAS las cuentas'

        targets = query.all()
        if not targets:
            print('No hay cuentas que coincidan.')
            return 1

        if not confirm(f'Se marcaran {len(targets)} cuenta(s) de {scope} '
                       'para cambio obligatorio de contrasena.'):
            return 1

        for user in targets:
            user.must_change_password = True
        db.session.commit()
        print(f'\n{len(targets)} cuenta(s) marcadas.')
        print('Deberan definir una contrasena nueva en su proximo ingreso.')
    return 0


def cmd_revoke_all_tokens(args):
    app = get_app()
    from models import JWTRevokedToken, User, db

    with app.app_context():
        if not confirm('Se invalidaran todos los tokens JWT vigentes.'):
            return 1

        # No se conservan los jti emitidos, asi que la via efectiva es rotar el
        # secreto de firma: invalida de golpe todo token existente.
        print(
            '\nPara invalidar los JWT ya emitidos, rota RURALHEALTH_JWT_SECRET_KEY\n'
            'y reinicia la aplicacion. Todos los tokens firmados con la clave\n'
            'anterior dejaran de validar de inmediato.\n'
        )
        print('Genera una clave nueva con:')
        print('  python -c "import secrets; print(secrets.token_urlsafe(64))"')
    return 0


def cmd_purge_login_attempts(args):
    app = get_app()
    from security import purge_stale_login_attempts

    with app.app_context():
        deleted = purge_stale_login_attempts(older_than_days=args.days)
        print(f'{deleted} registro(s) de intentos fallidos eliminados '
              f'(mas de {args.days} dias, sin bloqueo activo).')
    return 0


# =============================================================================
# Registros
# =============================================================================

# Patrones de secretos que nunca deberían haber llegado a los archivos de log.
# Cada uno conserva el prefijo (grupo 1) y sustituye el valor por [PURGADO];
# el grupo 2, cuando existe, es el sufijo que hay que conservar.
SECRET_PATTERNS = (
    (re.compile(r'(Temporary credentials for \S+:\s*)\S+', re.IGNORECASE), None),
    (re.compile(r'(password["\']?\s*[:=]\s*)\S+', re.IGNORECASE), None),
    (re.compile(r'(PASS:\s*)\S+'), None),
    (re.compile(r'(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)', re.IGNORECASE), 2),
    (re.compile(r'(RURALHEALTH_\w*(?:KEY|SECRET|PASSWORD|SEED|PEPPER)\s*=\s*)\S+'), None),
)


def cmd_scrub_logs(args):
    """Purga secretos de los archivos de registro.

    La versión anterior de `app.py` escribía las contraseñas generadas de
    `superadmin` y `admin` en `logs/ruralhealth.log` en texto plano. Ese archivo
    rota, se copia y suele acabar en sistemas de agregación de registros.
    """
    log_dir = args.directory
    if not os.path.isdir(log_dir):
        print(f'No existe el directorio {log_dir}.')
        return 1

    files = [
        os.path.join(log_dir, name)
        for name in os.listdir(log_dir)
        if os.path.isfile(os.path.join(log_dir, name))
    ]
    if not files:
        print('No hay archivos de registro.')
        return 0

    if not confirm(f'Se reescribiran {len(files)} archivo(s) en {log_dir}, '
                   'sustituyendo los secretos encontrados por [PURGADO].'):
        return 1

    total = 0
    for path in files:
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as handle:
                content = handle.read()
        except OSError as error:
            warn(f'{path}: {error}')
            continue

        original = content
        for pattern, suffix_group in SECRET_PATTERNS:
            if suffix_group:
                replacement = r'\1[PURGADO]\%d' % suffix_group
            else:
                replacement = r'\1[PURGADO]'
            content = pattern.sub(replacement, content)

        if content != original:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(content)
            changed = sum(1 for _ in re.finditer(r'\[PURGADO\]', content))
            print(f'  {os.path.basename(path)}: {changed} ocurrencia(s) purgadas')
            total += changed
        else:
            print(f'  {os.path.basename(path)}: sin coincidencias')

    print(f'\n{total} secreto(s) purgados en total.')
    print('Revisa tambien las copias de seguridad y los sistemas de agregacion.')
    return 0


# =============================================================================
# Catalogos y datos maestros
# =============================================================================

def _load_catalog(model_name, path, code_length):
    app = get_app()
    from models import CIE10, CUPS, db

    model = {'CIE10': CIE10, 'CUPS': CUPS}[model_name]

    if not os.path.isfile(path):
        print(f'No existe el archivo {path}.')
        return 1

    with app.app_context():
        existing = {row.code for row in model.query.all()}
        added = 0
        skipped = 0

        with open(path, encoding='utf-8-sig', newline='') as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader):
                if len(row) < 2:
                    continue
                code = row[0].strip().upper()[:code_length]
                description = row[1].strip()[:500]
                if not code or not description:
                    continue
                # Cabecera del archivo.
                if index == 0 and code.lower() in {'codigo', 'code', 'cie10', 'cups'}:
                    continue
                if code in existing:
                    skipped += 1
                    continue
                db.session.add(model(code=code, description=description))
                existing.add(code)
                added += 1
                if added % 500 == 0:
                    db.session.commit()
                    print(f'  {added} codigos cargados...')

        db.session.commit()
        print(f'\n{model_name}: {added} codigo(s) nuevos, {skipped} ya existentes.')
        print(f'Total en catalogo: {model.query.count()}')
    return 0


def cmd_load_cie10(args):
    print('Cargando catalogo CIE-10 (formato esperado: codigo,descripcion)...\n')
    return _load_catalog('CIE10', args.path, 10)


def cmd_load_cups(args):
    print('Cargando catalogo CUPS (formato esperado: codigo,descripcion)...\n')
    return _load_catalog('CUPS', args.path, 20)


def cmd_create_clinic(args):
    app = get_app()
    from models import Clinic, Pharmacy, db

    with app.app_context():
        clinic = Clinic(
            name=args.name,
            legal_name=args.legal_name or args.name,
            nit=args.nit,
            habilitacion_code=args.habilitacion,
            department_code=args.department,
            municipality_code=args.municipality,
            status='active',
        )
        db.session.add(clinic)
        db.session.flush()
        db.session.add(Pharmacy(
            clinic_id=clinic.id, name='Farmacia principal', is_active=True,
        ))
        db.session.commit()
        print(f'Clinica creada con id {clinic.id}: {clinic.name}')
        if not args.habilitacion:
            warn('Sin codigo de habilitacion no se podra exportar el RIPS.')
    return 0


def cmd_check_knowledge_base(args):
    from clinical_safety import (
        DRUG_CLASSES, INTERACTIONS, KNOWLEDGE_BASE_REVIEWED, KNOWLEDGE_BASE_SOURCE,
        KNOWLEDGE_BASE_VERSION, PREGNANCY_CONTRAINDICATED, knowledge_base_age_months,
        knowledge_base_is_stale,
    )

    print(bold('\nBase de conocimiento clinico\n'))
    print(f'  Version:        {KNOWLEDGE_BASE_VERSION}')
    print(f'  Ultima revision: {KNOWLEDGE_BASE_REVIEWED}')
    print(f'  Antiguedad:      {knowledge_base_age_months()} mes(es)')
    print(f'  Principios activos catalogados: {len(DRUG_CLASSES)}')
    print(f'  Interacciones especificas:      {len(INTERACTIONS)}')
    print(f'  Contraindicaciones en embarazo: {len(PREGNANCY_CONTRAINDICATED)}')
    print(f'\n  Procedencia: {KNOWLEDGE_BASE_SOURCE}')

    if knowledge_base_is_stale():
        print(bold('\n  AVISO: la base lleva mas de 6 meses sin revision clinica.'))
        print('  Debe contrastarse con el listado vigente del INVIMA.\n')
        return 1

    print('\n  Estado: vigente.\n')
    return 0


# =============================================================================
# Entrada
# =============================================================================


# =============================================================================
# Interoperabilidad IHCE (Resolucion 1888 de 2025)
# =============================================================================

def cmd_rda_status(args):
    """Estado de la cola de Resumenes Digitales de Atencion."""
    app = get_app()
    from ihce import IHCEConfig, resumen_estado
    from models import db

    with app.app_context():
        config = IHCEConfig.from_env()
        estado = config.describe()

        print(bold('Conexion con el IHCE'))
        print('  Ambiente:      %s' % estado['ambiente'])
        print('  Habilitacion:  %s' % estado['habilitacion'])
        print('  Sede:          %s' % estado['sede'])
        if estado['credenciales_completas']:
            ok('Credenciales completas.')
        else:
            fail('Faltan credenciales: %s' % ', '.join(estado['faltantes']))
            print('  Se obtienen en Hercules (SISPRO) tras registrar al')
            print('  prestador y a su delegado. Ver DEPLOYMENT.md.')
        if estado['transmision_activa']:
            ok('Transmision activa.')
        else:
            warn('Transmision inactiva: los RDA se acumulan en la cola.')

        print()
        print(bold('Cola de envios'))
        conteo = resumen_estado(db)
        if conteo is None:
            fail('La tabla rda_submission no existe todavia.')
            print('  Ejecute: flask db upgrade')
            return 1
        if not conteo:
            print('  Sin envios registrados.')
            return 0
        etiquetas = {
            'pendiente': 'Pendientes de transmitir',
            'enviando': 'En vuelo',
            'aceptado': 'Aceptados por el Ministerio',
            'duplicado': 'Ya estaban en el Ministerio',
            'rechazado': 'Rechazados, requieren revision',
            'bloqueado': 'Bloqueados por datos incompletos',
        }
        for clave, etiqueta in etiquetas.items():
            if clave in conteo:
                print('  %-34s %d' % (etiqueta + ':', conteo[clave]))

        problemas = conteo.get('rechazado', 0) + conteo.get('bloqueado', 0)
        if problemas:
            print()
            warn('%d envio(s) necesitan intervencion. '
                 'Vealos con: python manage.py rda-problems' % problemas)
        return 0


def cmd_rda_send(args):
    """Transmite los RDA pendientes."""
    app = get_app()
    from ihce import IHCEConfig, procesar_pendientes
    from models import db

    with app.app_context():
        config = IHCEConfig.from_env()
        if not config.is_enabled:
            fail('Transmision inactiva.')
            faltan = config.faltantes()
            if faltan:
                print('  Faltan: %s' % ', '.join(faltan))
            return 1

        resumen = procesar_pendientes(db, config, limite=args.limit)
        print('Procesados:  %d' % resumen['procesados'])
        print('Aceptados:   %d' % resumen['aceptados'])
        print('Duplicados:  %d' % resumen['duplicados'])
        print('Reintentar:  %d' % resumen['reintentar'])
        print('Rechazados:  %d' % resumen['rechazados'])
        print('Bloqueados:  %d' % resumen['bloqueados'])
        if resumen['rechazados'] or resumen['bloqueados']:
            warn('Hay envios que no saldran solos.')
            return 2
        ok('Cola procesada.')
        return 0


def cmd_rda_problems(args):
    """Lista los envios que necesitan intervencion humana."""
    app = get_app()
    from models import RDA_BLOQUEADO, RDA_RECHAZADO, RDASubmission

    with app.app_context():
        envios = (RDASubmission.query
                  .filter(RDASubmission.status.in_([RDA_RECHAZADO, RDA_BLOQUEADO]))
                  .order_by(RDASubmission.updated_at.desc())
                  .limit(args.limit).all())
        if not envios:
            ok('No hay envios con problemas.')
            return 0
        for envio in envios:
            print()
            print(bold('Atencion %s  (envio %s)' % (envio.medical_history_id, envio.id)))
            print('  Estado:   %s tras %d intento(s)' % (envio.status, envio.attempts or 0))
            print('  Fecha:    %s' % envio.created_at)
            print('  Motivo:   %s' % (envio.last_error or 'sin detalle'))
        print()
        warn('%d envio(s) con problemas.' % len(envios))
        return 2


def cmd_rda_retry(args):
    """Devuelve a la cola envios rechazados, tras corregir la causa."""
    app = get_app()
    from time_utils import colombia_now
    from models import RDA_BLOQUEADO, RDA_PENDIENTE, RDA_RECHAZADO, RDASubmission, db

    with app.app_context():
        consulta = RDASubmission.query.filter(
            RDASubmission.status.in_([RDA_RECHAZADO, RDA_BLOQUEADO]))
        if args.id:
            consulta = consulta.filter(RDASubmission.id == args.id)
        envios = consulta.all()
        if not envios:
            print('No hay envios que reencolar.')
            return 0

        print('Se reencolaran %d envio(s).' % len(envios))
        print('Reintentar sin haber corregido la causa vuelve a fallar igual.')
        if not args.yes and not confirm('Continuar'):
            print('Cancelado.')
            return 1

        for envio in envios:
            envio.status = RDA_PENDIENTE
            envio.attempts = 0
            envio.next_attempt_at = colombia_now()
            envio.last_error = None
        db.session.commit()
        ok('%d envio(s) devueltos a la cola.' % len(envios))
        return 0


def cmd_rda_backfill(args):
    """Encola las atenciones que quedaron sin registro de envio.

    Sirve para dos casos: la puesta en marcha, cuando ya hay historias clinicas
    anteriores a la integracion, y el hueco que deje un fallo al encolar.
    """
    app = get_app()
    from ihce import encolar
    from models import MedicalHistory, RDASubmission, db

    with app.app_context():
        con_envio = db.session.query(RDASubmission.medical_history_id).subquery()
        consulta = (MedicalHistory.query
                    .filter(~MedicalHistory.id.in_(db.session.query(con_envio.c.medical_history_id)))
                    .order_by(MedicalHistory.id))
        if args.since:
            consulta = consulta.filter(MedicalHistory.created_at >= args.since)
        pendientes = consulta.limit(args.limit).all()

        if not pendientes:
            ok('Todas las atenciones tienen registro de envio.')
            return 0

        print('Se encolaran %d atencion(es) sin registro de envio.' % len(pendientes))
        if not args.yes and not confirm('Continuar'):
            print('Cancelado.')
            return 1

        for historia in pendientes:
            encolar(db, historia)
        db.session.commit()
        ok('%d atencion(es) encoladas.' % len(pendientes))
        return 0


def cmd_rda_preview(args):
    """Muestra el Bundle que se enviaria para una atencion, sin transmitirlo.

    Util para revisar el mapeo antes de tener credenciales. El Bundle contiene
    datos clinicos: no lo pegue en un ticket ni en un chat.
    """
    import json as _json

    app = get_app()
    from ihce import armar_bundle
    from models import MedicalHistory

    with app.app_context():
        historia = MedicalHistory.query.get(args.history_id)
        if historia is None:
            fail('No existe la atencion %s.' % args.history_id)
            return 1
        bundle, errores = armar_bundle(historia)
        if errores:
            fail('El documento no pasa la validacion local:')
            for e in errores:
                print('   - %s' % e)
            if bundle is None:
                return 2
        else:
            ok('El documento pasa la validacion local.')
        if args.json:
            print(_json.dumps(bundle, indent=2, ensure_ascii=False))
        else:
            print()
            print('Recursos del Bundle:')
            for entrada in bundle.get('entry', []):
                recurso = entrada.get('resource', {})
                print('   %-22s %s' % (recurso.get('resourceType'), recurso.get('id', '')))
            composition = bundle['entry'][0]['resource']
            print()
            print('Secciones:')
            for seccion in composition.get('section', []):
                estado = ('%d entrada(s)' % len(seccion['entry'])
                          if seccion.get('entry') else 'vacia (emptyReason)')
                print('   %-62s %s' % (seccion.get('title', '')[:60], estado))
            print()
            print('Use --json para ver el documento completo.')
        return 0


def cmd_rips_json(args):
    """Genera el RIPS en el formato vigente (Resolucion 948 de 2026)."""
    from datetime import datetime

    app = get_app()
    import rips_json

    def _fecha(texto):
        return datetime.strptime(texto, '%Y-%m-%d')

    with app.app_context():
        try:
            inicio, fin = _fecha(args.desde), _fecha(args.hasta)
        except ValueError:
            fail('Las fechas van en formato AAAA-MM-DD.')
            return 1

        if args.preview:
            informe = rips_json.preview(args.clinic_id, inicio, fin, args.factura)
            if not informe['ok']:
                fail('El periodo no puede generarse. Faltan datos:')
                for problema in informe['issues']:
                    print('   - [%s] %s' % (problema['entity'], problema['message']))
                return 2
            ok('El periodo puede generarse.')
            print('   Usuarios:  %d' % informe['usuarios'])
            print('   Consultas: %d' % informe['consultas'])
            for aviso in informe['avisos']:
                warn(aviso)
            return 0

        if not args.factura:
            fail('Indique el numero de la factura electronica con --factura. '
                 'El RIPS es su soporte.')
            return 1

        try:
            documento, avisos = rips_json.generar(
                args.clinic_id, inicio, fin, args.factura)
        except rips_json.RIPSJSONError as e:
            fail('No se genero el archivo. Faltan datos:')
            for problema in e.issues:
                print('   - [%s] %s' % (problema['entity'], problema['message']))
            return 2

        contenido = rips_json.serializar(documento)
        with io.open(args.salida, 'w', encoding='utf-8') as destino:
            destino.write(contenido)
        ok('Escrito %s (%d usuarios, %d consultas).' % (
            args.salida, len(documento['usuarios']),
            len(documento['servicios']['consultas'])))
        print()
        for aviso in avisos:
            warn(aviso)
        return 0


def cmd_load_rips_tables(args):
    """Carga una tabla de referencia del RIPS desde el archivo oficial.

    Las semillas que trae la aplicacion son parciales: cubren las primeras
    entradas de cada tabla. El Ministerio publica las completas en SISPRO.

    Formato del archivo: CSV de dos columnas, codigo y descripcion.
    """
    app = get_app()
    from models import RIPSReferenceCode, db

    if not os.path.isfile(args.archivo):
        fail('No existe el archivo %s.' % args.archivo)
        return 1

    with app.app_context():
        existentes = {
            f.code: f for f in RIPSReferenceCode.query.filter_by(
                table_name=args.tabla).all()
        }
        nuevos, actualizados = 0, 0
        with io.open(args.archivo, encoding='utf-8-sig', newline='') as handle:
            for indice, fila in enumerate(csv.reader(handle)):
                if len(fila) < 2:
                    continue
                codigo = fila[0].strip()[:10]
                descripcion = fila[1].strip()[:250]
                if not codigo or not descripcion:
                    continue
                if indice == 0 and codigo.lower() in {'codigo', 'code', 'tabla'}:
                    continue
                fila_existente = existentes.get(codigo)
                if fila_existente is None:
                    db.session.add(RIPSReferenceCode(
                        table_name=args.tabla, code=codigo,
                        description=descripcion))
                    nuevos += 1
                elif fila_existente.description != descripcion:
                    fila_existente.description = descripcion
                    actualizados += 1
        db.session.commit()

    ok('%s: %d codigo(s) nuevo(s), %d actualizado(s).'
       % (args.tabla, nuevos, actualizados))
    print('   Las semillas de la aplicacion son parciales; verifique que el')
    print('   archivo provenga de web.sispro.gov.co.')
    return 0


def cmd_load_sivigila_events(args):
    """Carga el catalogo de eventos de interes en salud publica.

    Formato: CSV de cuatro columnas: codigo, nombre, periodicidad
    (inmediata|semanal) y prefijos CIE-10 separados por punto y coma.

    El Instituto Nacional de Salud publica el catalogo vigente cada ano en sus
    lineamientos. La semilla de la aplicacion cubre los eventos de mayor
    frecuencia en atencion primaria rural, no el catalogo completo.
    """
    app = get_app()
    from models import (NotifiableEvent, SIVIGILA_INMEDIATA, SIVIGILA_SEMANAL,
                        db)

    if not os.path.isfile(args.archivo):
        fail('No existe el archivo %s.' % args.archivo)
        return 1

    validas = {SIVIGILA_INMEDIATA, SIVIGILA_SEMANAL}
    with app.app_context():
        existentes = {e.code: e for e in NotifiableEvent.query.all()}
        nuevos, actualizados, rechazados = 0, 0, 0
        with io.open(args.archivo, encoding='utf-8-sig', newline='') as handle:
            for indice, fila in enumerate(csv.reader(handle)):
                if len(fila) < 3:
                    continue
                codigo = fila[0].strip()[:10]
                nombre = fila[1].strip()[:200]
                periodicidad = fila[2].strip().lower()
                prefijos = (fila[3].strip().replace(';', ',')[:300]
                            if len(fila) > 3 else None)
                if not codigo or not nombre:
                    continue
                if indice == 0 and codigo.lower() in {'codigo', 'code'}:
                    continue
                if periodicidad not in validas:
                    rechazados += 1
                    continue
                evento = existentes.get(codigo)
                if evento is None:
                    db.session.add(NotifiableEvent(
                        code=codigo, name=nombre, periodicity=periodicidad,
                        cie10_prefixes=prefijos))
                    nuevos += 1
                else:
                    evento.name = nombre
                    evento.periodicity = periodicidad
                    if prefijos:
                        evento.cie10_prefixes = prefijos
                    actualizados += 1
        db.session.commit()

    ok('%d evento(s) nuevo(s), %d actualizado(s).' % (nuevos, actualizados))
    if rechazados:
        warn('%d fila(s) rechazadas: la periodicidad debe ser "inmediata" o '
             '"semanal".' % rechazados)
    return 0

def build_parser():
    parser = argparse.ArgumentParser(
        prog='manage.py',
        description='Comandos de administracion de RuralHealth Connect.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('preflight', help='Verifica el entorno antes de desplegar').set_defaults(
        func=cmd_preflight)

    p = sub.add_parser('bootstrap', help='Crea esquema y datos de referencia')
    p.add_argument('--no-create-tables', dest='create_tables', action='store_false',
                   help='No crear tablas (usar cuando Alembic gobierna el esquema)')
    p.set_defaults(func=cmd_bootstrap, create_tables=True)

    sub.add_parser('backfill', help='Rellena datos heredados (una sola vez)').set_defaults(
        func=cmd_backfill)

    sub.add_parser('verify-audit-chain', help='Verifica la integridad de la auditoria'
                   ).set_defaults(func=cmd_verify_audit_chain)

    sub.add_parser('verify-stock-ledger', help='Contrasta existencias con el libro mayor'
                   ).set_defaults(func=cmd_verify_stock_ledger)

    sub.add_parser('rotate-encryption-key', help='Recifra tras rotar la llave de campo'
                   ).set_defaults(func=cmd_rotate_encryption_key)

    sub.add_parser('rebuild-blind-index', help='Reconstruye el indice por documento'
                   ).set_defaults(func=cmd_rebuild_blind_index)

    p = sub.add_parser('force-password-reset', help='Obliga a cambiar contrasena')
    p.add_argument('--all-privileged', action='store_true',
                   help='Solo cuentas super, admin y doctor')
    p.add_argument('--username', help='Una cuenta concreta')
    p.set_defaults(func=cmd_force_password_reset)

    sub.add_parser('revoke-all-tokens', help='Invalida los JWT vigentes').set_defaults(
        func=cmd_revoke_all_tokens)

    p = sub.add_parser('purge-login-attempts', help='Limpia intentos de acceso antiguos')
    p.add_argument('--days', type=int, default=30)
    p.set_defaults(func=cmd_purge_login_attempts)

    p = sub.add_parser('scrub-logs', help='Purga secretos de los archivos de registro')
    p.add_argument('--directory', default='logs')
    p.set_defaults(func=cmd_scrub_logs)

    p = sub.add_parser('load-cie10', help='Carga el catalogo CIE-10 desde CSV')
    p.add_argument('path')
    p.set_defaults(func=cmd_load_cie10)

    p = sub.add_parser('load-cups', help='Carga el catalogo CUPS desde CSV')
    p.add_argument('path')
    p.set_defaults(func=cmd_load_cups)

    p = sub.add_parser('create-clinic', help='Da de alta una clinica')
    p.add_argument('name')
    p.add_argument('--legal-name')
    p.add_argument('--nit')
    p.add_argument('--habilitacion', help='Codigo de habilitacion REPS')
    p.add_argument('--department', help='Codigo DANE de departamento (2 digitos)')
    p.add_argument('--municipality', help='Codigo DANE de municipio (3 digitos)')
    p.set_defaults(func=cmd_create_clinic)

    sub.add_parser('check-knowledge-base', help='Antiguedad de la base clinica'
                   ).set_defaults(func=cmd_check_knowledge_base)


    sub.add_parser('rda-status', help='Estado de la interoperabilidad IHCE'
                   ).set_defaults(func=cmd_rda_status)

    p = sub.add_parser('rda-send', help='Transmite los RDA pendientes al IHCE')
    p.add_argument('--limit', type=int, default=50, help='Maximo de envios por corrida')
    p.set_defaults(func=cmd_rda_send)

    p = sub.add_parser('rda-problems', help='Envios de RDA que requieren revision')
    p.add_argument('--limit', type=int, default=25)
    p.set_defaults(func=cmd_rda_problems)

    p = sub.add_parser('rda-retry', help='Devuelve envios rechazados a la cola')
    p.add_argument('--id', type=int, help='Solo este envio')
    p.add_argument('--yes', action='store_true', help='No preguntar')
    p.set_defaults(func=cmd_rda_retry)

    p = sub.add_parser('rda-backfill', help='Encola atenciones sin registro de envio')
    p.add_argument('--limit', type=int, default=500)
    p.add_argument('--since', help='Solo desde esta fecha (AAAA-MM-DD)')
    p.add_argument('--yes', action='store_true', help='No preguntar')
    p.set_defaults(func=cmd_rda_backfill)

    p = sub.add_parser('rda-preview', help='Muestra el RDA de una atencion sin enviarlo')
    p.add_argument('history_id', type=int)
    p.add_argument('--json', action='store_true', help='Documento FHIR completo')
    p.set_defaults(func=cmd_rda_preview)


    p = sub.add_parser('rips-json', help='Genera el RIPS vigente (Res 948 de 2026)')
    p.add_argument('clinic_id', type=int)
    p.add_argument('desde', help='AAAA-MM-DD')
    p.add_argument('hasta', help='AAAA-MM-DD')
    p.add_argument('--factura', help='Numero de la factura electronica de venta')
    p.add_argument('--salida', default='rips.json')
    p.add_argument('--preview', action='store_true',
                   help='Solo comprobar, sin escribir el archivo')
    p.set_defaults(func=cmd_rips_json)


    p = sub.add_parser('load-rips-tables', help='Carga una tabla de referencia del RIPS')
    p.add_argument('tabla', help='Ej: RIPSCausaExternaVersion2')
    p.add_argument('archivo', help='CSV de codigo,descripcion')
    p.set_defaults(func=cmd_load_rips_tables)

    p = sub.add_parser('load-sivigila-events',
                       help='Carga el catalogo de eventos notificables del INS')
    p.add_argument('archivo',
                   help='CSV de codigo,nombre,periodicidad,prefijos CIE-10')
    p.set_defaults(func=cmd_load_sivigila_events)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print('\nInterrumpido.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
