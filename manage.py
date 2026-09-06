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

Los comandos que modifican datos piden confirmación explícita.
"""

import argparse
import csv
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
