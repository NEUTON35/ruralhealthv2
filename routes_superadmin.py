from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from models import (
    APPOINTMENT_FREEING_STATUSES,
    Appointment,
    Chat,
    Clinic,
    CLINIC_SUSPENDED,
    DoctorTariff,
    DoctorSchedule,
    Favorite,
    InventoryItem,
    Lead,
    MedicalHistory,
    MedicalOrder,
    MedicationPickupTicket,
    Message,
    PatientDoctorSubscription,
    Pharmacy,
    Policy,
    PolicyInviteBatch,
    PolicyInviteCode,
    PolicyNetworkProvider,
    QuestionFlow,
    Rating,
    ReplenishmentAlert,
    ROLE_CLINIC_ADMIN,
    ROLE_DOCTOR,
    ROLE_PATIENT,
    Stock,
    StockTransferRequest,
    User,
    UserClinicAccess,
    UserPolicyEnrollment,
    db,
)
from security import audit, hash_password, pii_hash, role_required, validate_password
from monetization import generate_human_code, normalize_code
from time_utils import colombia_now
import bleach

superadmin_bp = Blueprint('superadmin', __name__)


def _resolve_consent_code(consent_code):
    clinic = Clinic.query.filter_by(policy_consent_code=consent_code).first()
    if clinic:
        return clinic, None
    doctor = User.query.filter_by(policy_consent_code=consent_code, role=ROLE_DOCTOR).first()
    if doctor:
        return None, doctor
    return None, None


def _unique_policy_invite_code(policy_code):
    prefix = ''.join(ch for ch in policy_code.upper() if ch.isalnum())[:18] or 'POL'
    for _ in range(20):
        code = generate_human_code(prefix)
        if not PolicyInviteCode.query.filter_by(code=code).first():
            return code
    return generate_human_code(prefix)


def _split_consent_codes(raw_value):
    raw = (raw_value or '').replace(',', '\n').replace(';', '\n')
    return [normalize_code(item) for item in raw.splitlines() if normalize_code(item)]


def _archive_patient(patient):
    """Archiva a un paciente. **No borra su historia clinica.**

    La version anterior de esta funcion (`_delete_patient`) eliminaba de forma
    permanente `MedicalHistory`, `MedicalOrder`, `MedicationPickupTicket`, `Chat`
    y `Message`. Eso hacia dos cosas graves a la vez:

    1. Incumplia el deber de conservar la historia clinica un minimo de 15 anos
       (Resolucion 839 de 2017). Un clic de superadministrador destruia registros
       que el prestador esta obligado a custodiar, y que pueden ser la prueba en
       una reclamacion posterior.
    2. Contradecia lo que el propio sistema le dice al paciente en el centro de
       privacidad: que su historia no puede eliminarse ni aunque la solicite.

    Ademas dejaba huerfanos los asientos del libro mayor y las entradas de
    auditoria, que referencian al usuario.

    Lo que si hace: desactivar la cuenta, cerrar sus consultas abiertas, cancelar
    las citas futuras —liberando esos horarios para otros pacientes— y retirar los
    datos que no tienen deber de conservacion (favoritos, calificaciones).
    """
    now = colombia_now()

    patient.is_active_account = False
    patient.deactivated_at = now
    patient.sensitive_data_consent_at = None

    # Las consultas abiertas se cierran: dejarlas activas mantendria al paciente
    # en las bandejas del personal.
    for chat in Chat.query.filter_by(patient_id=patient.id).all():
        if chat.status == 'open':
            chat.status = 'closed'
            chat.closed_by = 'archivado'

    # Las citas futuras se cancelan y el horario queda libre.
    hoy = now.strftime('%Y-%m-%d')
    for cita in Appointment.query.filter(
        Appointment.patient_id == patient.id,
        Appointment.date >= hoy,
        Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
    ).all():
        cita.status = 'cancelada'

    # Preferencias sin valor clinico ni deber de conservacion.
    Favorite.query.filter_by(patient_id=patient.id).delete(synchronize_session=False)

    return {
        'historias': MedicalHistory.query.filter_by(patient_id=patient.id).count(),
        'ordenes': MedicalOrder.query.filter_by(patient_id=patient.id).count(),
        'consultas': Chat.query.filter_by(patient_id=patient.id).count(),
    }


