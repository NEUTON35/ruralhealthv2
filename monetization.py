# -*- coding: utf-8 -*-
"""Acceso de pago: tarifas, suscripciones, pólizas y códigos de acceso.

Qué se corrigió de la versión anterior
--------------------------------------
**Dinero en coma flotante.** `discounted_amount` calculaba el precio final con
`float`. `billing.py` ya usa enteros de centavos precisamente para evitarlo, así
que la aplicación tenía dos criterios distintos para el mismo peso: el que se le
muestra al paciente y el que se le factura. Ahora ambos usan `Decimal` con
redondeo bancario explícito, y el resultado se emite como entero de pesos, que es
como se cobra en Colombia.

**"ano" por "año".** `remaining_label` devolvía cadenas como «1 ano». En una
plataforma de salud, esa palabra sin la tilde significa otra cosa, y aparecía en
la pantalla de suscripción del paciente.

**Meses de treinta días.** El mismo `remaining_label` dividía los días entre 30 y
entre 365, de modo que a 364 días de vencer decía «12 meses y 4 días». Ahora
cuenta meses de calendario.

**Fallo abierto.** `is_record_active` daba por activo cualquier registro sin
atributo `status`. Un objeto al que le falta el campo por el que se decide el
acceso no es un objeto activo: es uno que no se puede evaluar.

**Códigos ambiguos.** `generate_human_code` pasaba a mayúsculas un token en
base64url, lo que colapsa `a` con `A` y deja dentro los caracteres que se
confunden al dictarlos. Estos códigos se leen por teléfono y se copian a mano en
un puesto de salud.
"""

import calendar
import secrets
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from sqlalchemy import or_

from models import (
    ACCESS_ACTIVE,
    CLINIC_ACCESS_PRIVATE,
    Clinic,
    DoctorTariff,
    PatientDoctorSubscription,
    PolicyNetworkProvider,
    UserClinicAccess,
    UserPolicyEnrollment,
)
from time_utils import colombia_now

PLAN_LABELS = {
    'per_consultation': 'Por consulta',
    'monthly': 'Mensual',
    'quarterly': 'Trimestral',
    'annual': 'Anual',
    'policy': 'Póliza',
}

PLAN_PRICE_FIELDS = {
    'per_consultation': 'price_per_consultation',
    'monthly': 'price_monthly',
    'quarterly': 'price_quarterly',
    'annual': 'price_annual',
}

# Duración de cada plan, en meses de calendario. `per_consultation` no está
# aquí porque no se mide en meses.
PLAN_MESES = {'monthly': 1, 'quarterly': 3, 'annual': 12}

# Un pago por consulta habilita el acceso durante un día. Es una decisión de
# negocio, no una constante técnica, así que vive con nombre.
HORAS_ACCESO_POR_CONSULTA = 24

# Alfabeto de los códigos que una persona lee en voz alta o copia a mano. Sin
# I, O, 0 ni 1: en una llamada, o dictados a alguien que anota, se confunden.
ALFABETO_CODIGO = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
LONGITUD_CODIGO = 10


def normalize_code(value):
    return (value or '').strip().upper()


def generate_human_code(prefix='RH'):
    """Código de acceso legible por una persona.

    La versión anterior tomaba `secrets.token_urlsafe` y lo pasaba a mayúsculas.
    Eso hace dos daños a la vez: colapsa mayúsculas y minúsculas, lo que reduce
    el espacio de códigos, y deja dentro los caracteres que se confunden al
    dictarlos.

    Con el alfabeto de 32 caracteres y diez posiciones quedan 32^10 (unos 10^15)
    combinaciones, tomadas de `secrets`, que es el generador apto para esto.
    Aun así, quien lo emita debe comprobar que no exista: la unicidad la
    garantiza el índice de la base de datos, no la probabilidad.
    """
    limpio = ''.join(ch for ch in str(prefix or 'RH').upper() if ch.isalnum())[:24] or 'RH'
    cuerpo = ''.join(secrets.choice(ALFABETO_CODIGO) for _ in range(LONGITUD_CODIGO))
    return f'{limpio}-{cuerpo}'


def add_months(start, months):
    """Suma meses de calendario conservando el día cuando existe.

    El 31 de enero más un mes es el 28 de febrero, no el 3 de marzo.
    """
    mes = start.month - 1 + months
    anio = start.year + mes // 12
    mes = mes % 12 + 1
    dia = min(start.day, calendar.monthrange(anio, mes)[1])
    return start.replace(year=anio, month=mes, day=dia)


