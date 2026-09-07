import base64
import csv
import hashlib
import os
import secrets as _secrets
from io import StringIO
from flask import Blueprint, Response, jsonify, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from security import audit, csv_safe_row, generate_signed_order_hash, role_required, save_secure_upload, validate_medical_code
from clinical_safety import COMMON_MEDICATIONS, evaluate_prescription, normalize_drug
from sqlalchemy.exc import IntegrityError
from models import ADMINISTRATION_ROUTES, APPOINTMENT_FREEING_STATUSES, CARE_IN_PERSON, CARE_MODALITIES, CARE_TELEMEDICINE, DOSAGE_FORMS, InformedConsentLog, MedicalHistory, Notification, ACCESS_ACTIVE, Clinic, DoctorTariff, PatientAllergy, PatientChronicCondition, PaymentVerificationTicket, PatientDoctorSubscription, PAYMENT_APPROVED, PAYMENT_PENDING, PAYMENT_REJECTED, db, User, Chat, Message, Appointment, QuestionFlow, DoctorSchedule, MedicalOrder
from datetime import datetime, timedelta
from time_utils import colombia_now, colombia_strftime
from pharmacy_utils import doctor_distance, doctor_visible_on_map
from ihce import encolar as encolar_rda
from monetization import PLAN_LABELS, expiration_for_plan, generate_human_code, remaining_label
import json
import calendar
import bleach

# --- FUNCIÓN: Verificar si el doctor está en su horario laboral ahora ---
def is_doctor_currently_on_duty(doctor):
    if not doctor.is_available: return False
    now = colombia_now()
    day_of_week = now.weekday()
    current_time = now.strftime('%H:%M')
    schedule = DoctorSchedule.query.filter_by(doctor_id=doctor.id, day_of_week=day_of_week, specific_date=None, is_available=True).first()
    if schedule and schedule.start_time <= current_time <= schedule.end_time:
        return True
    return False

# --- FUNCIÓN: Verificar regla de 5 minutos (No-show) ---
def check_no_show_appointments(doctor_id):
    now = colombia_now()
    today_str = now.strftime('%Y-%m-%d')
    current_time_str = now.strftime('%H:%M')
    
    pending_appts = Appointment.query.filter_by(doctor_id=doctor_id, date=today_str, status='pending').all()
    for appt in pending_appts:
        appt_time = datetime.strptime(appt.time, '%H:%M')
        limit_time = appt_time + timedelta(minutes=5)
        limit_time_str = limit_time.strftime('%H:%M')
        
        if current_time_str > limit_time_str:
            appt.status = 'no_show'
            db.session.commit()

doctor_bp = Blueprint('doctor', __name__)


def _unique_doctor_consent_code(prefix='DOC'):
    for _ in range(10):
        code = generate_human_code(prefix)
        if not Clinic.query.filter_by(policy_consent_code=code).first() and not User.query.filter_by(policy_consent_code=code).first():
            return code
    return generate_human_code('DOC')


def build_schedule_summary(doctor_id):
    names = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
    schedules = DoctorSchedule.query.filter_by(
        doctor_id=doctor_id,
        specific_date=None,
        is_available=True
    ).order_by(DoctorSchedule.day_of_week.asc()).all()
    if not schedules:
        return None
    return " · ".join(f"{names[item.day_of_week]} {item.start_time}-{item.end_time}" for item in schedules)


def _medications_from_form():
    names = request.form.getlist('med_name')
    quantities = request.form.getlist('quantity')
    units = request.form.getlist('unit')
    instructions = request.form.getlist('instructions')
    meds = []
    for index, name in enumerate(names):
        med_name = bleach.clean((name or '').strip()[:180])
        if not med_name:
            continue
        try:
            quantity = int(quantities[index])
        except (IndexError, TypeError, ValueError):
            quantity = 1
        unit = bleach.clean((units[index] if index < len(units) else 'unidad') or 'unidad')
        instruction = bleach.clean((instructions[index] if index < len(instructions) else '') or '')
        meds.append({
            'medicamento': med_name,
            'nombre_med': med_name,
            'cantidad': max(1, quantity),
            'unidad': unit[:40],
            'instrucciones': instruction.strip()[:500],
        })
    return meds

@doctor_bp.route('/dashboard', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
def dashboard():
    check_no_show_appointments(current_user.id)
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'toggle_availability':
            current_user.is_available = not current_user.is_available
            db.session.commit()
            flash(f"Estado cambiado a {'Disponible' if current_user.is_available else 'Ocupado'}")
            
        elif action == 'update_metrics':
            current_user.schedule = bleach.clean(request.form.get('schedule') or '')
            current_user.peak_hours = bleach.clean(request.form.get('peak_hours') or '')
            
            resp_val = bleach.clean(request.form.get('avg_response_time_val') or '')
            resp_unit = bleach.clean(request.form.get('avg_response_time_unit') or '')
            current_user.avg_response_time = f"{resp_val} {resp_unit}" if resp_val else None
            
            cons_val = bleach.clean(request.form.get('avg_consultation_time_val') or '')
            cons_unit = bleach.clean(request.form.get('avg_consultation_time_unit') or '')
            current_user.avg_consultation_time = f"{cons_val} {cons_unit}" if cons_val else None
            
            db.session.commit()
            flash("Métricas actualizadas")
            
        elif action == 'update_office_location':
            current_user.office_address = bleach.clean((request.form.get('office_address') or '').strip()[:220]) or None
            current_user.office_latitude = request.form.get('office_latitude', type=float)
            current_user.office_longitude = request.form.get('office_longitude', type=float)
            current_user.show_office_on_map = request.form.get('show_office_on_map') == 'on'
            current_user.affiliation_name = bleach.clean((request.form.get('affiliation_name') or '').strip()[:180]) or (current_user.clinic.name if current_user.clinic else None)
            current_user.affiliation_type = 'autonomous' if current_user.is_autonomous else 'clinic'
            audit('doctor_office_location_updated')
            db.session.commit()
            flash("Consultorio actualizado")

        elif action == 'update_tariff':
            tariff = DoctorTariff.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).first()
            if not tariff:
                tariff = DoctorTariff(clinic_id=current_user.clinic_id, doctor_id=current_user.id)
                db.session.add(tariff)
            for field in ('price_per_consultation', 'price_monthly', 'price_quarterly', 'price_annual', 'default_policy_discount_percent'):
                value = request.form.get(field, type=float)
                setattr(tariff, field, value if value is not None and value >= 0 else None if field != 'default_policy_discount_percent' else 0)
            tariff.payment_instructions = bleach.clean((request.form.get('payment_instructions') or '').strip()[:2000]) or None
            tariff.accepts_manual_payment = request.form.get('accepts_manual_payment') == 'on'
            methods_image = request.files.get('payment_methods_image')
            if methods_image and methods_image.filename:
                try:
                    tariff.payment_methods_image = save_secure_upload(methods_image, current_app.config['UPLOAD_FOLDER'], current_user.clinic_id)
                except ValueError as error:
                    flash(str(error))
                    return redirect(url_for('doctor.dashboard'))
            audit('doctor_tariff_updated')
            db.session.commit()
            flash('Tarifas y metodos de pago actualizados.')

        elif action == 'generate_doctor_consent':
            prefix = (request.form.get('consent_prefix') or current_user.name or 'DOC').strip()[:24]
            current_user.policy_consent_code = _unique_doctor_consent_code(prefix)
            current_user.policy_consent_generated_at = colombia_now()
            audit('doctor_policy_consent_generated')
            db.session.commit()
            flash('Codigo de consentimiento para polizas generado.')

        elif action == 'review_payment_ticket':
            ticket_id = request.form.get('ticket_id', type=int)
            decision = request.form.get('decision')
            ticket = PaymentVerificationTicket.query.filter_by(
                id=ticket_id,
                doctor_id=current_user.id,
                clinic_id=current_user.clinic_id,
                status=PAYMENT_PENDING,
            ).first()
            if not ticket or decision not in {'approve', 'reject'}:
                flash('Ticket de pago invalido.')
            elif decision == 'reject':
                ticket.status = PAYMENT_REJECTED
                ticket.reviewed_at = colombia_now()
                ticket.reviewed_by_user_id = current_user.id
                ticket.doctor_note = (request.form.get('doctor_note') or '').strip()[:1000] or None
                audit('manual_payment_rejected', details=f'ticket_id={ticket.id}')
                db.session.commit()
                flash('Comprobante rechazado.')
            else:
                now = colombia_now()
                subscription = PatientDoctorSubscription(
                    clinic_id=current_user.clinic_id,
                    patient_id=ticket.patient_id,
                    doctor_id=current_user.id,
                    source='manual_payment',
                    plan_type=ticket.plan_type,
                    status=ACCESS_ACTIVE,
                    starts_at=now,
                    expires_at=expiration_for_plan(ticket.plan_type, now),
                    payment_ticket_id=ticket.id,
                )
                db.session.add(subscription)
                db.session.flush()
                ticket.status = PAYMENT_APPROVED
                ticket.reviewed_at = now
                ticket.reviewed_by_user_id = current_user.id
                ticket.doctor_note = (request.form.get('doctor_note') or '').strip()[:1000] or None
                ticket.subscription_id = subscription.id
                audit('manual_payment_approved', details=f'ticket_id={ticket.id}; subscription_id={subscription.id}')
                db.session.commit()
                flash('Pago aprobado y acceso activado.')

        elif action == 'create_flow':
            title = request.form.get('title')
            flow_data = request.form.get('flow_data')
            try:
                json.loads(flow_data)
                flow = QuestionFlow(clinic_id=current_user.clinic_id, doctor_id=current_user.id, title=title, flow_data=flow_data)
                db.session.add(flow)
                db.session.commit()
                flash("Flujo de preguntas creado")
            except json.JSONDecodeError:
                flash("Error: Formato JSON inválido")
        return redirect(url_for('doctor.dashboard'))
        
    open_chats = Chat.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id, status='open').all()
    open_chats_data = []
    for c in open_chats:
        unread = Message.query.filter(Message.chat_id == c.id, Message.sender_id == c.patient_id, Message.is_read == False).count()
        last_msg = Message.query.filter_by(chat_id=c.id).order_by(Message.timestamp.desc()).first()
        open_chats_data.append({'chat': c, 'unread': unread, 'last_msg': last_msg})

    appointments = Appointment.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).all()
    flows = QuestionFlow.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).all()
    schedules = DoctorSchedule.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).all()
    tariff = DoctorTariff.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).first()
    payment_tickets = PaymentVerificationTicket.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).order_by(PaymentVerificationTicket.created_at.desc()).limit(30).all()
    client_subscription_rows = []
    if current_user.is_autonomous:
        now = colombia_now()
        active_subscriptions = PatientDoctorSubscription.query.filter(
            PatientDoctorSubscription.doctor_id == current_user.id,
            PatientDoctorSubscription.status == ACCESS_ACTIVE,
            (PatientDoctorSubscription.expires_at == None) | (PatientDoctorSubscription.expires_at > now),
        ).order_by(PatientDoctorSubscription.expires_at.asc()).all()
        for subscription in active_subscriptions:
            if subscription.plan_type not in {'monthly', 'quarterly', 'annual', 'policy'}:
                continue
            patient = User.query.filter_by(id=subscription.patient_id).execution_options(include_all_clinics=True).first()
            client_subscription_rows.append({
                'subscription': subscription,
                'patient': patient,
                'remaining': remaining_label(subscription.expires_at, now),
                'has_policy': subscription.source == 'policy',
            })
    
    patient_ids = db.session.query(Chat.patient_id).filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).distinct().all()
    p_ids = [p[0] for p in patient_ids]
    patients = User.query.filter(User.clinic_id == current_user.clinic_id, User.id.in_(p_ids)).all()
    
    return render_template('doctor_dashboard.html', 
                           open_chats=open_chats_data, 
                           appointments=appointments, 
                           flows=flows, 
                           patients=patients, 
                           schedules=schedules,
                           tariff=tariff,
                           payment_tickets=payment_tickets,
                           client_subscription_rows=client_subscription_rows,
                           plan_labels=PLAN_LABELS)