def _archive_clinic(clinic):
    """Suspende una clinica y desactiva sus cuentas. **No borra sus datos.**

    Igual que con el paciente: la version anterior vaciaba todas las tablas de la
    clinica, incluidas las historias clinicas de todos sus pacientes. Una clinica
    que cierra sigue teniendo la obligacion de custodiar esos registros durante el
    plazo legal, y quien los reclame despues —un paciente, un ente de control—
    tiene derecho a que existan.
    """
    clinic.status = CLINIC_SUSPENDED
    clinic.activa = False

    afectados = User.query.filter(
        User.clinic_id == clinic.id, User.role != 'super'
    ).all()
    now = colombia_now()
    for usuario in afectados:
        usuario.is_active_account = False
        usuario.deactivated_at = now

    for chat in Chat.query.filter_by(clinic_id=clinic.id, status='open').all():
        chat.status = 'closed'
        chat.closed_by = 'clinica_archivada'

    hoy = now.strftime('%Y-%m-%d')
    for cita in Appointment.query.filter(
        Appointment.clinic_id == clinic.id,
        Appointment.date >= hoy,
        Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
    ).all():
        cita.status = 'cancelada'

    return {
        'usuarios': len(afectados),
        'historias': MedicalHistory.query.filter_by(clinic_id=clinic.id).count(),
    }


def _autonomous_clinic():
    clinic = Clinic.query.filter(Clinic.plan == 'autonomous', Clinic.status == 'active').first()
    if clinic:
        return clinic
    clinic = Clinic(
        name='Red de Medicos Autonomos',
        legal_name='Profesionales Autonomos RuralHealth',
        plan='autonomous',
        plan_pago='autonomous',
        status='active',
        activa=True,
    )
    db.session.add(clinic)
    db.session.flush()
    return clinic


