import re
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, logout_user
from werkzeug.security import check_password_hash

from models import (
    ACCESS_ACTIVE,
    APPOINTMENT_FREEING_STATUSES,
    BillingProfile,
    DataSubjectRequest,
    ISSUER_CLINIC,
    ISSUER_INDEPENDENT,
    LegalConfiguration,
    Appointment,
    Chat,
    Clinic,
    ClinicAccessCode,
    DoctorSchedule,
    Favorite,
    PatientDoctorSubscription,
    PolicyInviteCode,
    PolicyNetworkProvider,
    QuestionFlow,
    Rating,
    User,
    UserClinicAccess,
    UserPolicyEnrollment,
    db,
)
from billing import describe_readiness, profile_for_doctor
from legal_documents import PLACEHOLDER_LABELS, pending_configuration
from security import audit, check_password_reuse, hash_password, record_password_change, save_secure_upload, validate_password
from time_utils import colombia_now
import bleach
from monetization import (
    active_clinic_access,
    active_doctor_subscription,
    clinic_access_allowed,
    clinic_access_state,
    expiration_for_plan,
    generate_human_code,
    normalize_code,
)

settings_bp = Blueprint('settings', __name__)


def _unique_consent_code(prefix):
    for _ in range(10):
        code = generate_human_code(prefix)
        if not Clinic.query.filter_by(policy_consent_code=code).first() and not User.query.filter_by(policy_consent_code=code).first():
            return code
    return generate_human_code(prefix)


def _grant_clinic_access(user, clinic_id, source, expires_at=None, code_id=None, policy_enrollment_id=None):
    existing = active_clinic_access(user.id, clinic_id)
    if existing and (existing.expires_at is None or not expires_at or existing.expires_at >= expires_at):
        return existing
    access = UserClinicAccess(
        user_id=user.id,
        clinic_id=clinic_id,
        source=source,
        status=ACCESS_ACTIVE,
        code_id=code_id,
        policy_enrollment_id=policy_enrollment_id,
        starts_at=colombia_now(),
        expires_at=expires_at,
    )
    db.session.add(access)
    return access