# --- RUTA RESTAURADA: SUBIR FOTO DE PERFIL ---
@doctor_bp.route('/upload_pfp', methods=['POST'])
@login_required
@role_required('doctor')
def upload_pfp():
    file = request.files.get('pfp')
    try:
        filename = save_secure_upload(file, current_app.config['UPLOAD_FOLDER'], current_user.clinic_id)
    except ValueError as error:
        flash(str(error))
        return redirect(url_for('doctor.dashboard'))
    if filename:
        current_user.profile_pic = filename
        audit('profile_photo_uploaded')
        db.session.commit()
        flash('Foto de perfil actualizada')
    return redirect(url_for('doctor.dashboard'))


def _medications_from_form_legal():
    """Lee los medicamentos del formulario y valida lo que exige la norma.

    La Resolucion 1403 de 2007 enumera lo que debe contener cada renglon de una
    prescripcion. Faltaban cuatro cosas:

    - **Denominacion Comun Internacional.** La norma exige prescribir por nombre
      generico. Antes el campo era texto libre, asi que "Dolex" y "paracetamol"
      entraban igual y la farmacia no podia saber si era una marca.
    - **Concentracion y forma farmaceutica por separado.** "Amoxicilina 500 mg"
      no dice si es capsula o suspension, y esa diferencia cambia como se
      dispensa y como se administra a un nino.
    - **Duracion del tratamiento.** Sin ella no se puede verificar que la
      cantidad prescrita corresponda a la pauta.
    - **Cantidad en letras.** La norma la exige para los medicamentos de control
      especial, porque una cifra en numeros se altera con un trazo.

    Devuelve `(medicamentos, errores)`. Con errores no se emite la orden.
    """
    campos = {
        clave: request.form.getlist(clave)
        for clave in ('med_name', 'generic_name', 'quantity', 'unit', 'instructions',
                      'concentration', 'dosage_form', 'dosage', 'frequency',
                      'route', 'duration_days', 'is_brand')
    }

    def leer(clave, indice, defecto=''):
        lista = campos[clave]
        return (lista[indice] if indice < len(lista) else defecto) or defecto

    meds = []
    errores = []

    for indice, nombre in enumerate(campos['med_name']):
        med_name = bleach.clean((nombre or '').strip()[:180])
        if not med_name:
            continue

        renglon = indice + 1
        generico = bleach.clean(leer('generic_name', indice).strip()[:180])
        # Si no se indica generico aparte, se asume que el nombre ya lo es.
        if not generico:
            generico = med_name

        concentracion = bleach.clean(leer('concentration', indice).strip()[:60])
        forma = bleach.clean(leer('dosage_form', indice).strip().lower()[:60])
        via = bleach.clean(leer('route', indice, 'oral').strip().lower()[:30])
        dosis = bleach.clean(leer('dosage', indice).strip()[:80])
        frecuencia = bleach.clean(leer('frequency', indice).strip()[:100])
        instruccion = bleach.clean(leer('instructions', indice).strip()[:500])
        unidad = bleach.clean(leer('unit', indice, 'unidad').strip()[:40]) or 'unidad'

        try:
            cantidad = int(leer('quantity', indice, '0'))
        except (TypeError, ValueError):
            cantidad = 0

        try:
            duracion = int(leer('duration_days', indice, '0'))
        except (TypeError, ValueError):
            duracion = 0

        # --- Requisitos de la norma ---
        if not concentracion:
            errores.append(f'Renglon {renglon} ({med_name}): falta la concentracion.')
        if not forma:
            errores.append(f'Renglon {renglon} ({med_name}): falta la forma farmaceutica.')
        elif forma not in DOSAGE_FORMS:
            errores.append(
                f'Renglon {renglon} ({med_name}): forma farmaceutica "{forma}" no reconocida.'
            )
        if via not in ADMINISTRATION_ROUTES:
            errores.append(
                f'Renglon {renglon} ({med_name}): via de administracion "{via}" no reconocida.'
            )
        if not dosis:
            errores.append(f'Renglon {renglon} ({med_name}): falta la dosis.')
        if not frecuencia:
            errores.append(f'Renglon {renglon} ({med_name}): falta la frecuencia.')
        if cantidad < 1:
            errores.append(f'Renglon {renglon} ({med_name}): la cantidad debe ser mayor que cero.')
        if duracion < 1:
            errores.append(
                f'Renglon {renglon} ({med_name}): falta la duracion del tratamiento en dias.'
            )

        meds.append({
            'medicamento': med_name,
            'nombre_med': generico,           # lo que consulta el motor de seguridad
            'denominacion_comun': generico,
            'nombre_comercial': med_name if med_name.lower() != generico.lower() else None,
            'es_marca': leer('is_brand', indice) == 'on',
            'concentracion': concentracion,
            'forma_farmaceutica': forma,
            'via': via,
            'dosis': dosis,
            'frecuencia': frecuencia,
            'duracion_dias': max(0, duracion),
            'cantidad': max(0, cantidad),
            'cantidad_en_letras': _number_to_words(cantidad),
            'unidad': unidad,
            'instrucciones': instruccion,
        })

    return meds, errores


_UNIDADES = ('cero', 'uno', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete',
             'ocho', 'nueve', 'diez', 'once', 'doce', 'trece', 'catorce',
             'quince', 'dieciseis', 'diecisiete', 'dieciocho', 'diecinueve')
_DECENAS = ('', '', 'veinte', 'treinta', 'cuarenta', 'cincuenta', 'sesenta',
            'setenta', 'ochenta', 'noventa')
_CENTENAS = ('', 'ciento', 'doscientos', 'trescientos', 'cuatrocientos',
             'quinientos', 'seiscientos', 'setecientos', 'ochocientos', 'novecientos')