def expiration_for_plan(plan_type, start=None, duration_days=None):
    start = start or colombia_now()
    if duration_days:
        return start + timedelta(days=max(1, int(duration_days)))
    if plan_type in PLAN_MESES:
        return add_months(start, PLAN_MESES[plan_type])
    if plan_type == 'per_consultation':
        return start + timedelta(hours=HORAS_ACCESO_POR_CONSULTA)
    return None


def is_record_active(record, now=None):
    """Si un registro de acceso está vigente.

    Falla cerrado. La versión anterior usaba `getattr(record, 'status',
    ACCESS_ACTIVE)`, de modo que un objeto sin el campo `status` se daba por
    activo. Un registro al que le falta justo el campo por el que se decide el
    acceso no es un registro activo: es uno que no se puede evaluar, y ante la
    duda no se abre la puerta.
    """
    if record is None:
        return False
    if getattr(record, 'status', None) != ACCESS_ACTIVE:
        return False
    now = now or colombia_now()
    starts_at = getattr(record, 'starts_at', None)
    expires_at = getattr(record, 'expires_at', None)
    return (starts_at is None or starts_at <= now) and (expires_at is None or expires_at > now)


def active_clinic_access(user_id, clinic_id, now=None):
    now = now or colombia_now()
    return UserClinicAccess.query.filter(
        UserClinicAccess.user_id == user_id,
        UserClinicAccess.clinic_id == clinic_id,
        UserClinicAccess.status == ACCESS_ACTIVE,
        or_(UserClinicAccess.starts_at.is_(None), UserClinicAccess.starts_at <= now),
        or_(UserClinicAccess.expires_at.is_(None), UserClinicAccess.expires_at > now),
    ).order_by(UserClinicAccess.expires_at.desc().nullsfirst()).first()


def clinic_access_allowed(user, clinic, now=None):
    if not user or not clinic or clinic.status != 'active':
        return False
    if getattr(user, 'role', None) != 'patient':
        return True
    if clinic.access_type != CLINIC_ACCESS_PRIVATE:
        return True
    return active_clinic_access(user.id, clinic.id, now=now) is not None


def clinic_access_state(user, clinic, now=None):
    access = active_clinic_access(user.id, clinic.id, now=now) if user and clinic else None
    allowed = clinic_access_allowed(user, clinic, now=now)
    return {
        'clinic': clinic,
        'allowed': allowed,
        'access': access,
        'locked': not allowed,
        'is_current': bool(user and clinic and user.clinic_id == clinic.id),
    }


def tariff_for_doctor(doctor):
    if not doctor:
        return None
    return DoctorTariff.query.filter_by(
        doctor_id=doctor.id).execution_options(include_all_clinics=True).first()


def active_doctor_subscription(patient_id, doctor_id, now=None):
    now = now or colombia_now()
    return PatientDoctorSubscription.query.filter(
        PatientDoctorSubscription.patient_id == patient_id,
        PatientDoctorSubscription.doctor_id == doctor_id,
        PatientDoctorSubscription.status == ACCESS_ACTIVE,
        or_(PatientDoctorSubscription.starts_at.is_(None),
            PatientDoctorSubscription.starts_at <= now),
        or_(PatientDoctorSubscription.expires_at.is_(None),
            PatientDoctorSubscription.expires_at > now),
    ).execution_options(include_all_clinics=True).order_by(
        PatientDoctorSubscription.expires_at.desc().nullsfirst()).first()


