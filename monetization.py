import calendar
import secrets
from datetime import timedelta

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
    'policy': 'Poliza',
}

PLAN_PRICE_FIELDS = {
    'per_consultation': 'price_per_consultation',
    'monthly': 'price_monthly',
    'quarterly': 'price_quarterly',
    'annual': 'price_annual',
}


def normalize_code(value):
    return (value or '').strip().upper()


def generate_human_code(prefix='RH'):
    clean_prefix = ''.join(ch for ch in str(prefix or 'RH').upper() if ch.isalnum())[:24] or 'RH'
    token = secrets.token_urlsafe(9).replace('-', '').replace('_', '').upper()[:12]
    return f'{clean_prefix}-{token}'


def add_months(start, months):
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def expiration_for_plan(plan_type, start=None, duration_days=None):
    start = start or colombia_now()
    if duration_days:
        return start + timedelta(days=max(1, int(duration_days)))
    if plan_type == 'monthly':
        return add_months(start, 1)
    if plan_type == 'quarterly':
        return add_months(start, 3)
    if plan_type == 'annual':
        return add_months(start, 12)
    if plan_type == 'per_consultation':
        return start + timedelta(days=1)
    return None


def is_record_active(record, now=None):
    now = now or colombia_now()
    if not record or getattr(record, 'status', ACCESS_ACTIVE) != ACCESS_ACTIVE:
        return False
    starts_at = getattr(record, 'starts_at', None)
    expires_at = getattr(record, 'expires_at', None)
    return (starts_at is None or starts_at <= now) and (expires_at is None or expires_at > now)


def active_clinic_access(user_id, clinic_id, now=None):
    now = now or colombia_now()
    return UserClinicAccess.query.filter(
        UserClinicAccess.user_id == user_id,
        UserClinicAccess.clinic_id == clinic_id,
        UserClinicAccess.status == ACCESS_ACTIVE,
        or_(UserClinicAccess.starts_at == None, UserClinicAccess.starts_at <= now),
        or_(UserClinicAccess.expires_at == None, UserClinicAccess.expires_at > now),
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
    return DoctorTariff.query.filter_by(doctor_id=doctor.id).execution_options(include_all_clinics=True).first()


def active_doctor_subscription(patient_id, doctor_id, now=None):
    now = now or colombia_now()
    return PatientDoctorSubscription.query.filter(
        PatientDoctorSubscription.patient_id == patient_id,
        PatientDoctorSubscription.doctor_id == doctor_id,
        PatientDoctorSubscription.status == ACCESS_ACTIVE,
        or_(PatientDoctorSubscription.starts_at == None, PatientDoctorSubscription.starts_at <= now),
        or_(PatientDoctorSubscription.expires_at == None, PatientDoctorSubscription.expires_at > now),
    ).execution_options(include_all_clinics=True).order_by(PatientDoctorSubscription.expires_at.desc().nullsfirst()).first()


def best_policy_discount(user, doctor, now=None):
    now = now or colombia_now()
    if not user or not doctor:
        return 0.0, None, None
    enrollments = UserPolicyEnrollment.query.filter(
        UserPolicyEnrollment.user_id == user.id,
        UserPolicyEnrollment.status == ACCESS_ACTIVE,
        or_(UserPolicyEnrollment.starts_at == None, UserPolicyEnrollment.starts_at <= now),
        or_(UserPolicyEnrollment.expires_at == None, UserPolicyEnrollment.expires_at > now),
    ).all()
    best = (0.0, None, None)
    for enrollment in enrollments:
        providers = PolicyNetworkProvider.query.filter(
            PolicyNetworkProvider.policy_id == enrollment.policy_id,
            PolicyNetworkProvider.is_active == True,
            or_(
                PolicyNetworkProvider.doctor_id == doctor.id,
                PolicyNetworkProvider.clinic_id == doctor.clinic_id,
            ),
        ).all()
        for provider in providers:
            discount = float(provider.discount_percent or 0)
            if discount > best[0]:
                best = (discount, enrollment, provider)
    return best


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
    if amount is None:
        return None
    discount = min(max(float(discount_percent or 0), 0.0), 100.0)
    return round(float(amount) * (1 - discount / 100), 2)


def remaining_label(expires_at, now=None):
    if not expires_at:
        return 'Sin vencimiento'
    now = now or colombia_now()
    delta = expires_at - now
    if delta.total_seconds() <= 0:
        return 'Vencido'
    days = delta.days
    hours = delta.seconds // 3600
    if days >= 365:
        years = days // 365
        return f'{years} ano{"s" if years != 1 else ""}'
    if days >= 30:
        months = days // 30
        remainder = days % 30
        return f'{months} mes{"es" if months != 1 else ""}' + (f' y {remainder} dias' if remainder else '')
    if days > 0:
        return f'{days} dia{"s" if days != 1 else ""}'
    return f'{max(1, hours)} hora{"s" if hours != 1 else ""}'


def is_policy_subscription(subscription):
    return bool(subscription and subscription.source == 'policy')


def tariff_prices(tariff, discount_percent=0):
    prices = []
    for plan_type, field in PLAN_PRICE_FIELDS.items():
        amount = getattr(tariff, field, None) if tariff else None
        if amount is None:
            continue
        prices.append({
            'plan_type': plan_type,
            'label': PLAN_LABELS.get(plan_type, plan_type),
            'amount': amount,
            'final_amount': discounted_amount(amount, discount_percent),
        })
    return prices