def _number_to_words(numero):
    """Escribe la cantidad en letras.

    La norma la exige para medicamentos de control especial: una cifra en
    numeros se altera con un trazo de boligrafo, "10" se convierte en "100".
    En letras hace falta reescribir la palabra entera.
    """
    try:
        numero = int(numero)
    except (TypeError, ValueError):
        return ''
    if numero < 0:
        return ''
    if numero == 100:
        return 'cien'
    if numero < 20:
        return _UNIDADES[numero]
    if numero < 30:
        return 'veinti' + _UNIDADES[numero - 20] if numero > 20 else 'veinte'
    if numero < 100:
        decena, unidad = divmod(numero, 10)
        return _DECENAS[decena] + (f' y {_UNIDADES[unidad]}' if unidad else '')
    if numero < 1000:
        centena, resto = divmod(numero, 100)
        return (_CENTENAS[centena] + (f' {_number_to_words(resto)}' if resto else '')).strip()
    if numero < 1000000:
        millar, resto = divmod(numero, 1000)
        prefijo = 'mil' if millar == 1 else f'{_number_to_words(millar)} mil'
        return (prefijo + (f' {_number_to_words(resto)}' if resto else '')).strip()
    return str(numero)


def _generate_order_number(clinic_id, order_id):
    """Numero legal de la orden, derivado de su identificador real.

    La version anterior calculaba `ultimo_id + 1` antes de insertar. Con dos
    medicos prescribiendo a la vez, ambos leian el mismo ultimo identificador y
    las dos ordenes salian con el mismo numero: dos documentos legales distintos
    compartiendo identificador. Derivarlo del identificador ya asignado por la
    base de datos elimina la carrera, porque la secuencia la garantiza el motor.
    """
    return f'OM-{clinic_id}-{order_id:05d}'


def _patient_age_years(patient, reference=None):
    """Edad en anos cumplidos, o None si no hay fecha de nacimiento."""
    birth = getattr(patient, 'birth_date', None)
    if not birth:
        return None
    today = (reference or colombia_now()).date()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _active_medications_for(patient, days=90):
    """Medicamentos que el paciente esta tomando, segun ordenes vigentes.

    Se deducen de las ordenes emitidas y no vencidas en el periodo, en lugar de
    mantener una lista aparte que se desincronizaria. Alimenta la deteccion de
    interacciones con el tratamiento en curso.
    """
    cutoff = colombia_now() - timedelta(days=days)
    orders = MedicalOrder.query.filter(
        MedicalOrder.patient_id == patient.id,
        MedicalOrder.clinic_id == patient.clinic_id,
        MedicalOrder.created_at >= cutoff,
        MedicalOrder.status.in_(['pendiente', 'autorizada', 'pendiente_stock', 'completada']),
    ).order_by(MedicalOrder.created_at.desc()).limit(30).all()

    seen = set()
    active = []
    for order in orders:
        try:
            for med in json.loads(order.meds_json or '[]'):
                name = (med.get('medicamento') or med.get('nombre_med') or '').strip()
                key = name.lower()
                if name and key not in seen:
                    seen.add(key)
                    active.append(name)
        except (json.JSONDecodeError, TypeError):
            continue
    return active


def _active_allergies_for(patient):
    return PatientAllergy.query.filter(
        PatientAllergy.patient_id == patient.id,
        PatientAllergy.status != 'descartada',
    ).all()


def _clinical_record_number(patient):
    """Numero de historia clinica del paciente.

    La Resolucion 1403 de 2007 lo exige en la prescripcion y no existia. Se
    deriva de la clinica y del identificador del paciente, que es estable y
    unico dentro de la institucion, en lugar de crear un contador aparte que
    habria que mantener sincronizado.
    """
    return f'HC-{patient.clinic_id}-{patient.id:06d}'


def _resolve_care_modality(chat_id, patient):
    """Determina si la atencion fue presencial o por telemedicina.

    La Resolucion 2654 de 2019 obliga a dejar constancia de la modalidad. Si la
    orden nace de un chat clinico, la atencion fue a distancia; si no, se toma
    lo que indique el profesional.
    """
    declarada = (request.form.get('care_modality') or '').strip().lower()
    if chat_id:
        return CARE_TELEMEDICINE
    if declarada in CARE_MODALITIES:
        return declarada
    return CARE_IN_PERSON


def _telemedicine_consent(patient):
    """Consentimiento de telemedicina vigente del paciente, si existe."""
    return InformedConsentLog.query.filter_by(
        patient_id=patient.id,
        consent_type='telemedicine',
        granted=True,
        revoked_at=None,
    ).order_by(InformedConsentLog.timestamp.desc()).first()