@settings_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'change_password':
            current_pass = request.form.get('current_password') or ''
            new_pass = request.form.get('new_password') or ''
            confirm_pass = request.form.get('confirm_password') or ''
            # La validacion recibe el usuario y el nombre para rechazar
            # contrasenas que los contengan: son adivinables por cualquiera que
            # conozca a la persona.
            password_error = validate_password(
                new_pass, username=current_user.username, name=current_user.name
            )

            if not check_password_hash(current_user.password, current_pass):
                audit('password_change_failed', details='contrasena actual incorrecta')
                db.session.commit()
                flash('Tu contrasena actual es incorrecta.')
            elif password_error:
                flash(password_error)
            elif new_pass != confirm_pass:
                flash('Las contrasenas nuevas no coinciden.')
            elif new_pass == current_pass:
                flash('La nueva contrasena debe ser distinta de la actual.')
            elif check_password_reuse(current_user.id, new_pass):
                flash('No puedes reutilizar una de tus ultimas 5 contrasenas.')
            else:
                record_password_change(current_user.id, current_user.password)
                current_user.password = hash_password(new_pass)
                current_user.password_changed_at = colombia_now()
                # Levanta el bloqueo de navegacion impuesto a las cuentas que
                # todavia usaban su contrasena inicial.
                current_user.must_change_password = False
                audit('password_changed')
                db.session.commit()
                flash('Contrasena actualizada correctamente.')
                return redirect(url_for('settings.index'))

        elif action == 'profile_location':
            if request.form.get('accept_location') != 'on':
                flash('Debes autorizar el uso de ubicacion para guardar coordenadas.')
                return redirect(url_for('settings.index'))
            current_user.address = bleach.clean((request.form.get('address') or '').strip()[:220]) or None
            current_user.latitude = request.form.get('latitude', type=float)
            current_user.longitude = request.form.get('longitude', type=float)
            current_user.location_consent_at = colombia_now()
            audit('profile_location_updated')
            db.session.commit()
            flash('Direccion actualizada.')

        elif action == 'switch_clinic' and current_user.role == 'patient':
            clinic_id = request.form.get('clinic_id', type=int)
            clinic = db.session.get(Clinic, clinic_id) if clinic_id else None
            if not clinic or clinic.status != 'active':
                flash('Clinica no disponible.')
            elif not clinic_access_allowed(current_user, clinic):
                flash('Esta clinica esta bloqueada. Redime un codigo valido para acceder.')
            else:
                current_user.clinic_id = clinic.id
                session['clinic_id'] = clinic.id
                audit('patient_switched_clinic', details=f'clinic_id={clinic.id}')
                db.session.commit()
                flash(f'Clinica activa: {clinic.name}')

        elif action == 'redeem_clinic_code' and current_user.role == 'patient':
            code_value = normalize_code(request.form.get('clinic_code'))
            access_code = ClinicAccessCode.query.filter_by(code=code_value).first()
            now = colombia_now()
            if not access_code or not access_code.is_active:
                flash('Codigo de clinica invalido.')
            elif access_code.expires_at and access_code.expires_at <= now:
                flash('Este codigo de clinica ya vencio.')
            elif access_code.uses >= access_code.max_uses:
                flash('Este codigo ya alcanzo su limite de usos.')
            elif not access_code.clinic or access_code.clinic.status != 'active':
                flash('La clinica asociada no esta activa.')
            elif active_clinic_access(current_user.id, access_code.clinic_id):
                current_user.clinic_id = access_code.clinic_id
                session['clinic_id'] = access_code.clinic_id
                db.session.commit()
                flash(f'Ya tenias acceso activo. Clinica activa: {access_code.clinic.name}.')
            else:
                expires_at = expiration_for_plan(None, now, access_code.duration_days)
                _grant_clinic_access(
                    current_user,
                    access_code.clinic_id,
                    source='code',
                    expires_at=expires_at,
                    code_id=access_code.id,
                )
                access_code.uses += 1
                current_user.clinic_id = access_code.clinic_id
                session['clinic_id'] = access_code.clinic_id
                audit('clinic_access_code_redeemed', details=f'clinic_id={access_code.clinic_id}; code_id={access_code.id}')
                db.session.commit()
                flash(f'Acceso activado para {access_code.clinic.name}.')

        elif action == 'redeem_policy_code' and current_user.role == 'patient':
            code_value = normalize_code(request.form.get('policy_code'))
            invite = PolicyInviteCode.query.filter_by(code=code_value, status='unused').first()
            if not invite or not invite.policy or not invite.policy.is_active:
                flash('Codigo de poliza invalido o ya usado.')
            else:
                now = colombia_now()
                document_path = None
                policy_file = request.files.get('policy_file')
                if policy_file and policy_file.filename:
                    try:
                        document_path = save_secure_upload(policy_file, current_app.config['UPLOAD_FOLDER'], current_user.clinic_id)
                    except ValueError as error:
                        flash(str(error))
                        return redirect(url_for('settings.index'))
                expires_at = expiration_for_plan(None, now, invite.batch.duration_days if invite.batch else 30)
                enrollment = UserPolicyEnrollment(
                    user_id=current_user.id,
                    policy_id=invite.policy_id,
                    invite_code_id=invite.id,
                    status=ACCESS_ACTIVE,
                    starts_at=now,
                    expires_at=expires_at,
                    policy_document_path=document_path,
                )
                db.session.add(enrollment)
                db.session.flush()
                invite.status = 'redeemed'
                invite.redeemed_by_user_id = current_user.id
                invite.redeemed_at = now

                providers = PolicyNetworkProvider.query.filter_by(policy_id=invite.policy_id, is_active=True).all()
                unlocked_clinics = 0
                unlocked_doctors = 0
                for provider in providers:
                    if provider.clinic_id:
                        _grant_clinic_access(
                            current_user,
                            provider.clinic_id,
                            source='policy',
                            expires_at=expires_at,
                            policy_enrollment_id=enrollment.id,
                        )
                        unlocked_clinics += 1
                    if provider.doctor_id and not active_doctor_subscription(current_user.id, provider.doctor_id):
                        doctor = db.session.get(User, provider.doctor_id)
                        if doctor:
                            db.session.add(PatientDoctorSubscription(
                                clinic_id=doctor.clinic_id,
                                patient_id=current_user.id,
                                doctor_id=doctor.id,
                                source='policy',
                                plan_type='policy',
                                status=ACCESS_ACTIVE,
                                starts_at=now,
                                expires_at=expires_at,
                                policy_enrollment_id=enrollment.id,
                            ))
                            unlocked_doctors += 1
                audit('policy_code_redeemed', details=f'policy_id={invite.policy_id}; clinics={unlocked_clinics}; doctors={unlocked_doctors}')
                db.session.commit()
                flash(f'Poliza activada. Desbloqueo {unlocked_clinics} clinicas y {unlocked_doctors} medicos.')

        elif action == 'delete_account':
            # Cierre de cuenta a peticion del titular.
            #
            # Antes esto borraba de verdad: chats, citas y calificaciones, y luego
            # la fila del usuario. Dos problemas.
            #
            # Uno legal: los chats son historia clinica y hay deber de conservarla
            # 15 anos (Resolucion 839 de 2017). El titular puede pedir la supresion,
            # pero ese deber prevalece (Ley 1581, articulo 9).
            #
            # Y uno de honestidad: la pantalla decia "eliminada permanentemente",
            # que es justo lo contrario de lo que el centro de privacidad de la
            # misma aplicacion le explica al paciente. Ahora dice lo que ocurre.
            confirmation = request.form.get('confirm_text')
            if confirmation != 'CERRAR':
                flash('Escribe CERRAR para confirmar el cierre de tu cuenta.')
            else:
                now = colombia_now()
                user_id = current_user.id

                current_user.is_active_account = False
                current_user.deactivated_at = now
                current_user.sensitive_data_consent_at = None
                current_user.is_available = False

                if current_user.role == 'doctor':
                    # La disponibilidad se retira para que no aparezca en las
                    # busquedas; los flujos y horarios dejan de ofrecerse.
                    DoctorSchedule.query.filter_by(doctor_id=user_id).delete()
                    QuestionFlow.query.filter_by(doctor_id=user_id).delete()
                elif current_user.role == 'patient':
                    Favorite.query.filter_by(patient_id=user_id).delete()

                columna = Chat.doctor_id if current_user.role == 'doctor' else Chat.patient_id
                for chat in Chat.query.filter(columna == user_id, Chat.status == 'open').all():
                    chat.status = 'closed'
                    chat.closed_by = 'cuenta_cerrada'

                columna_cita = (Appointment.doctor_id if current_user.role == 'doctor'
                                else Appointment.patient_id)
                hoy = now.strftime('%Y-%m-%d')
                for cita in Appointment.query.filter(
                    columna_cita == user_id,
                    Appointment.date >= hoy,
                    Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
                ).all():
                    cita.status = 'cancelada'

                # La solicitud queda registrada, que es lo que permite acreditar
                # ante la autoridad que se atendio.
                db.session.add(DataSubjectRequest(
                    user_id=user_id,
                    clinic_id=current_user.clinic_id,
                    request_type='supresion',
                    status='atendida',
                    detail='Cierre de cuenta solicitado por el titular.',
                    resolution_note=(
                        'Cuenta desactivada y tratamientos no obligatorios detenidos. '
                        'La historia clinica se conserva por el plazo legal de 15 anos '
                        '(Resolucion 839 de 2017).'
                    ),
                    requested_at=now,
                    resolved_at=now,
                    requester_ip=request.remote_addr,
                ))

                audit('account_closed_by_owner', user_id=user_id)
                db.session.commit()
                flash(
                    'Tu cuenta fue cerrada y ya no podras iniciar sesion. '
                    'Tu historia clinica se conserva porque la ley obliga al '
                    'prestador a guardarla 15 anos; nadie la usara para nuevas '
                    'atenciones. Si necesitas una copia, pidela antes de cerrar.'
                )
                logout_user()
                return redirect(url_for('auth.login'))

        elif action == 'clinic_settings' and current_user.role == 'admin':
            if current_user.clinic:
                current_user.clinic.opening_hours = bleach.clean((request.form.get('opening_hours') or '')[:2000])
                current_user.clinic.contact_email = bleach.clean((request.form.get('contact_email') or '')[:180])
                current_user.clinic.nit = bleach.clean((request.form.get('nit') or '')[:50])
                current_user.clinic.location = bleach.clean((request.form.get('location') or '').strip()[:220]) or None
                current_user.clinic.latitude = request.form.get('latitude', type=float)
                current_user.clinic.longitude = request.form.get('longitude', type=float)
                access_type = request.form.get('access_type')
                if access_type in {'public', 'private'}:
                    current_user.clinic.access_type = access_type
                logo = request.files.get('logo')
                if logo and logo.filename:
                    try:
                        current_user.clinic.logo_path = save_secure_upload(logo, current_app.config['UPLOAD_FOLDER'], current_user.clinic_id)
                    except ValueError as error:
                        flash(str(error))
                        return redirect(url_for('settings.index'))
                audit('clinic_settings_updated')
                db.session.commit()
                flash('Configuración de clínica actualizada.')

        elif action == 'generate_clinic_consent' and current_user.role == 'admin':
            if current_user.clinic:
                prefix = (request.form.get('consent_prefix') or current_user.clinic.name or 'CLINIC').strip()[:24]
                current_user.clinic.policy_consent_code = _unique_consent_code(prefix)
                current_user.clinic.policy_consent_generated_at = colombia_now()
                audit('clinic_policy_consent_generated')
                db.session.commit()
                flash('Codigo de consentimiento de poliza generado.')

        elif action == 'doctor_professional' and current_user.role == 'doctor':
            registration = bleach.clean((request.form.get('medical_registration') or '').strip()[:50])
            # El registro medico profesional es lo que da validez legal a la
            # orden que firma. Sin el, el sistema no permite prescribir.
            if registration and not re.match(r'^[A-Za-z0-9\-\.\s]{4,50}$', registration):
                flash('El numero de registro medico tiene caracteres no validos.')
            else:
                previous = current_user.medical_registration
                current_user.medical_registration = registration or None
                current_user.specialty = bleach.clean(
                    (request.form.get('specialty') or '').strip()[:150]
                ) or None
                # El cambio de registro profesional queda auditado: es el dato
                # que identifica al responsable de cada orden emitida.
                audit(
                    'doctor_professional_updated',
                    details=f'registro_previo={"si" if previous else "no"}',
                )
                db.session.commit()
                flash('Datos profesionales actualizados.')

        elif action == 'clinic_rips_identity' and current_user.role == 'admin':
            # Datos del prestador exigidos por RIPS. Sin el codigo de habilitacion
            # del REPS la exportacion no puede generarse: el NIT no lo sustituye.
            if current_user.clinic:
                current_user.clinic.habilitacion_code = bleach.clean(
                    (request.form.get('habilitacion_code') or '').strip()[:20]
                ) or None
                dept = (request.form.get('department_code') or '').strip()[:2]
                muni = (request.form.get('municipality_code') or '').strip()[:3]
                current_user.clinic.department_code = dept if dept.isdigit() else None
                current_user.clinic.municipality_code = muni if muni.isdigit() else None
                audit('clinic_rips_identity_updated')
                db.session.commit()
                flash('Identificacion del prestador actualizada.')

        elif action == 'billing_profile' and current_user.role in ('doctor', 'admin'):
            # Perfil de facturacion.
            #
            # El medico independiente factura a su propio nombre, con su propia
            # resolucion de numeracion de la DIAN. Mezclar su consecutivo con el
            # de la clinica invalidaria ambos: cada resolucion autoriza un rango
            # a un emisor concreto.
            es_independiente = (current_user.role == 'doctor'
                                and current_user.is_autonomous)

            if current_user.role == 'doctor' and not es_independiente:
                flash('Los medicos vinculados facturan bajo el perfil de la clinica.')
                return redirect(url_for('settings.index'))

            if es_independiente:
                perfil = BillingProfile.query.filter_by(
                    doctor_id=current_user.id, issuer_kind=ISSUER_INDEPENDENT).first()
                if not perfil:
                    perfil = BillingProfile(
                        issuer_kind=ISSUER_INDEPENDENT,
                        doctor_id=current_user.id,
                        clinic_id=current_user.clinic_id,
                        legal_name=current_user.name,
                        document_type='CC',
                        document_number='',
                    )
                    db.session.add(perfil)
            else:
                perfil = BillingProfile.query.filter_by(
                    clinic_id=current_user.clinic_id, issuer_kind=ISSUER_CLINIC).first()
                if not perfil:
                    perfil = BillingProfile(
                        issuer_kind=ISSUER_CLINIC,
                        clinic_id=current_user.clinic_id,
                        legal_name=(current_user.clinic.legal_name
                                    or current_user.clinic.name),
                        document_type='NIT',
                        document_number=current_user.clinic.nit or '',
                    )
                    db.session.add(perfil)

            perfil.legal_name = bleach.clean(
                (request.form.get('legal_name') or perfil.legal_name or '').strip())[:220]
            tipo_doc = (request.form.get('document_type') or perfil.document_type or 'NIT').strip().upper()
            perfil.document_type = tipo_doc if tipo_doc in ('NIT', 'CC') else 'NIT'
            perfil.document_number = re.sub(
                r'[^0-9]', '', request.form.get('document_number') or '')[:40]
            perfil.verification_digit = re.sub(
                r'[^0-9]', '', request.form.get('verification_digit') or '')[:1] or None
            perfil.fiscal_address = bleach.clean(
                (request.form.get('fiscal_address') or '').strip())[:300] or None
            perfil.email = bleach.clean((request.form.get('billing_email') or '').strip())[:180] or None
            perfil.phone = bleach.clean((request.form.get('billing_phone') or '').strip())[:80] or None
            perfil.tax_regime = bleach.clean((request.form.get('tax_regime') or '').strip())[:60] or None
            perfil.is_vat_responsible = request.form.get('is_vat_responsible') == 'on'

            # --- Resolucion de numeracion ---
            perfil.resolution_number = bleach.clean(
                (request.form.get('resolution_number') or '').strip())[:40] or None
            perfil.invoice_prefix = re.sub(
                r'[^A-Za-z0-9]', '', request.form.get('invoice_prefix') or '')[:10].upper() or None

            for campo, atributo in (('resolution_date', 'resolution_date'),
                                    ('resolution_valid_until', 'resolution_valid_until')):
                crudo = (request.form.get(campo) or '').strip()
                if crudo:
                    try:
                        setattr(perfil, atributo,
                                datetime.strptime(crudo, '%Y-%m-%d').date())
                    except ValueError:
                        flash(f'La fecha de "{campo}" no es valida.')
                        return redirect(url_for('settings.index'))
                else:
                    setattr(perfil, atributo, None)

            desde = request.form.get('range_from', type=int)
            hasta = request.form.get('range_to', type=int)
            if desde and hasta and hasta < desde:
                flash('El rango autorizado esta invertido: el final es menor que el inicio.')
                return redirect(url_for('settings.index'))
            perfil.range_from = desde
            perfil.range_to = hasta

            audit(
                'billing_profile_updated',
                details=f'emisor={perfil.issuer_kind}; numeracion={"si" if perfil.has_numbering else "no"}',
            )
            db.session.commit()

            faltantes = describe_readiness(perfil)
            if faltantes:
                flash('Perfil guardado. Para poder emitir todavia falta:')
                for detalle in faltantes[:5]:
                    flash(f'  {detalle}')
            else:
                flash('Perfil de facturacion completo.')

        elif action == 'legal_configuration' and current_user.role in ('admin', 'super'):
            # Datos del prestador que completan los textos legales.
            #
            # Un documento con `[[NIT_OPERADOR]]` sin reemplazar no es un
            # documento legal, asi que estos valores son los que lo vuelven
            # publicable.
            guardados = 0
            for clave in PLACEHOLDER_LABELS:
                valor = bleach.clean((request.form.get(f'legal_{clave}') or '').strip())[:500]
                fila = LegalConfiguration.query.filter_by(key=clave).first()
                if not fila:
                    fila = LegalConfiguration(key=clave)
                    db.session.add(fila)
                if (fila.value or '') != valor:
                    fila.value = valor or None
                    fila.updated_by_id = current_user.id
                    guardados += 1

            audit('legal_configuration_updated', details=f'campos={guardados}')
            db.session.commit()

            valores = {row.key: row.value
                       for row in LegalConfiguration.query.all() if row.value}
            pendientes = pending_configuration(valores)
            if pendientes:
                total = sum(len(v) for v in pendientes.values())
                flash(
                    f'Configuracion guardada. Faltan {total} dato(s) para que los '
                    'documentos legales sean publicables.'
                )
            else:
                flash('Documentos legales completos y publicables.')

        elif action == 'update_pregnancy_status' and current_user.role == 'patient':
            # Alimenta la verificacion de contraindicaciones en el embarazo.
            is_pregnant = request.form.get('is_pregnant') == 'on'
            current_user.is_pregnant = is_pregnant
            current_user.pregnancy_updated_at = colombia_now()
            audit('pregnancy_status_updated', details=f'gestante={"si" if is_pregnant else "no"}')
            db.session.commit()
            flash(
                'Estado de gestacion actualizado. Se tendra en cuenta al recetarte '
                'medicamentos.' if is_pregnant else 'Estado de gestacion actualizado.'
            )

        return redirect(url_for('settings.index'))

    clinic_rows = []
    policy_enrollments = []
    if current_user.role == 'patient':
        clinic_rows = [
            clinic_access_state(current_user, clinic)
            for clinic in Clinic.query.filter_by(status='active').order_by(Clinic.name.asc()).all()
        ]
        policy_enrollments = UserPolicyEnrollment.query.filter_by(user_id=current_user.id).order_by(UserPolicyEnrollment.created_at.desc()).all()
    # --- Facturacion ---
    billing_profile = None
    billing_gaps = []
    if current_user.role == 'doctor' and current_user.is_autonomous:
        billing_profile = BillingProfile.query.filter_by(
            doctor_id=current_user.id, issuer_kind=ISSUER_INDEPENDENT).first()
        billing_gaps = describe_readiness(billing_profile)
    elif current_user.role == 'admin':
        billing_profile = BillingProfile.query.filter_by(
            clinic_id=current_user.clinic_id, issuer_kind=ISSUER_CLINIC).first()
        billing_gaps = describe_readiness(billing_profile)

    # --- Configuracion legal ---
    legal_values = {}
    legal_pending = {}
    if current_user.role in ('admin', 'super'):
        legal_values = {row.key: row.value for row in LegalConfiguration.query.all()}
        legal_pending = pending_configuration(
            {k: v for k, v in legal_values.items() if v}
        )

    return render_template(
        'settings.html',
        clinic_rows=clinic_rows,
        policy_enrollments=policy_enrollments,
        billing_profile=billing_profile,
        billing_gaps=billing_gaps,
        legal_values=legal_values,
        legal_pending=legal_pending,
        legal_labels=PLACEHOLDER_LABELS,
    )