def best_policy_discount(user, doctor, now=None):
    """El mayor descuento aplicable entre las pólizas activas del paciente.

    Se resuelve en dos consultas y no en una por afiliación: un paciente con
    varias pólizas hacía tantas consultas como pólizas tuviera, y esto corre en
    la pantalla de agendamiento.

    Devuelve `(descuento, afiliación, prestador)`.
    """
    now = now or colombia_now()
    if not user or not doctor:
        return 0.0, None, None

    afiliaciones = UserPolicyEnrollment.query.filter(
        UserPolicyEnrollment.user_id == user.id,
        UserPolicyEnrollment.status == ACCESS_ACTIVE,
        or_(UserPolicyEnrollment.starts_at.is_(None),
            UserPolicyEnrollment.starts_at <= now),
        or_(UserPolicyEnrollment.expires_at.is_(None),
            UserPolicyEnrollment.expires_at > now),
    ).all()
    if not afiliaciones:
        return 0.0, None, None

    por_poliza = {a.policy_id: a for a in afiliaciones}
    prestadores = PolicyNetworkProvider.query.filter(
        PolicyNetworkProvider.policy_id.in_(list(por_poliza)),
        PolicyNetworkProvider.is_active.is_(True),
        or_(PolicyNetworkProvider.doctor_id == doctor.id,
            PolicyNetworkProvider.clinic_id == doctor.clinic_id),
    ).all()

    mejor = (0.0, None, None)
    for prestador in prestadores:
        descuento = float(prestador.discount_percent or 0)
        if descuento > mejor[0]:
            mejor = (descuento, por_poliza.get(prestador.policy_id), prestador)
    return mejor


def patient_has_doctor_access(patient, doctor, now=None):
    if not patient or not doctor:
        return False
    if doctor.is_autonomous:
        return active_doctor_subscription(patient.id, doctor.id, now=now) is not None
    clinic = Clinic.query.filter_by(id=doctor.clinic_id).first()
    return clinic_access_allowed(patient, clinic, now=now)


def price_for_plan(tariff, plan_type):
    if not tariff:
        return None
    field = PLAN_PRICE_FIELDS.get(plan_type)
    return getattr(tariff, field, None) if field else None


def discounted_amount(amount, discount_percent):
    """Precio final tras el descuento, en pesos enteros.

    Con `Decimal` y no con `float`. `billing.py` ya trabajaba en enteros de
    centavos por la misma razón, así que la aplicación tenía dos criterios para
    el mismo peso: el que se le muestra al paciente y el que se le factura.

    El peso colombiano no tiene subdivisión en circulación, de modo que el
    resultado se redondea a pesos enteros con redondeo bancario, que es el que
    no sesga sistemáticamente al alza ni a la baja cuando se suman muchos
    importes.
    """
    if amount is None:
        return None
    try:
        base = Decimal(str(amount))
        porcentaje = Decimal(str(discount_percent or 0))
    except (InvalidOperation, ValueError, TypeError):
        return None

    porcentaje = min(max(porcentaje, Decimal('0')), Decimal('100'))
    final = base * (Decimal('1') - porcentaje / Decimal('100'))
    return int(final.quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))


def remaining_label(expires_at, now=None):
    """Cuánto falta para vencer, en palabras.

    Cuenta meses de calendario. La versión anterior dividía los días entre 30 y
    entre 365, así que a 364 días de vencer decía «12 meses y 4 días».
    """
    if not expires_at:
        return 'Sin vencimiento'
    now = now or colombia_now()
    if expires_at <= now:
        return 'Vencido'

    meses = 0
    cursor = now
    while add_months(cursor, 1) <= expires_at:
        cursor = add_months(cursor, 1)
        meses += 1

    if meses >= 12:
        años = meses // 12
        resto = meses % 12
        texto = f'{años} año{"s" if años != 1 else ""}'
        if resto:
            texto += f' y {resto} mes{"es" if resto != 1 else ""}'
        return texto
    if meses:
        dias = (expires_at - cursor).days
        texto = f'{meses} mes{"es" if meses != 1 else ""}'
        if dias:
            texto += f' y {dias} día{"s" if dias != 1 else ""}'
        return texto

    restante = expires_at - now
    if restante.days:
        return f'{restante.days} día{"s" if restante.days != 1 else ""}'
    horas = restante.seconds // 3600
    if horas:
        return f'{horas} hora{"s" if horas != 1 else ""}'
    minutos = max(1, restante.seconds // 60)
    return f'{minutos} minuto{"s" if minutos != 1 else ""}'


def is_policy_subscription(subscription):
    return bool(subscription and getattr(subscription, 'source', None) == 'policy')


def tariff_prices(tariff, discount_percent=0):
    precios = []
    for plan_type, field in PLAN_PRICE_FIELDS.items():
        amount = getattr(tariff, field, None) if tariff else None
        if amount is None:
            continue
        precios.append({
            'plan_type': plan_type,
            'label': PLAN_LABELS.get(plan_type, plan_type),
            'amount': amount,
            'final_amount': discounted_amount(amount, discount_percent),
        })
    return precios