@doctor_bp.route('/prescription/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
def prescription(patient_id):
    patient = User.query.filter_by(id=patient_id, clinic_id=current_user.clinic_id, role='patient').first_or_404()
    chat_id = request.args.get('chat_id', type=int) or request.form.get('chat_id', type=int)

    allergies = _active_allergies_for(patient)
    active_meds = _active_medications_for(patient)
    age_years = _patient_age_years(patient)

    def back_to_form():
        return redirect(url_for('doctor.prescription', patient_id=patient.id, chat_id=chat_id or ''))

    if request.method == 'POST':
        # --- Requisitos legales de quien firma -------------------------------
        # Una orden medica sin registro profesional y sin firma no es un documento
        # valido. Antes se emitia igual, y el campo `signature_hash` del modelo
        # nunca llegaba a poblarse.
        if not (current_user.medical_registration or '').strip():
            flash(
                'No es posible emitir ordenes medicas sin el numero de registro '
                'medico profesional. Registrelo en Configuracion.'
            )
            return redirect(url_for('settings.index'))

        if not (current_user.signature_path or request.form.get('signature_data')):
            flash('Debe registrar su firma digital antes de emitir una orden medica.')
            return back_to_form()

        # --- Modalidad de atencion (Resolucion 2654 de 2019) -----------------
        # Si la orden nace de una consulta por chat, la atencion fue a distancia
        # y requiere el consentimiento especifico de telemedicina, distinto del
        # consentimiento general de datos.
        care_modality = _resolve_care_modality(chat_id, patient)
        telemedicine_consent = None
        if care_modality == CARE_TELEMEDICINE:
            telemedicine_consent = _telemedicine_consent(patient)
            if not telemedicine_consent:
                flash(
                    'Esta atencion es por telemedicina y el paciente aun no ha '
                    'otorgado el consentimiento informado especifico que exige la '
                    'Resolucion 2654 de 2019. Solicitele que lo acepte en "Mis datos" '
                    'antes de emitir la orden.'
                )
                return back_to_form()

        meds, errores_receta = _medications_from_form_legal()
        if not meds:
            flash('Agregue al menos un medicamento.')
            return back_to_form()
        if errores_receta:
            # La Resolucion 1403 de 2007 enumera lo que debe contener cada
            # renglon. Una prescripcion incompleta no es dispensable: la farmacia
            # no puede saber que forma farmaceutica entregar ni por cuanto tiempo.
            for error in errores_receta[:8]:
                flash(error)
            if len(errores_receta) > 8:
                flash(f'... y {len(errores_receta) - 8} dato(s) mas por completar.')
            return back_to_form()

        # --- Vigencia --------------------------------------------------------
        expires_date = request.form.get('expires_date')
        expires_time = request.form.get('expires_time') or '23:59'
        try:
            expires_at = datetime.strptime(f'{expires_date} {expires_time}', '%Y-%m-%d %H:%M')
        except (TypeError, ValueError):
            flash('La fecha de caducidad no es valida.')
            return back_to_form()

        now = colombia_now()
        if expires_at <= now:
            flash('La orden debe caducar en una fecha futura.')
            return back_to_form()

        # Tope superior: sin el, el formulario aceptaba una orden valida durante
        # anos, que sigue surtiendo efecto mucho despues de que la indicacion
        # clinica dejo de tener sentido.
        max_days = current_app.config.get('MAX_ORDER_VALIDITY_DAYS', 180)
        if (expires_at - now).days > max_days:
            flash(f'La vigencia de una orden no puede superar {max_days} dias.')
            return back_to_form()

        # --- Diagnosticos: CIE-10 validado -----------------------------------
        # `validate_medical_code` existia desde antes y nunca se llamaba en este
        # flujo, asi que cualquier cadena entraba como diagnostico y salia en el
        # RIPS que se radica.
        dx_codes = request.form.getlist('dx_code')
        dx_descriptions = request.form.getlist('dx_description')
        diagnoses = []
        for index, raw_code in enumerate(dx_codes):
            code = (raw_code or '').strip().upper()[:10]
            if not code:
                continue
            valid, result = validate_medical_code(code, 'CIE10')
            if not valid:
                flash(f'Diagnostico rechazado: {result}')
                return back_to_form()
            description = bleach.clean(
                (dx_descriptions[index] if index < len(dx_descriptions) else '') or ''
            )[:300]
            diagnoses.append({'code': result, 'description': description})

        if not diagnoses:
            flash('Toda orden medica requiere al menos un diagnostico CIE-10.')
            return back_to_form()

        # --- Verificacion de seguridad clinica -------------------------------
        safety = evaluate_prescription(
            medications=meds,
            allergies=allergies,
            active_medications=active_meds,
            is_pregnant=bool(getattr(patient, 'is_pregnant', False)),
            patient_age_years=age_years,
        )

        override_reason = bleach.clean((request.form.get('safety_override_reason') or '').strip())[:1000]

        if safety.requires_override and len(override_reason) < 20:
            # El profesional conserva la decision, pero debe dejarla escrita.
            # Sin justificacion no se firma.
            audit(
                'prescription_blocked_by_safety',
                details=(
                    f'patient_id={patient.id}; hallazgos={len(safety.blocking)}; '
                    f'kb={safety.knowledge_base_version}'
                ),
            )
            db.session.commit()
            for finding in safety.blocking:
                flash(f'[{finding.severity_label}] {finding.title}: {finding.detail}')
            flash(
                'Para continuar pese a estas alertas debe consignar la justificacion '
                'clinica (minimo 20 caracteres). Quedara registrada en la orden.'
            )
            return render_template(
                'doctor_prescription.html',
                patient=patient,
                chat_id=chat_id,
                default_expiration=expires_date,
                dosage_forms=DOSAGE_FORMS,
                administration_routes=ADMINISTRATION_ROUTES,
                common_medications=COMMON_MEDICATIONS,
                safety=safety,
                allergies=allergies,
                active_meds=active_meds,
                submitted_meds=meds,
                submitted_diagnoses=diagnoses,
            )

        # --- Firma -----------------------------------------------------------
        signature_data = request.form.get('signature_data', '')
        if signature_data and signature_data.startswith('data:image/png;base64,'):
            try:
                raw = base64.b64decode(signature_data.split(',', 1)[1])
                if len(raw) > 500_000:
                    flash('La imagen de la firma es demasiado grande.')
                    return back_to_form()
                sig_filename = f'{_secrets.token_urlsafe(16)}.png'
                clinic_folder = f'clinic_{current_user.clinic_id}'
                target_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], clinic_folder)
                os.makedirs(target_dir, exist_ok=True)
                with open(os.path.join(target_dir, sig_filename), 'wb') as handle:
                    handle.write(raw)
                if not current_user.signature_path:
                    current_user.signature_path = f'{clinic_folder}/{sig_filename}'
            except (ValueError, OSError):
                # Antes esto era `except Exception: pass`: la orden salia sin firma
                # y nadie se enteraba.
                current_app.logger.exception('Fallo al guardar la firma del medico %s', current_user.id)
                flash('No fue posible guardar la firma. Intenta de nuevo.')
                return back_to_form()

        created_at = colombia_now()
        meds_json = json.dumps(meds, ensure_ascii=False, sort_keys=True)
        diagnosis_json = json.dumps(diagnoses, ensure_ascii=False)

        secure_hash = generate_signed_order_hash(
            current_user.id,
            patient.id,
            meds_json,
            current_user.clinic_id,
            issued_at=created_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )

        order = MedicalOrder(
            clinic_id=current_user.clinic_id,
            order_number='PENDIENTE',
            doctor_id=current_user.id,
            patient_id=patient.id,
            meds_json=meds_json,
            diagnosis_json=diagnosis_json,
            patient_age=str(age_years) if age_years is not None else (
                bleach.clean((request.form.get('patient_age') or '').strip()[:50]) or None
            ),
            patient_level=bleach.clean((request.form.get('patient_level') or '1').strip()[:10]),
            insurance_name=bleach.clean((request.form.get('insurance_name') or '').strip()[:180]) or None,
            insurance_plan=bleach.clean((request.form.get('insurance_plan') or '').strip()[:80]) or None,
            insurance_regime=bleach.clean((request.form.get('insurance_regime') or '').strip()[:50]) or None,
            observations=bleach.clean((request.form.get('observations') or '').strip()[:2000]) or None,

            # Datos del paciente congelados en el momento de prescribir. Una
            # orden es un documento con fecha: si el paciente cambia de telefono
            # manana, la orden de hoy no puede cambiar sola.
            patient_document_type=patient.document_type,
            patient_document=patient.cedula,
            patient_address=patient.address,
            patient_phone=patient.phone,
            clinical_record_number=_clinical_record_number(patient),
            care_modality=care_modality,

            doctor_registration=current_user.medical_registration,
            signed_at=created_at,
            mipres_number=bleach.clean((request.form.get('mipres_number') or '').strip()[:40]) or None,
            safety_report_json=json.dumps(safety.to_dict(), ensure_ascii=False),
            safety_kb_version=safety.knowledge_base_version,
            safety_override_reason=override_reason or None,
            safety_override_at=created_at if (safety.requires_override and override_reason) else None,
            verification_hash=secure_hash,
            hash_seguridad=secure_hash,
            status='pendiente',
            created_at=created_at,
            expires_at=expires_at,
            fecha_expiracion=expires_at,
        )
        db.session.add(order)
        db.session.flush()   # obtiene el identificador real de la base de datos

        order.order_number = _generate_order_number(current_user.clinic_id, order.id)
        if telemedicine_consent:
            order.telemedicine_consent_id = telemedicine_consent.id

        # Sello de la orden firmada: vincula contenido, autor y registro
        # profesional. Permite demostrar despues que el documento no se altero.
        order.signature_hash = hashlib.sha256(
            '|'.join([
                str(order.id),
                order.order_number,
                str(current_user.id),
                current_user.medical_registration or '',
                meds_json,
                diagnosis_json,
                created_at.isoformat(),
            ]).encode('utf-8')
        ).hexdigest()

        chat = Chat.query.filter_by(
            id=chat_id, clinic_id=current_user.clinic_id,
            doctor_id=current_user.id, patient_id=patient.id,
        ).first() if chat_id else None
        if chat:
            db.session.add(Message(
                clinic_id=current_user.clinic_id,
                chat_id=chat.id,
                sender_id=current_user.id,
                content=f'Orden medica digital #{order.order_number} generada.',
            ))

        audit(
            'medical_order_created',
            details=(
                f'order_id={order.id}; numero={order.order_number}; patient_id={patient.id}; '
                f'hallazgos={len(safety)}; override={"si" if override_reason else "no"}'
            ),
        )
        if override_reason:
            audit(
                'prescription_safety_override',
                details=(
                    f'order_id={order.id}; bloqueantes={len(safety.blocking)}; '
                    f'kb={safety.knowledge_base_version}'
                ),
            )

        db.session.commit()

        if safety.warnings:
            for finding in safety.warnings:
                flash(f'[Advertencia] {finding.title}: {finding.recommendation or finding.detail}')
        flash('Orden medica firmada y enviada al perfil del paciente.')
        return redirect(url_for('doctor.view_order', order_id=order.id))

    default_expiration = (colombia_now() + timedelta(days=30)).strftime('%Y-%m-%d')
    return render_template(
        'doctor_prescription.html',
        patient=patient,
        chat_id=chat_id,
        default_expiration=default_expiration,
        dosage_forms=DOSAGE_FORMS,
        administration_routes=ADMINISTRATION_ROUTES,
        common_medications=COMMON_MEDICATIONS,
        allergies=allergies,
        active_meds=active_meds,
        patient_age_years=age_years,
    )


@doctor_bp.route('/order/<int:order_id>/anular', methods=['POST'])
@login_required
@role_required('doctor')
def annul_order(order_id):
    """Anula una orden medica emitida por error.

    No existia ninguna via para esto: una orden con un error —dosis equivocada,
    medicamento cambiado, paciente confundido— seguia siendo dispensable hasta su
    fecha de vencimiento, que puede ser meses despues.

    La orden no se borra ni se edita. Se marca como anulada con el motivo y la
    identidad de quien la anula, y deja de poder dispensarse. El documento
    original permanece porque forma parte de la historia clinica.
    """
    order = MedicalOrder.query.filter_by(
        id=order_id, clinic_id=current_user.clinic_id
    ).first_or_404()

    if order.doctor_id != current_user.id:
        # Solo quien firmo puede anular: la firma es lo que da validez al
        # documento y a su retiro.
        audit('order_annulment_denied', details=f'order_id={order.id}')
        db.session.commit()
        flash('Solo el profesional que firmo la orden puede anularla.')
        return redirect(url_for('doctor.dashboard'))

    if order.annulled_at:
        flash('Esta orden ya estaba anulada.')
        return redirect(url_for('doctor.view_order', order_id=order.id))

    motivo = bleach.clean((request.form.get('reason') or '').strip())[:500]
    if len(motivo) < 15:
        flash('Indique el motivo de la anulacion (minimo 15 caracteres). Quedara en la historia clinica.')
        return redirect(url_for('doctor.view_order', order_id=order.id))

    if order.status in ('autorizada', 'completada'):
        # Si ya se genero ticket o se entrego algo, anular la orden no deshace la
        # entrega: se advierte para que el profesional lo tenga en cuenta.
        flash(
            'Atencion: esta orden ya fue autorizada en farmacia. Anularla impide '
            'nuevas entregas, pero no revierte lo ya dispensado. Coordina con la '
            'farmacia si es necesario.'
        )

    now = colombia_now()
    order.annulled_at = now
    order.annulled_by_id = current_user.id
    order.annulment_reason = motivo
    order.status = 'anulada'

    audit(
        'medical_order_annulled',
        details=f'order_id={order.id}; numero={order.order_number}; estado_previo={order.status}',
    )

    db.session.add(Notification(
        user_id=order.patient_id,
        clinic_id=order.clinic_id,
        title='Orden medica anulada',
        message=(
            f'La orden {order.order_number} fue anulada por el profesional que la '
            f'emitio. Motivo: {motivo} Si requiere el medicamento, comuniquese con su medico.'
        ),
        type='order_annulled',
    ))
    db.session.commit()

    flash(f'Orden {order.order_number} anulada. El paciente fue notificado.')
    return redirect(url_for('doctor.view_order', order_id=order.id))