@superadmin_bp.route('/', methods=['GET', 'POST'])
@login_required
@role_required('super')
def dashboard():
    if request.method == 'POST':
        action = request.form.get('action')
        clinic_id = request.form.get('clinic_id', type=int)
        clinic = db.session.get(Clinic, clinic_id) if clinic_id else None

        if action == 'create_clinic':
            clinic_name = bleach.clean((request.form.get('clinic_name') or '').strip())[:180]
            location = bleach.clean((request.form.get('location') or '').strip())[:220]
            latitude = request.form.get('latitude', type=float)
            longitude = request.form.get('longitude', type=float)
            plan_pago = (request.form.get('plan_pago') or 'starter').strip()[:80]
            access_type = request.form.get('access_type') if request.form.get('access_type') in {'public', 'private'} else 'public'
            admin_name = (request.form.get('admin_name') or '').strip()
            admin_username = (request.form.get('admin_username') or '').strip()
            admin_password = request.form.get('admin_password') or ''
            admin_cedula = (request.form.get('admin_cedula') or '').strip()
            admin_email = (request.form.get('admin_email') or '').strip()
            password_error = validate_password(admin_password)

            if not clinic_name or not admin_name or not admin_username or not admin_cedula:
                flash('Completa los datos de la clinica y del admin inicial.')
            elif password_error:
                flash(password_error)
            elif User.query.filter_by(username=admin_username).first():
                flash('El usuario admin ya existe.')
            elif User.query.filter_by(cedula_hash=pii_hash(admin_cedula)).first():
                flash('La cedula del admin ya esta registrada.')
            else:
                new_clinic = Clinic(
                    name=clinic_name,
                    legal_name=clinic_name,
                    location=location or None,
                    latitude=latitude,
                    longitude=longitude,
                    access_type=access_type,
                    plan=plan_pago,
                    plan_pago=plan_pago,
                    status='active',
                    activa=True,
                    contact_email=admin_email or None,
                )
                db.session.add(new_clinic)
                db.session.flush()
                admin = User(
                    clinic_id=new_clinic.id,
                    username=admin_username,
                    password=hash_password(admin_password),
                    role=ROLE_CLINIC_ADMIN,
                    name=admin_name,
                    cedula=admin_cedula,
                    cedula_hash=pii_hash(admin_cedula),
                    email=admin_email or None,
                )
                db.session.add(admin)
                audit('clinic_created_with_admin', details=f'clinic_id={new_clinic.id}; admin={admin_username}')
                db.session.commit()
                flash('Clinica y admin inicial creados.')

        elif clinic and action in {'suspend', 'activate'}:
            clinic.status = 'suspended' if action == 'suspend' else 'active'
            clinic.activa = action == 'activate'
            audit('clinic_status_changed', details=f'clinic_id={clinic.id}; status={clinic.status}')
            db.session.commit()
            flash(f'Clinica {clinic.name} actualizada a {clinic.status}.')

        elif clinic and action == 'delete_clinic':
            if User.query.filter_by(clinic_id=clinic.id, role='super').first():
                flash('No es posible archivar la clinica que contiene usuarios superadmin.')
                return redirect(url_for('superadmin.dashboard'))
            clinic_name = clinic.name
            resumen = _archive_clinic(clinic)
            audit(
                'clinic_archived',
                details=(f'clinic_id={clinic_id}; nombre={clinic_name}; '
                         f'usuarios={resumen["usuarios"]}; historias={resumen["historias"]}'),
            )
            db.session.commit()
            flash(
                f'Clinica {clinic_name} archivada: {resumen["usuarios"]} cuenta(s) '
                f'desactivada(s). Sus {resumen["historias"]} registro(s) clinico(s) '
                'se conservan por el plazo legal de 15 anos y no se eliminan.'
            )

        elif action == 'delete_patient':
            patient_id = request.form.get('patient_id', type=int)
            patient = db.session.get(User, patient_id) if patient_id else None
            if patient and patient.role == ROLE_PATIENT:
                resumen = _archive_patient(patient)
                audit(
                    'patient_archived_by_superadmin',
                    details=(f'patient_id={patient_id}; historias={resumen["historias"]}; '
                             f'ordenes={resumen["ordenes"]}'),
                )
                db.session.commit()
                flash(
                    'Paciente archivado: su cuenta queda desactivada. '
                    f'Sus {resumen["historias"]} registro(s) de historia clinica y '
                    f'{resumen["ordenes"]} orden(es) medica(s) se conservan por el '
                    'plazo legal de 15 anos.'
                )
            else:
                flash('Solo se pueden archivar cuentas de paciente.')

        elif action == 'create_autonomous_doctor':
            username = (request.form.get('username') or '').strip()
            password = request.form.get('password') or ''
            name = bleach.clean((request.form.get('name') or '').strip())
            cedula = (request.form.get('cedula') or '').strip()
            specialty = (request.form.get('specialty') or '').strip()[:150]
            email = (request.form.get('email') or '').strip()
            phone = (request.form.get('phone') or '').strip()
            office_address = (request.form.get('office_address') or '').strip()[:220]
            office_latitude = request.form.get('office_latitude', type=float)
            office_longitude = request.form.get('office_longitude', type=float)
            password_error = validate_password(password)
            if not username or not name or not cedula:
                flash('Completa nombre, usuario y cedula del medico autonomo.')
            elif password_error:
                flash(password_error)
            elif User.query.filter_by(username=username).first():
                flash('El usuario ya existe.')
            elif User.query.filter_by(cedula_hash=pii_hash(cedula)).first():
                flash('La cedula ya esta registrada.')
            else:
                clinic = _autonomous_clinic()
                doctor = User(
                    clinic_id=clinic.id,
                    username=username,
                    password=hash_password(password),
                    role=ROLE_DOCTOR,
                    name=name,
                    cedula=cedula,
                    cedula_hash=pii_hash(cedula),
                    specialty=specialty or None,
                    email=email or None,
                    phone=phone or None,
                    office_address=office_address or None,
                    office_latitude=office_latitude,
                    office_longitude=office_longitude,
                    show_office_on_map=bool(office_latitude and office_longitude),
                    is_autonomous=True,
                    affiliation_type='autonomous',
                    affiliation_name='Medico autonomo',
                )
                db.session.add(doctor)
                audit('autonomous_doctor_created', details=f'username={username}')
                db.session.commit()
                flash('Medico autonomo creado.')

        elif action == 'generate_policy_codes':
            policy_code = normalize_code(request.form.get('policy_code'))
            code_prefix = (request.form.get('code_prefix') or policy_code).strip()
            consent_codes = _split_consent_codes(request.form.get('consent_codes') or request.form.get('consent_code'))
            policy_name = (request.form.get('policy_name') or policy_code or 'Poliza').strip()[:180]
            insurer_name = (request.form.get('insurer_name') or '').strip()[:180] or None
            quantity = max(1, min(request.form.get('quantity', type=int) or 1, 50))
            duration_days = max(1, request.form.get('duration_days', type=int) or 30)
            discount_percent = min(max(request.form.get('discount_percent', type=float) or 0.0, 0.0), 100.0)

            if not policy_code or not consent_codes:
                flash('Ingrese el codigo de poliza y al menos un codigo de consentimiento.')
            else:
                policy = Policy.query.filter_by(code=policy_code).first()
                if not policy:
                    policy = Policy(code=policy_code, name=policy_name, insurer_name=insurer_name, is_active=True)
                    db.session.add(policy)
                    db.session.flush()
                else:
                    policy.name = policy_name or policy.name
                    policy.insurer_name = insurer_name or policy.insurer_name
                    policy.is_active = True

                providers = []
                missing_codes = []
                for consent_code in consent_codes:
                    clinic_target, doctor_target = _resolve_consent_code(consent_code)
                    if not clinic_target and not doctor_target:
                        missing_codes.append(consent_code)
                        continue
                    provider_discount = discount_percent
                    if doctor_target and not provider_discount:
                        tariff = DoctorTariff.query.filter_by(doctor_id=doctor_target.id).execution_options(include_all_clinics=True).first()
                        provider_discount = min(max((tariff.default_policy_discount_percent if tariff else 0.0) or 0.0, 0.0), 100.0)
                    provider = PolicyNetworkProvider.query.filter_by(
                        policy_id=policy.id,
                        clinic_id=clinic_target.id if clinic_target else None,
                        doctor_id=doctor_target.id if doctor_target else None,
                    ).first()
                    if not provider:
                        provider = PolicyNetworkProvider(
                            policy_id=policy.id,
                            clinic_id=clinic_target.id if clinic_target else None,
                            doctor_id=doctor_target.id if doctor_target else None,
                            consent_code=consent_code,
                            discount_percent=provider_discount,
                            is_active=True,
                        )
                        db.session.add(provider)
                        db.session.flush()
                    else:
                        provider.consent_code = consent_code
                        provider.discount_percent = provider_discount
                        provider.is_active = True
                    providers.append(provider)

                if not providers:
                    flash('Ningun codigo de consentimiento fue valido.')
                    db.session.rollback()
                    return redirect(url_for('superadmin.dashboard'))

                batch = PolicyInviteBatch.query.filter(
                    PolicyInviteBatch.policy_id == policy.id,
                    PolicyInviteBatch.consent_code == policy_code,
                    PolicyInviteBatch.generated_count < PolicyInviteBatch.max_codes,
                ).order_by(PolicyInviteBatch.id.desc()).first()
                if not batch:
                    batch = PolicyInviteBatch(
                        policy_id=policy.id,
                        provider_id=providers[0].id,
                        consent_code=policy_code,
                        max_codes=50,
                        generated_count=0,
                        duration_days=duration_days,
                        created_by_user_id=None,
                    )
                    db.session.add(batch)
                    db.session.flush()
                else:
                    batch.duration_days = duration_days
                    batch.provider_id = providers[0].id

                remaining = max(0, batch.max_codes - batch.generated_count)
                to_generate = min(quantity, remaining)
                generated_codes = []
                for _ in range(to_generate):
                    invite_code = _unique_policy_invite_code(code_prefix)
                    db.session.add(PolicyInviteCode(
                        batch_id=batch.id,
                        policy_id=policy.id,
                        code=invite_code,
                        status='unused',
                    ))
                    generated_codes.append(invite_code)
                batch.generated_count += to_generate
                audit('policy_invite_codes_generated', details=f'policy_id={policy.id}; batch_id={batch.id}; count={to_generate}; providers={len(providers)}')
                db.session.commit()
                if to_generate < quantity:
                    flash(f'Se generaron {to_generate} codigos porque el lote llego al limite de 50.')
                elif missing_codes:
                    flash(f'Codigos generados: {", ".join(generated_codes)}. No encontrados: {", ".join(missing_codes)}')
                else:
                    flash(f'Codigos generados: {", ".join(generated_codes)}')

        return redirect(url_for('superadmin.dashboard'))

    clinics = Clinic.query.order_by(Clinic.name.asc()).all()
    metrics = {
        'clinics_total': Clinic.query.count(),
        'clinics_active': Clinic.query.filter_by(status='active').count(),
        'clinics_suspended': Clinic.query.filter_by(status='suspended').count(),
        'users_total': User.query.count(),
        'autonomous_doctors': User.query.filter_by(role=ROLE_DOCTOR, is_autonomous=True).count(),
        'appointments_total': Appointment.query.count(),
        'chats_total': Chat.query.count(),
        'messages_total': Message.query.count(),
        'leads_total': Lead.query.count(),
        'policies_total': Policy.query.count(),
    }
    clinic_rows = []
    for clinic in clinics:
        clinic_rows.append({
            'clinic': clinic,
            'users': User.query.filter_by(clinic_id=clinic.id).count(),
            'doctors': User.query.filter_by(clinic_id=clinic.id, role='doctor').count(),
            'patients': User.query.filter_by(clinic_id=clinic.id, role='patient').count(),
            'appointments': Appointment.query.filter_by(clinic_id=clinic.id).count(),
            'chats': Chat.query.filter_by(clinic_id=clinic.id).count(),
        })
    leads = Lead.query.order_by(Lead.fecha.desc()).limit(50).all()
    patients = User.query.filter_by(role=ROLE_PATIENT).order_by(User.id.desc()).limit(100).all()
    autonomous_doctors = User.query.filter_by(role=ROLE_DOCTOR, is_autonomous=True).order_by(User.id.desc()).limit(100).all()
    policies = Policy.query.order_by(Policy.created_at.desc()).limit(25).all()
    policy_batches = PolicyInviteBatch.query.order_by(PolicyInviteBatch.created_at.desc()).limit(25).all()
    recent_policy_codes = PolicyInviteCode.query.order_by(PolicyInviteCode.created_at.desc()).limit(50).all()
    return render_template(
        'superadmin_dashboard.html',
        metrics=metrics,
        clinic_rows=clinic_rows,
        leads=leads,
        patients=patients,
        autonomous_doctors=autonomous_doctors,
        policies=policies,
        policy_batches=policy_batches,
        recent_policy_codes=recent_policy_codes,
    )