@doctor_bp.route('/api/prescription_check', methods=['POST'])
@login_required
@role_required('doctor')
def prescription_check():
    """Verificacion de seguridad en vivo, mientras se escribe la orden.

    Permite al profesional ver la alerta al anadir el medicamento, no al intentar
    firmar. Es el mismo motor que aplica la validacion definitiva: esta vista no
    autoriza nada por si sola.
    """
    payload = request.get_json(silent=True) or {}
    patient_id = payload.get('patient_id')

    patient = User.query.filter_by(
        id=patient_id, clinic_id=current_user.clinic_id, role='patient'
    ).first()
    if not patient:
        return jsonify({'error': 'patient_not_found'}), 404

    medications = []
    for item in (payload.get('medications') or [])[:50]:
        if not isinstance(item, dict):
            continue
        name = str(item.get('nombre_med') or item.get('medicamento') or '').strip()[:180]
        if not name:
            continue
        try:
            quantity = int(item.get('cantidad') or 1)
        except (TypeError, ValueError):
            quantity = 1
        medications.append({'nombre_med': name, 'medicamento': name, 'cantidad': quantity})

    report = evaluate_prescription(
        medications=medications,
        allergies=_active_allergies_for(patient),
        active_medications=_active_medications_for(patient),
        is_pregnant=bool(getattr(patient, 'is_pregnant', False)),
        patient_age_years=_patient_age_years(patient),
    )
    return jsonify(report.to_dict())


@doctor_bp.route('/order/<int:order_id>')
@login_required
@role_required('doctor')
def view_order(order_id):
    order = MedicalOrder.query.filter_by(id=order_id, clinic_id=current_user.clinic_id).first_or_404()
    if order.doctor_id != current_user.id:
        flash('Esta orden no corresponde a su cuenta.')
        return redirect(url_for('doctor.dashboard'))

    clinic = current_user.clinic
    meds = json.loads(order.meds_json or '[]')
    diagnoses = json.loads(order.diagnosis_json or '[]') if order.diagnosis_json else []
    med_instructions = [m.get('instrucciones', '') for m in meds]
    has_instructions = any(inst for inst in med_instructions)

    validity_days = max(1, (order.expires_at - order.created_at).days) if order.expires_at and order.created_at else 30
    print_date = colombia_strftime(colombia_now(), '%d/%m/%Y %H:%M:%S')

    return render_template('medical_order_print.html',
                           order=order,
                           clinic=clinic,
                           meds=meds,
                           diagnoses=diagnoses,
                           med_instructions=med_instructions if has_instructions else [],
                           validity_days=validity_days,
                           print_date=print_date)


@doctor_bp.route('/save_signature', methods=['POST'])
@login_required
@role_required('doctor')
def save_signature():
    import base64, os, secrets as _secrets
    signature_data = request.form.get('signature_data', '')
    if not signature_data or not signature_data.startswith('data:image/png;base64,'):
        flash('No se recibió firma válida.')
        return redirect(url_for('doctor.dashboard'))
    try:
        raw = base64.b64decode(signature_data.split(',', 1)[1])
        if len(raw) > 500_000:
            flash('La imagen de la firma es demasiado grande.')
            return redirect(url_for('doctor.dashboard'))
        sig_filename = f'{_secrets.token_urlsafe(16)}.png'
        clinic_folder = f'clinic_{current_user.clinic_id}'
        target_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], clinic_folder)
        os.makedirs(target_dir, exist_ok=True)
        sig_path = f'{clinic_folder}/{sig_filename}'
        with open(os.path.join(target_dir, sig_filename), 'wb') as f:
            f.write(raw)
        current_user.signature_path = sig_path
        audit('doctor_signature_saved')
        db.session.commit()
        flash('Firma digital guardada exitosamente.')
    except Exception:
        flash('Error al guardar la firma.')
    return redirect(url_for('doctor.dashboard'))



@doctor_bp.route('/chat/<int:chat_id>', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
def chat(chat_id):
    chat_obj = Chat.query.get_or_404(chat_id)
    if chat_obj.doctor_id != current_user.id or chat_obj.clinic_id != current_user.clinic_id:
        return redirect(url_for('doctor.dashboard'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_reason': 
            chat_obj.reason = (request.form.get('reason') or '')[:200]
            flash('Motivo actualizado')
        elif action == 'send_message':
            content = (request.form.get('content') or '').strip()[:5000]
            if content: 
                msg = Message(clinic_id=current_user.clinic_id, chat_id=chat_obj.id, sender_id=current_user.id, content=content)
                db.session.add(msg)
        elif action == 'upload_file':
            file = request.files.get('file')
            if file and file.filename != '':
                try:
                    filename = save_secure_upload(file, current_app.config['UPLOAD_FOLDER'], current_user.clinic_id)
                except ValueError as error:
                    flash(str(error))
                    return redirect(url_for('doctor.chat', chat_id=chat_obj.id))
                ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
                content = "Imagen" if ext in ['jpg', 'jpeg', 'png', 'webp'] else "Archivo"
                msg = Message(clinic_id=current_user.clinic_id, chat_id=chat_obj.id, sender_id=current_user.id, content=content, file_path=filename)
                db.session.add(msg)
        elif action == 'start_flow':
            flow_id = request.form.get('flow_id')
            flow = db.session.get(QuestionFlow, int(flow_id)) if flow_id else None
            if flow:
                flow_dict = json.loads(flow.flow_data)
                start_node_id = flow_dict['start_node']
                first_node = flow_dict['nodes'][start_node_id]
                chat_obj.active_flow_id = flow.id
                chat_obj.active_flow_node = start_node_id
                input_type = first_node.get('input_type', 'option')
                has_options = bool(first_node.get('options'))
                msg = Message(clinic_id=current_user.clinic_id, chat_id=chat_obj.id, sender_id=current_user.id, 
                              content=first_node['text'], 
                              is_flow_question=True if has_options or input_type == 'open' else False, 
                              flow_options=json.dumps(first_node['options']) if has_options else None, 
                              flow_input_type=input_type if not has_options else 'option')
                db.session.add(msg)
        elif action == 'close_chat':
            if current_user.attending_chat_id == chat_obj.id:
                current_user.attending_chat_id = None
                current_user.is_available = True
            chat_obj.status = 'closed'
            chat_obj.closed_by = 'doctor'
            chat_obj.active_flow_id = None
            chat_obj.mode = 'triage'
            if chat_obj.appointment and chat_obj.appointment.status == 'in_progress':
                chat_obj.appointment.status = 'attended'
            flash('Chat cerrado.')

        elif action == 'save_history':
            summary = bleach.clean(request.form.get('summary') or '')
            diagnosis = bleach.clean(request.form.get('diagnosis') or '')
            cie10 = request.form.get('cie10_code', '').upper().strip()
            cups = request.form.get('cups_code', '').upper().strip()
            treatment = bleach.clean(request.form.get('treatment') or '')
            
            # Validation
            cie_ok, cie_res = validate_medical_code(cie10, 'CIE10')
            if not cie_ok:
                flash(cie_res)
                return redirect(url_for('doctor.chat', chat_id=chat_obj.id))
            
            cups_ok, cups_res = validate_medical_code(cups, 'CUPS')
            if not cups_ok:
                flash(cups_res)
                return redirect(url_for('doctor.chat', chat_id=chat_obj.id))

            history = MedicalHistory(
                clinic_id=current_user.clinic_id,
                patient_id=chat_obj.patient_id,
                doctor_id=current_user.id,
                chat_id=chat_obj.id,
                appointment_id=chat_obj.appointment_id,
                record_type='consultation',
                summary=summary,
                diagnosis=diagnosis,
                cie10_code=cie_res,
                cups_code=cups_res,
                treatment=treatment
            )
            db.session.add(history)
            audit('medical_history_recorded', details=f'patient_id={chat_obj.patient_id}')

            # Resolucion 1888 de 2025: cada atencion debe remitirse al IHCE.
            # Solo se encola. Si se llamara al Ministerio aqui, una caida de su
            # servidor impediria cerrar la historia clinica, y en una zona rural
            # eso pasa. La transmision corre aparte y reintenta.
            db.session.flush()
            try:
                encolar_rda(db, history)
            except Exception:
                # Encolar nunca puede tumbar el registro de la atencion. Si la
                # cola falla, queda el hueco documentado en la auditoria y
                # `manage.py rda-backfill` lo recupera despues.
                current_app.logger.exception('No se pudo encolar el RDA')
                audit('rda_enqueue_failed', details=f'history_id={history.id}')

            flash('Historia clínica guardada exitosamente.')
        
        audit('doctor_chat_updated', details=f'chat_id={chat_obj.id}; action={action}')
        db.session.commit()
        return redirect(url_for('doctor.chat', chat_id=chat_obj.id))
    
    messages = Message.query.filter_by(chat_id=chat_obj.id).order_by(Message.timestamp.asc()).all()
    
    # Marcar como leídos
    unread_msgs = Message.query.filter(Message.chat_id == chat_obj.id, Message.sender_id == chat_obj.patient_id, Message.is_read == False).all()
    for msg in unread_msgs:
        msg.is_read = True
    db.session.commit()

    previous_chats = Chat.query.filter(
        Chat.patient_id == chat_obj.patient_id,
        Chat.doctor_id == current_user.id,
        Chat.clinic_id == current_user.clinic_id,
        Chat.id != chat_obj.id
    ).with_entities(Chat.id).all()
    chat_ids = [c[0] for c in previous_chats]
    file_history = Message.query.filter(Message.chat_id.in_(chat_ids), Message.file_path.isnot(None)).all() if chat_ids else []
    flows = QuestionFlow.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).all()
    nearby_doctors = []
    if not current_user.is_autonomous:
        colleagues = User.query.filter(
            User.clinic_id == current_user.clinic_id,
            User.role == 'doctor',
            User.id != current_user.id,
        ).order_by(User.name.asc()).all()
        for colleague in colleagues:
            distance = doctor_distance(chat_obj.patient, colleague) if doctor_visible_on_map(colleague) else None
            nearby_doctors.append({'doctor': colleague, 'distance_km': distance})
        nearby_doctors.sort(key=lambda item: item['distance_km'] if item['distance_km'] is not None else 999999)
    
    return render_template('doctor_chat.html', chat=chat_obj, messages=messages, file_history=file_history, flows=flows, nearby_doctors=nearby_doctors)


@doctor_bp.route('/refer/<int:chat_id>', methods=['POST'])
@login_required
@role_required('doctor')
def refer_patient(chat_id):
    chat_obj = Chat.query.filter_by(id=chat_id, clinic_id=current_user.clinic_id, doctor_id=current_user.id).first_or_404()
    if current_user.is_autonomous:
        flash('Los doctores autonomos no pueden remitir dentro de una red clinica.')
        return redirect(url_for('doctor.chat', chat_id=chat_id))
    target_id = request.form.get('target_doctor_id', type=int)
    target = User.query.filter_by(id=target_id, clinic_id=current_user.clinic_id, role='doctor').first()
    if not target or target.id == current_user.id:
        flash('Doctor de remision invalido.')
        return redirect(url_for('doctor.chat', chat_id=chat_id))
    reason = (request.form.get('reason') or chat_obj.reason or 'Remision clinica')[:500]
    referred_chat = Chat.query.filter_by(
        clinic_id=current_user.clinic_id,
        patient_id=chat_obj.patient_id,
        doctor_id=target.id,
        status='open',
    ).first()
    if not referred_chat:
        referred_chat = Chat(
            clinic_id=current_user.clinic_id,
            patient_id=chat_obj.patient_id,
            doctor_id=target.id,
            reason=f'Remision de Dr. {current_user.name}: {reason}'[:200],
            mode='triage',
        )
        db.session.add(referred_chat)
        db.session.flush()
    db.session.add(Message(
        clinic_id=current_user.clinic_id,
        chat_id=chat_obj.id,
        sender_id=current_user.id,
        content=f'Remision sugerida con Dr. {target.name}. Motivo: {reason}',
    ))
    db.session.add(Message(
        clinic_id=current_user.clinic_id,
        chat_id=referred_chat.id,
        sender_id=current_user.id,
        content=f'Remision desde Dr. {current_user.name}. Motivo: {reason}',
    ))
    audit('patient_referred_to_colleague', details=f'chat_id={chat_id}; target_doctor_id={target.id}')
    db.session.commit()
    flash('Remision enviada al colega seleccionado.')
    return redirect(url_for('doctor.chat', chat_id=chat_id))

@doctor_bp.route('/save_schedule', methods=['POST'])
@login_required
@role_required('doctor')
def save_schedule():
    action_type = request.form.get('schedule_action')
    
    if action_type == 'weekly':
        any_active = False
        for i in range(7):
            start = request.form.get(f'start_{i}')
            end = request.form.get(f'end_{i}')
            is_active = request.form.get(f'active_{i}') == 'on'
            DoctorSchedule.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id, day_of_week=i, specific_date=None).delete()
            if is_active and start and end:
                if start >= end:
                    flash('Cada horario debe terminar después de la hora de inicio.')
                    return redirect(url_for('doctor.dashboard'))
                new_sched = DoctorSchedule(clinic_id=current_user.clinic_id, doctor_id=current_user.id, day_of_week=i, specific_date=None, start_time=start, end_time=end, is_available=True)
                db.session.add(new_sched)
                any_active = True
        db.session.flush()
        current_user.schedule = build_schedule_summary(current_user.id) if any_active else None
        flash('Horario semanal actualizado')
            
    elif action_type == 'blockout':
        date = request.form.get('specific_date')
        start = request.form.get('block_start')
        end = request.form.get('block_end')
        if date and start and end:
            if start >= end:
                flash('El bloqueo debe terminar después de la hora de inicio.')
                return redirect(url_for('doctor.dashboard'))
            block = DoctorSchedule(clinic_id=current_user.clinic_id, doctor_id=current_user.id, specific_date=date, day_of_week=None, start_time=start, end_time=end, is_available=False)
            db.session.add(block)
            flash('Día bloqueado correctamente')
    
    audit('doctor_schedule_updated', details=f'action={action_type}')
    db.session.commit()
    return redirect(url_for('doctor.dashboard'))

# --- RUTA RESTAURADA: BORRAR BLOQUEOS ---
@doctor_bp.route('/delete_blockout', methods=['POST'])
@login_required
@role_required('doctor')
def delete_blockout():
    block_id = request.form.get('block_id')
    block = DoctorSchedule.query.get_or_404(block_id)
    if block.doctor_id == current_user.id and block.specific_date is not None:
        db.session.delete(block)
        db.session.commit()
        flash('Bloqueo de agenda eliminado.')
    return redirect(url_for('doctor.dashboard'))

@doctor_bp.route('/set_chat_mode/<int:chat_id>/<mode>', methods=['POST'])
@login_required
@role_required('doctor')
def set_chat_mode(chat_id, mode):
    chat = Chat.query.get_or_404(chat_id)
    if chat.doctor_id != current_user.id or chat.clinic_id != current_user.clinic_id:
        return redirect(url_for('doctor.dashboard'))
    
    if mode == 'consultation':
        chat.mode = 'consultation'
        current_user.attending_chat_id = chat.id
        current_user.is_available = False 
        flash('Modo Consulta Activa.')
        today_str = colombia_now().strftime('%Y-%m-%d')
        appt = Appointment.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id, patient_id=chat.patient_id, date=today_str, status='pending').first()
        if appt: appt.status = 'attended'
        
    elif mode == 'read-only':
        chat.mode = 'read-only'
        if current_user.attending_chat_id == chat.id:
            current_user.attending_chat_id = None
            current_user.is_available = True
        flash('Modo Solo Lectura activado.')
        
    elif mode == 'triage':
        chat.mode = 'triage'
        if current_user.attending_chat_id == chat.id:
            current_user.attending_chat_id = None
            current_user.is_available = True
        flash('Modo Triaje activado.')
        
    db.session.commit()
    return redirect(url_for('doctor.chat', chat_id=chat.id))

@doctor_bp.route('/end_consultation/<int:chat_id>', methods=['POST'])
@login_required
@role_required('doctor')
def end_consultation(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    if chat.doctor_id != current_user.id or chat.clinic_id != current_user.clinic_id:
        return redirect(url_for('doctor.dashboard'))
    current_user.attending_chat_id = None
    current_user.is_available = True
    chat.mode = 'triage'
    db.session.commit()
    flash('Saliste de la atención. La consulta sigue abierta hasta usar Cerrar Sesión.')
    return redirect(url_for('doctor.chat', chat_id=chat.id))

@doctor_bp.route('/patient_history/<int:patient_id>')
@login_required
@role_required('doctor')
def patient_history(patient_id):
    patient = User.query.filter_by(
        id=patient_id, clinic_id=current_user.clinic_id, role='patient'
    ).first_or_404()

    chats = Chat.query.filter_by(doctor_id=current_user.id, patient_id=patient_id, clinic_id=current_user.clinic_id).order_by(Chat.timestamp.desc()).all()
    appointments = Appointment.query.filter_by(doctor_id=current_user.id, patient_id=patient_id, clinic_id=current_user.clinic_id).order_by(Appointment.date.desc()).all()

    # El acceso a una historia clinica se audita. Es lo que permite responder
    # despues a la pregunta de quien consulto los datos de un paciente.
    audit('patient_history_viewed', details=f'patient_id={patient.id}')
    db.session.commit()

    return render_template(
        'doctor_patient_history.html',
        patient=patient,
        chats=chats,
        appointments=appointments,
        allergies=_active_allergies_for(patient),
        conditions=PatientChronicCondition.query.filter_by(
            patient_id=patient.id, status='activa').all(),
        active_meds=_active_medications_for(patient),
        patient_age_years=_patient_age_years(patient),
    )


@doctor_bp.route('/patient/<int:patient_id>/alergias', methods=['POST'])
@login_required
@role_required('doctor')
def add_allergy(patient_id):
    """Registra una alergia del paciente.

    Este registro es la fuente que consulta la verificacion de seguridad antes de
    permitir la firma de una orden. Un paciente sin alergias registradas no es un
    paciente sin alergias: es un paciente del que no sabemos, y la interfaz lo
    dice con esas palabras para que nadie interprete el silencio como seguridad.
    """
    patient = User.query.filter_by(
        id=patient_id, clinic_id=current_user.clinic_id, role='patient'
    ).first_or_404()

    substance = bleach.clean((request.form.get('substance') or '').strip())[:180]
    if len(substance) < 2:
        flash('Indique la sustancia a la que el paciente es alergico.')
        return redirect(url_for('doctor.patient_history', patient_id=patient.id))

    severity = (request.form.get('severity') or 'moderada').strip().lower()
    if severity not in {'leve', 'moderada', 'grave', 'anafilaxia'}:
        severity = 'moderada'

    status = (request.form.get('status') or 'reportada').strip().lower()
    if status not in {'confirmada', 'reportada', 'descartada'}:
        status = 'reportada'

    normalized = normalize_drug(substance)

    existing = PatientAllergy.query.filter_by(
        patient_id=patient.id, substance_normalized=normalized
    ).first()
    if existing and existing.status != 'descartada':
        flash(f'"{substance}" ya esta registrada como alergia de este paciente.')
        return redirect(url_for('doctor.patient_history', patient_id=patient.id))

    allergy = PatientAllergy(
        clinic_id=current_user.clinic_id,
        patient_id=patient.id,
        substance=substance,
        substance_normalized=normalized or None,
        reaction=bleach.clean((request.form.get('reaction') or '').strip())[:300] or None,
        severity=severity,
        status=status,
        notes=bleach.clean((request.form.get('notes') or '').strip())[:500] or None,
        recorded_by_id=current_user.id,
    )
    db.session.add(allergy)
    audit(
        'patient_allergy_recorded',
        details=f'patient_id={patient.id}; severidad={severity}; estado={status}',
    )
    db.session.commit()

    flash(f'Alergia a "{substance}" registrada. Se verificara en cada prescripcion.')
    return redirect(url_for('doctor.patient_history', patient_id=patient.id))


@doctor_bp.route('/alergia/<int:allergy_id>/descartar', methods=['POST'])
@login_required
@role_required('doctor')
def discard_allergy(allergy_id):
    """Marca una alergia como descartada.

    No se borra: se conserva con el estado 'descartada'. Que una alergia se
    descartara, quien lo hizo y cuando forma parte de la historia clinica; una
    alergia mal atribuida tambien causa dano, al cerrar opciones terapeuticas.
    """
    allergy = PatientAllergy.query.filter_by(
        id=allergy_id, clinic_id=current_user.clinic_id
    ).first_or_404()

    reason = bleach.clean((request.form.get('reason') or '').strip())[:300]
    if len(reason) < 10:
        flash('Indique por que se descarta la alergia (minimo 10 caracteres).')
        return redirect(url_for('doctor.patient_history', patient_id=allergy.patient_id))

    allergy.status = 'descartada'
    allergy.notes = f'{allergy.notes or ""}\n[Descartada por {current_user.name}] {reason}'.strip()
    audit('patient_allergy_discarded', details=f'allergy_id={allergy.id}')
    db.session.commit()

    flash('Alergia marcada como descartada. Queda en el historial con su justificacion.')
    return redirect(url_for('doctor.patient_history', patient_id=allergy.patient_id))


@doctor_bp.route('/patient/<int:patient_id>/condicion', methods=['POST'])
@login_required
@role_required('doctor')
def add_condition(patient_id):
    """Registra una condicion cronica activa del paciente."""
    patient = User.query.filter_by(
        id=patient_id, clinic_id=current_user.clinic_id, role='patient'
    ).first_or_404()

    condition = bleach.clean((request.form.get('condition') or '').strip())[:200]
    if len(condition) < 3:
        flash('Describa la condicion cronica.')
        return redirect(url_for('doctor.patient_history', patient_id=patient.id))

    cie10 = (request.form.get('cie10_code') or '').strip().upper()[:10]
    if cie10:
        valid, result = validate_medical_code(cie10, 'CIE10')
        if not valid:
            flash(result)
            return redirect(url_for('doctor.patient_history', patient_id=patient.id))
        cie10 = result

    db.session.add(PatientChronicCondition(
        clinic_id=current_user.clinic_id,
        patient_id=patient.id,
        condition=condition,
        cie10_code=cie10 or None,
        recorded_by_id=current_user.id,
    ))
    audit('patient_condition_recorded', details=f'patient_id={patient.id}; cie10={cie10}')
    db.session.commit()

    flash('Condicion cronica registrada.')
    return redirect(url_for('doctor.patient_history', patient_id=patient.id))

@doctor_bp.route('/export_clinical_records')
@login_required
@role_required('doctor')
def export_clinical_records():
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'tipo_registro', 'clinic_id', 'doctor', 'paciente', 'cedula_paciente',
        'fecha', 'hora', 'estado', 'motivo_descripcion', 'chat_id', 'mensaje_de',
        'mensaje', 'archivo'
    ])

    # Toda celda pasa por `csv_safe_row`. Sin ello, un nombre de paciente que
    # empiece por "=", "+", "-" o "@" se ejecuta como formula al abrir el archivo
    # en Excel, en el equipo del auditor que lo recibe.
    appointments = Appointment.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).order_by(Appointment.date.asc(), Appointment.time.asc()).all()
    for appt in appointments:
        writer.writerow(csv_safe_row([
            'cita', current_user.clinic_id, current_user.name, appt.patient.name, appt.patient.cedula,
            appt.date, appt.time, appt.status, appt.description or '', appt.chat.id if appt.chat else '',
            '', '', ''
        ]))

    chats = Chat.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id).order_by(Chat.timestamp.asc()).all()
    for chat in chats:
        writer.writerow(csv_safe_row([
            'consulta', current_user.clinic_id, current_user.name, chat.patient.name, chat.patient.cedula,
            colombia_strftime(chat.timestamp, '%Y-%m-%d'), colombia_strftime(chat.timestamp, '%H:%M'),
            chat.status, chat.reason or '', chat.id, '', '', ''
        ]))
        messages = Message.query.filter_by(chat_id=chat.id, clinic_id=current_user.clinic_id).order_by(Message.timestamp.asc()).all()
        for msg in messages:
            writer.writerow(csv_safe_row([
                'mensaje', current_user.clinic_id, current_user.name, chat.patient.name, chat.patient.cedula,
                colombia_strftime(msg.timestamp, '%Y-%m-%d'), colombia_strftime(msg.timestamp, '%H:%M'),
                chat.status, chat.reason or '', chat.id, msg.sender.name, msg.content or '', msg.file_path or ''
            ]))

    audit(
        'clinical_records_exported',
        details=f'citas={len(appointments)}; consultas={len(chats)}',
    )
    db.session.commit()
    filename = f"historia_clinica_doctor_{current_user.id}.csv"
    return Response(
        # BOM UTF-8: sin el, Excel en Windows interpreta el archivo como ANSI y
        # los nombres con tilde salen corrompidos en el documento que se radica.
        '﻿' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'X-Content-Type-Options': 'nosniff',
        },
    )

@doctor_bp.route('/export_rips')
@login_required
@role_required('doctor', 'admin')
def export_rips():
    appointments = Appointment.query.filter_by(clinic_id=current_user.clinic_id, status='attended').order_by(Appointment.date.asc(), Appointment.time.asc()).all()
    rips = {
        'nota': 'Borrador JSON RIPS-FEV. Completar códigos oficiales, factura electrónica y validación MUV antes de envío.',
        'norma_base': 'Resolución 2275 de 2023 y modificatorias',
        'prestador': {
            'clinic_id': current_user.clinic_id,
            'nombre': current_user.clinic.name if current_user.clinic else '',
            'nit': current_user.clinic.nit if current_user.clinic else ''
        },
        'usuarios': [],
        'servicios': {'consultas': []}
    }
    seen_patients = set()
    for appt in appointments:
        if appt.patient_id not in seen_patients:
            seen_patients.add(appt.patient_id)
            rips['usuarios'].append({
                'idUsuario': appt.patient_id,
                'nombre': appt.patient.name,
                'documento': appt.patient.cedula
            })
        rips['servicios']['consultas'].append({
            'fechaAtencion': appt.date,
            'horaAtencion': appt.time,
            'idPaciente': appt.patient_id,
            'idDoctor': appt.doctor_id,
            'finalidad': 'telemedicina_rural',
            'motivoConsulta': appt.description or '',
            'chatId': appt.chat.id if appt.chat else None
        })
    audit('rips_exported')
    db.session.commit()
    return Response(
        json.dumps(rips, ensure_ascii=False, indent=2),
        mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename="rips_borrador.json"'}
    )

# --- RUTA COMPLETADA: AGENDAR CITA CALENDARIO DOCTOR ---
@doctor_bp.route('/book_appointment/<int:patient_id>', methods=['GET', 'POST'])
@login_required
@role_required('doctor')
def book_appointment(patient_id):
    
    patient = User.query.get_or_404(patient_id)
    if patient.clinic_id != current_user.clinic_id:
        return redirect(url_for('doctor.dashboard'))

    c = calendar.Calendar(firstweekday=0)
    month_str = request.args.get('month', colombia_now().strftime('%Y-%m'))
    current_view_date = datetime.strptime(month_str, '%Y-%m')
    
    month_days = []
    for d in c.itermonthdates(current_view_date.year, current_view_date.month):
        day_of_week = d.weekday()
        works = DoctorSchedule.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id, day_of_week=day_of_week, specific_date=None, is_available=True).first() is not None
        month_days.append({
            'date_str': d.strftime('%Y-%m-%d'),
            'day_num': d.day,
            'is_current_month': d.month == current_view_date.month,
            'is_working_day': works,
            'is_past': d < colombia_now().date()
        })
        
    next_month = (current_view_date.replace(day=28) + timedelta(days=5)).replace(day=1)
    prev_month = (current_view_date.replace(day=1) - timedelta(days=1)).replace(day=1)

    if request.method == 'POST':
        date = request.form.get('date')
        time = request.form.get('time')
        desc = (request.form.get('description') or '')[:1000]
        
        # Solución: Validar que no sea en el pasado
        try:
            appt_datetime = datetime.strptime(f"{date} {time}", '%Y-%m-%d %H:%M')
            if appt_datetime < colombia_now() - timedelta(minutes=5):  # 5 min de margen
                flash('No es posible agendar citas en una fecha pasada.')
                return redirect(request.referrer or url_for('patient.dashboard'))
        except ValueError:
            flash('Formato de fecha u hora inválido.')
            return redirect(request.referrer or url_for('patient.dashboard'))
        
        exists = Appointment.query.filter(
            Appointment.doctor_id == current_user.id,
            Appointment.clinic_id == current_user.clinic_id,
            Appointment.date == date,
            Appointment.time == time,
            Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
        ).first()
        if exists:
            flash('Esa hora ya esta ocupada.')
            return redirect(url_for('doctor.book_appointment', patient_id=patient_id, date=date))

        appt = Appointment(clinic_id=current_user.clinic_id, patient_id=patient_id,
                           doctor_id=current_user.id, date=date, time=time, description=desc)
        db.session.add(appt)
        try:
            db.session.commit()
        except IntegrityError:
            # El indice unico de la base cierra la ventana entre la comprobacion
            # anterior y esta insercion.
            db.session.rollback()
            flash('Esa hora acaba de ser tomada. Seleccione otra.')
            return redirect(url_for('doctor.book_appointment', patient_id=patient_id, date=date))

        audit('appointment_booked_by_doctor', details=f'patient_id={patient_id}; fecha={date} {time}')
        db.session.commit()
        flash(f'Cita agendada para {patient.name}.')
        return redirect(url_for('doctor.dashboard'))
    
    selected_date = request.args.get('date')
    slots = []
    
    if selected_date:
        date_obj = datetime.strptime(selected_date, '%Y-%m-%d')
        day_of_week = date_obj.weekday()
        weekly_sched = DoctorSchedule.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id, day_of_week=day_of_week, specific_date=None, is_available=True).first()
        
        if weekly_sched:
            start_dt = datetime.strptime(weekly_sched.start_time, '%H:%M')
            end_dt = datetime.strptime(weekly_sched.end_time, '%H:%M')
            current_slot = start_dt
            while current_slot < end_dt:
                slots.append(current_slot.strftime('%H:%M'))
                current_slot += timedelta(minutes=15)
        
        booked_slots = [appt.time for appt in Appointment.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id, date=selected_date).all()]
        blockouts = DoctorSchedule.query.filter_by(doctor_id=current_user.id, clinic_id=current_user.clinic_id, specific_date=selected_date, is_available=False).all()
        blocked_slots = []
        
        for block in blockouts:
            block_start = datetime.strptime(block.start_time, '%H:%M')
            block_end = datetime.strptime(block.end_time, '%H:%M')
            current_b = block_start
            while current_b < block_end:
                blocked_slots.append(current_b.strftime('%H:%M'))
                current_b += timedelta(minutes=15)
        
        final_slots = []
        for s in slots:
            if s in blocked_slots:
                final_slots.append({'time': s, 'status': 'blocked'})
            elif s in booked_slots:
                final_slots.append({'time': s, 'status': 'booked'})
            else:
                final_slots.append({'time': s, 'status': 'available'})
                
        return render_template('doctor_appointment.html', 
                               patient=patient, 
                               selected_date=selected_date, 
                               slots=final_slots, 
                               month_days=month_days,
                               current_view_date=current_view_date,
                               prev_month=prev_month,
                               next_month=next_month)
        
    return render_template('doctor_appointment.html', 
                           patient=patient, 
                           selected_date=None, 
                           slots=[], 
                           month_days=month_days,
                           current_view_date=current_view_date,
                           prev_month=prev_month,
                           next_month=next_month)

# --- RUTAS DE SALA DE ESPERA Y NO-SHOW (DOCTOR) ---
@doctor_bp.route('/join_appointment/<int:appt_id>')
@login_required
@role_required('doctor')
def join_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    
    if appt.doctor_id != current_user.id or appt.clinic_id != current_user.clinic_id or appt.status != 'pending':
        flash('No es posible unirse a esta cita.')
        return redirect(url_for('doctor.dashboard'))
        
    # --- LÓGICA DE HORA EXACTA ---
    now = colombia_now()
    try:
        appt_datetime = datetime.strptime(f"{appt.date} {appt.time}", '%Y-%m-%d %H:%M')
    except ValueError:
        flash('Formato de fecha/hora de la cita inválido.')
        return redirect(url_for('doctor.dashboard'))

    # 1. No puedes entrar ANTES de la hora
    if now < appt_datetime:
        flash(f'Aún no es la hora. La consulta puede iniciarse a partir de las {appt.time}.')
        return redirect(url_for('doctor.dashboard'))
        
    # 2. Solo tienes 2 minutos de tolerancia si llegas tarde
    if now > appt_datetime + timedelta(minutes=2):
        flash('El tiempo para iniciar esta cita ha expirado.')
        return redirect(url_for('doctor.dashboard'))
    # --------------------------------------

    appt.status = 'in_progress'
    appt.started_at = colombia_now()
    
    chat = Chat.query.filter_by(appointment_id=appt.id, clinic_id=current_user.clinic_id).first()
    if not chat:
        chat = Chat(clinic_id=current_user.clinic_id, patient_id=appt.patient_id, doctor_id=current_user.id, reason=f"Cita: {appt.description or 'General'}", appointment_id=appt.id)
        db.session.add(chat)
        
    db.session.commit()
    return redirect(url_for('doctor.chat', chat_id=chat.id))

@doctor_bp.route('/mark_no_show/<int:appt_id>', methods=['POST'])
@login_required
@role_required('doctor')
def mark_no_show(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    if appt.doctor_id != current_user.id or appt.clinic_id != current_user.clinic_id:
        return redirect(url_for('doctor.dashboard'))
    appt.status = 'no_show'
    chat = Chat.query.filter_by(appointment_id=appt.id, clinic_id=current_user.clinic_id).first()
    if chat:
        chat.status = 'closed'
        chat.closed_by = 'system'
    db.session.commit()
    flash('Paciente marcado como No Asistido.')
    return redirect(url_for('doctor.dashboard'))
