from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from models import APPOINTMENT_FREEING_STATUSES, db, User, Chat, Message, Appointment, Rating, QuestionFlow, Favorite, DoctorSchedule, MedicalOrder, MedicationPickupTicket, Notification, Pharmacy, Stock, STAFF_ROLE_VALUES, Clinic, PaymentVerificationTicket, PatientDoctorSubscription, PAYMENT_PENDING
import json
import calendar
from datetime import datetime, timedelta
from time_utils import colombia_now, colombia_strftime
from security import audit, role_required, save_secure_upload, limiter
from pharmacy_utils import available_quantity, distance_km, doctor_distance, doctor_visible_on_map, pharmacy_distance
import hashlib
from monetization import (
    PLAN_LABELS,
    active_doctor_subscription,
    best_policy_discount,
    clinic_access_allowed,
    clinic_access_state,
    discounted_amount,
    expiration_for_plan,
    patient_has_doctor_access,
    price_for_plan,
    tariff_for_doctor,
    tariff_prices,
)
import bleach

# --- FUNCIONES AUXILIARES ---
def is_doctor_currently_on_duty(doctor):
    if not doctor:
        return False
    if doctor.role in STAFF_ROLE_VALUES:
        start = doctor.atencion_inicio or '08:00'
        end = doctor.atencion_fin or '17:00'
        current_time = colombia_now().strftime('%H:%M')
        return start <= current_time <= end
    if not doctor.is_available: return False
    now = colombia_now()
    day_of_week = now.weekday()
    current_time = now.strftime('%H:%M')
    schedule = DoctorSchedule.query.filter_by(doctor_id=doctor.id, clinic_id=doctor.clinic_id, day_of_week=day_of_week, specific_date=None, is_available=True).first()
    if schedule and schedule.start_time <= current_time <= schedule.end_time:
        return True
    return False

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

def get_doctor_availability(doctor, days=5):
    availability = []
    today = colombia_now().date()
    dias_semana = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
    for i in range(days):
        current_date = today + timedelta(days=i)
        day_of_week = current_date.weekday()
        schedule = DoctorSchedule.query.filter_by(
            doctor_id=doctor.id,
            clinic_id=doctor.clinic_id,
            day_of_week=day_of_week,
            specific_date=None,
            is_available=True,
        ).execution_options(include_all_clinics=True).first()
        if not schedule:
            availability.append({'date': current_date, 'day_name': dias_semana[day_of_week], 'slots': 0})
            continue
        try:
            start_dt = datetime.strptime(schedule.start_time, '%H:%M')
            end_dt = datetime.strptime(schedule.end_time, '%H:%M')
            total_slots = 0
            current_slot = start_dt
            while current_slot < end_dt:
                total_slots += 1
                current_slot += timedelta(minutes=15)
            # Solo cuentan las citas que realmente ocupan el horario. Contar tambien
            # las canceladas y no asistidas hacia que el paciente viera menos cupos
            # de los que habia, y en una agenda rural escasa eso son consultas que
            # nadie llega a tomar.
            booked = Appointment.query.filter(
                Appointment.doctor_id == doctor.id,
                Appointment.clinic_id == doctor.clinic_id,
                Appointment.date == current_date.strftime('%Y-%m-%d'),
                Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
            ).execution_options(include_all_clinics=True).count()
            avail_slots = max(0, total_slots - booked)
            availability.append({'date': current_date, 'day_name': dias_semana[day_of_week], 'slots': avail_slots})
        except Exception:
            availability.append({'date': current_date, 'day_name': dias_semana[day_of_week], 'slots': 0})
    return availability

def process_next_flow_node(chat, next_node_id, flow_dict):
    if next_node_id and next_node_id in flow_dict['nodes']:
        next_node = flow_dict['nodes'][next_node_id]
        chat.active_flow_node = next_node_id
        input_type = next_node.get('input_type', 'option')
        has_options = bool(next_node.get('options'))
        next_q_msg = Message(
            clinic_id=chat.clinic_id,
            chat_id=chat.id, sender_id=chat.doctor_id, content=next_node['text'], 
            is_flow_question=True if has_options or input_type == 'open' else False, 
            flow_options=json.dumps(next_node['options']) if has_options else None, 
            flow_input_type=input_type if not has_options else 'option'
        )
        db.session.add(next_q_msg)
    else:
        chat.active_flow_id = None
        chat.active_flow_node = None

patient_bp = Blueprint('patient', __name__)

def has_sensitive_data_consent():
    return bool(current_user.sensitive_data_consent_at)

def require_sensitive_data_consent():
    if not has_sensitive_data_consent():
        flash('Antes de iniciar o continuar una consulta debe autorizar el tratamiento de datos sensibles de salud.')
        return redirect(url_for('patient.consent'))
    return None

@patient_bp.route('/consentimiento-datos-sensibles', methods=['GET', 'POST'])
@login_required
@role_required('patient')
def consent():
    if request.method == 'POST':
        if request.form.get('accept_sensitive_data') != 'on':
            flash('Debe marcar la autorización explícita para usar chat, citas y seguimiento clínico.')
            return redirect(url_for('patient.consent'))
            
        now = colombia_now()
        current_user.sensitive_data_consent_at = now
        
        # Firma Digital (SHA-256) - Refinada con hash de cédula para consistencia
        ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr)
        user_agent = request.headers.get('User-Agent', '')
        signature_base = f"{current_user.id}|{current_user.cedula_hash}|{now.isoformat()}|{ip_addr}|{user_agent}"
        digital_signature = hashlib.sha256(signature_base.encode('utf-8')).hexdigest()
        
        from models import InformedConsentLog
        consent_log = InformedConsentLog(
            patient_id=current_user.id,
            clinic_id=current_user.clinic_id,
            consent_type='sensitive_data',
            ip_address=ip_addr,
            user_agent=user_agent,
            digital_signature_hash=digital_signature,
            timestamp=now
        )
        db.session.add(consent_log)
        
        audit('sensitive_data_consent_accepted', details=f"hash={digital_signature[:8]}...; ip={ip_addr}")
        db.session.commit()
        flash('Autorización registrada con firma digital. Ya puede iniciar su consulta.')
        return redirect(url_for('patient.dashboard'))
    from app import legal_context
    return render_template('legal.html', consent_required=True, **legal_context('privacy'))

# --- DASHBOARD ---
@patient_bp.route('/dashboard')
@login_required
@role_required('patient')
def dashboard():
    
    search_query = request.args.get('q', '').lower()
    selected_doctor_id = request.args.get('doctor_id', type=int)
    near_me = request.args.get('near') == '1'
    mode_filter = request.args.get('mode') or ''
    all_doctors = User.query.filter(
        User.role == 'doctor',
        or_(User.clinic_id == current_user.clinic_id, User.is_autonomous == True),
    ).execution_options(include_all_clinics=True).all()
    
    if search_query:
        all_doctors = [d for d in all_doctors if search_query in d.name.lower() or search_query in (d.specialty or '').lower()]
    if mode_filter == 'presencial':
        all_doctors = [d for d in all_doctors if doctor_visible_on_map(d)]
    elif mode_filter == 'telemedicina':
        all_doctors = [d for d in all_doctors if d.is_available]
    
    doctor_data = []
    for doc in all_doctors:
        avail = get_doctor_availability(doc)
        is_fav = Favorite.query.filter_by(patient_id=current_user.id, clinic_id=current_user.clinic_id, doctor_id=doc.id).first() is not None
        recent_reviews = Rating.query.filter(Rating.doctor_id==doc.id, Rating.comment != None, Rating.comment != '').order_by(Rating.id.desc()).limit(3).all()
        distance = doctor_distance(current_user, doc) if doctor_visible_on_map(doc) else None
        tariff = tariff_for_doctor(doc)
        discount_percent, policy_enrollment, policy_provider = best_policy_discount(current_user, doc)
        has_access = patient_has_doctor_access(current_user, doc)
        subscription = active_doctor_subscription(current_user.id, doc.id) if doc.is_autonomous else None
        doctor_data.append({
            'doc': doc,
            'avail': avail,
            'is_fav': is_fav,
            'reviews': recent_reviews,
            'distance_km': distance,
            'tariff': tariff,
            'prices': tariff_prices(tariff, discount_percent),
            'discount_percent': discount_percent,
            'policy_enrollment': policy_enrollment,
            'policy_provider': policy_provider,
            'has_access': has_access,
            'subscription': subscription,
            'locked': not has_access,
        })
    if near_me:
        doctor_data.sort(key=lambda data: data['distance_km'] if data['distance_km'] is not None else 999999)
        
    fav_data = [d for d in doctor_data if d['is_fav']]
    avail_data = [d for d in doctor_data if not d['is_fav'] and d['doc'].is_available]
    unavail_data = [d for d in doctor_data if not d['is_fav'] and not d['doc'].is_available]
    
    chats_query = Chat.query.filter_by(patient_id=current_user.id).execution_options(include_all_clinics=True)
    if selected_doctor_id:
        chats_query = chats_query.filter_by(doctor_id=selected_doctor_id)
    chats = chats_query.order_by(Chat.id.desc()).all()
    chat_data = []
    for c in chats:
        unread = Message.query.filter(Message.chat_id == c.id, Message.sender_id == c.doctor_id, Message.is_read == False).execution_options(include_all_clinics=True).count()
        last_msg = Message.query.filter_by(chat_id=c.id).execution_options(include_all_clinics=True).order_by(Message.timestamp.desc()).first()
        has_rated = Rating.query.filter_by(chat_id=c.id, patient_id=current_user.id).first() is not None
        chat_data.append({'chat': c, 'unread': unread, 'last_msg': last_msg, 'has_rated': has_rated})
    
    active_chats = [item for item in chat_data if item['chat'].status == 'open']
    closed_chats = [item for item in chat_data if item['chat'].status == 'closed']
    
    active_doctor_chats = [item for item in active_chats if item['chat'].doctor.role == 'doctor']
    active_staff_chats = [item for item in active_chats if item['chat'].doctor.role != 'doctor']
    closed_doctor_chats = [item for item in closed_chats if item['chat'].doctor.role == 'doctor']
    closed_staff_chats = [item for item in closed_chats if item['chat'].doctor.role != 'doctor']
    
    doctors_with_chats = User.query.join(Chat, Chat.doctor_id == User.id).filter(Chat.patient_id == current_user.id, User.role == 'doctor').distinct().order_by(User.name.asc()).execution_options(include_all_clinics=True).all()
    staff_with_chats = User.query.join(Chat, Chat.doctor_id == User.id).filter(Chat.patient_id == current_user.id, User.role != 'doctor').distinct().order_by(User.name.asc()).execution_options(include_all_clinics=True).all()
    
    appointments = Appointment.query.filter_by(patient_id=current_user.id).execution_options(include_all_clinics=True).all()
    medical_orders = MedicalOrder.query.filter_by(patient_id=current_user.id, clinic_id=current_user.clinic_id).order_by(MedicalOrder.created_at.desc()).limit(10).all()
    clinic_rows = [
        clinic_access_state(current_user, clinic)
        for clinic in Clinic.query.filter_by(status='active').order_by(Clinic.name.asc()).all()
    ]
    payment_tickets = PaymentVerificationTicket.query.filter_by(patient_id=current_user.id).execution_options(include_all_clinics=True).order_by(PaymentVerificationTicket.created_at.desc()).limit(8).all()
    
    # Tickets de recogida de medicamentos (activos y recientes)
    pickup_tickets = MedicationPickupTicket.query.filter(
        MedicationPickupTicket.patient_id == current_user.id,
        MedicationPickupTicket.clinic_id == current_user.clinic_id,
        MedicationPickupTicket.status.in_(['autorizado', 'sin_stock']),
    ).order_by(MedicationPickupTicket.created_at.desc()).limit(10).all()
    # También mostrar entregas recientes (últimos 3 días)
    from datetime import timedelta
    cutoff = colombia_now() - timedelta(days=3)
    recent_deliveries = MedicationPickupTicket.query.filter(
        MedicationPickupTicket.patient_id == current_user.id,
        MedicationPickupTicket.clinic_id == current_user.clinic_id,
        MedicationPickupTicket.status == 'entregado',
        MedicationPickupTicket.delivered_at >= cutoff,
    ).order_by(MedicationPickupTicket.delivered_at.desc()).limit(3).all()
    all_pickup_tickets = list(pickup_tickets) + list(recent_deliveries)
    today_str = colombia_now().strftime('%Y-%m-%d')
    return render_template(
        'patient_dashboard.html',
        fav_data=fav_data,
        avail_data=avail_data,
        unavail_data=unavail_data,
        active_chats=active_chats,
        active_doctor_chats=active_doctor_chats,
        active_staff_chats=active_staff_chats,
        closed_doctor_chats=closed_doctor_chats,
        closed_staff_chats=closed_staff_chats,
        doctors_with_chats=doctors_with_chats,
        staff_with_chats=staff_with_chats,
        selected_doctor_id=selected_doctor_id,
        appointments=appointments,
        medical_orders=medical_orders,
        pickup_tickets=all_pickup_tickets,
        today_str=today_str,
        search_query=search_query,
        near_me=near_me,
        mode_filter=mode_filter,
        clinic_rows=clinic_rows,
        payment_tickets=payment_tickets,
        needs_sensitive_consent=not has_sensitive_data_consent()
    )

@patient_bp.route('/notify_arriving/<int:ticket_id>', methods=['POST'])
@login_required
@role_required('patient')
def notify_arriving(ticket_id):
    """Patient notifies expendedor/staff that they are on their way to pick up meds."""
    ticket = MedicationPickupTicket.query.filter(
        MedicationPickupTicket.id == ticket_id,
        MedicationPickupTicket.patient_id == current_user.id,
        MedicationPickupTicket.clinic_id == current_user.clinic_id,
        MedicationPickupTicket.status.in_(['autorizado', 'parcial'])
    ).first()
    if not ticket:
        flash('Ticket no disponible o ya entregado.')
        return redirect(url_for('patient.dashboard'))

    # Un aviso cada media hora es suficiente.
    #
    # Media hora es el orden de magnitud de un desplazamiento en vereda: si el
    # paciente vuelve a pulsar antes, es que no vio que ya habia avisado, no
    # que este avisando de otra cosa. Se le confirma cuando aviso, en lugar de
    # volver a notificar a toda la farmacia.
    if ticket.arrival_notified_at:
        desde = colombia_now() - ticket.arrival_notified_at
        if desde.total_seconds() < 30 * 60:
            hora = colombia_strftime(ticket.arrival_notified_at, '%H:%M')
            flash(f'Ya avisaste a las {hora}. La farmacia esta enterada; '
                  f'no hace falta volver a avisar.')
            return redirect(url_for('patient.dashboard'))

    # Notify the expendedores assigned to that pharmacy
    notified_count = 0
    from models import User, ROLE_EXPENDOR
    target_users = []
    
    if ticket.pharmacy_id:
        # Find all expendedores in that pharmacy
        pharmacy_expendedores = User.query.filter_by(
            clinic_id=current_user.clinic_id,
            role=ROLE_EXPENDOR,
            pharmacy_id=ticket.pharmacy_id
        ).all()
        target_users.extend(pharmacy_expendedores)
    
    # Also notify the staff who created the ticket (doctor/nurse)
    if ticket.staff_id:
        creator_staff = User.query.get(ticket.staff_id)
        if creator_staff and creator_staff not in target_users:
            target_users.append(creator_staff)

    for target in target_users:
        notif = Notification(
            user_id=target.id,
            clinic_id=current_user.clinic_id,
            title='Paciente en camino',
            message=f'El paciente {current_user.name} notificó que viene en camino para recoger el ticket {ticket.pickup_code}. Alista el stock si es necesario.',
            type='patient_arriving',
        )
        db.session.add(notif)
        notified_count += 1

    if notified_count > 0:
        # La marca es lo que permite ensenarle al paciente que ya aviso, y lo
        # que impide que el siguiente clic vuelva a notificar a todos.
        ticket.arrival_notified_at = colombia_now()
        audit('patient_notified_arriving', details=f'ticket_id={ticket.id}; notified={notified_count}')
        db.session.commit()
        flash('Notificacion enviada. El personal de la farmacia fue avisado de su llegada.')
    else:
        flash('Ticket valido. Presentese en la farmacia con su codigo.')
    return redirect(url_for('patient.dashboard'))


@patient_bp.route('/api/offline_agenda')
@limiter.exempt
@login_required
@role_required('patient')
def offline_agenda():
    appointments = Appointment.query.filter_by(patient_id=current_user.id).execution_options(include_all_clinics=True).order_by(Appointment.date.asc(), Appointment.time.asc()).all()
    active = Chat.query.filter_by(patient_id=current_user.id, status='open').execution_options(include_all_clinics=True).order_by(Chat.timestamp.desc()).all()
    return jsonify({
        'synced_at': colombia_now().isoformat(),
        'appointments': [
            {
                'id': appt.id,
                'doctor': appt.doctor.name,
                'date': appt.date,
                'time': appt.time,
                'status': appt.status,
                'description': appt.description or ''
            }
            for appt in appointments
        ],
        'active_consultations': [
            {
                'id': chat.id,
                'doctor': chat.doctor.name,
                'reason': chat.reason or '',
                'status': chat.status
            }
            for chat in active
        ]
    })


@patient_bp.route('/location', methods=['POST'])
@login_required
@role_required('patient')
def update_location():
    if request.form.get('accept_location') != 'on':
        flash('Debe autorizar el uso de ubicacion para guardar coordenadas.')
        return redirect(request.referrer or url_for('patient.dashboard'))
    current_user.address = bleach.clean((request.form.get('address') or '').strip()[:220]) or None
    current_user.latitude = request.form.get('latitude', type=float)
    current_user.longitude = request.form.get('longitude', type=float)
    current_user.location_consent_at = colombia_now()
    audit('patient_location_updated')
    db.session.commit()
    flash('Ubicacion guardada para calculos de cercania.')
    return redirect(request.referrer or url_for('patient.map_view'))


@patient_bp.route('/map')
@login_required
@role_required('patient')
def map_view():
    return render_template('patient_map.html')


@patient_bp.route('/api/map_points')
@login_required
@role_required('patient')
def map_points():
    clinics = Clinic.query.filter_by(status='active').order_by(Clinic.name.asc()).all()
    clinic_points = [
        {
            'id': clinic.id,
            'type': 'clinic',
            'name': clinic.name,
            'address': clinic.location or '',
            'lat': clinic.latitude,
            'lng': clinic.longitude,
            'distance_km': distance_km(current_user.latitude, current_user.longitude, clinic.latitude, clinic.longitude),
            'access_type': clinic.access_type,
            'locked': not clinic_access_allowed(current_user, clinic),
            'opening_hours': clinic.opening_hours or '',
        }
        for clinic in clinics
        if clinic.latitude is not None and clinic.longitude is not None
    ]
    pharmacies = Pharmacy.query.filter_by(clinic_id=current_user.clinic_id, is_active=True).order_by(Pharmacy.name.asc()).all()
    pharmacy_points = []
    for pharmacy in pharmacies:
        stocks = Stock.query.filter_by(clinic_id=current_user.clinic_id, pharmacy_id=pharmacy.id).order_by(Stock.nombre_med.asc()).all()
        available_stocks = [
            {
                'name': stock.nombre_med,
                'available': available_quantity(stock),
                'unit': stock.unidad,
            }
            for stock in stocks
            if available_quantity(stock) > 0
        ]
        pharmacy_points.append({
            'id': pharmacy.id,
            'type': 'pharmacy',
            'name': pharmacy.name,
            'address': pharmacy.address or '',
            'lat': pharmacy.latitude,
            'lng': pharmacy.longitude,
            'distance_km': pharmacy_distance(current_user, pharmacy),
            'has_stock': bool(available_stocks),
            'stock': available_stocks[:8],
        })

    doctors = User.query.filter(
        User.role == 'doctor',
        or_(User.clinic_id == current_user.clinic_id, User.is_autonomous == True),
        User.show_office_on_map == True,
        User.office_latitude.isnot(None),
        User.office_longitude.isnot(None),
    ).order_by(User.name.asc()).execution_options(include_all_clinics=True).all()
    doctor_points = [
        {
            'id': doctor.id,
            'type': 'doctor',
            'name': doctor.name,
            'specialty': doctor.specialty or 'Medicina General',
            'address': doctor.office_address or '',
            'lat': doctor.office_latitude,
            'lng': doctor.office_longitude,
            'distance_km': doctor_distance(current_user, doctor),
            'affiliation': doctor.affiliation_name or (doctor.clinic.name if doctor.clinic else 'Autonomo'),
            'is_autonomous': bool(doctor.is_autonomous),
            'available': bool(doctor.is_available),
            'schedule': doctor.schedule or '',
        }
        for doctor in doctors
    ]
    return jsonify({
        'synced_at': colombia_now().isoformat(),
        'patient': {
            'name': current_user.name,
            'address': current_user.address or '',
            'lat': current_user.latitude,
            'lng': current_user.longitude,
        },
        'clinics': clinic_points,
        'pharmacies': pharmacy_points,
        'doctors': doctor_points,
    })

# --- CHAT ---
@patient_bp.route('/chat/<int:chat_id>', methods=['GET', 'POST'])
@login_required
@role_required('patient')
def chat(chat_id):
    chat_obj = Chat.query.filter_by(id=chat_id).execution_options(include_all_clinics=True).first_or_404()
    if chat_obj.patient_id != current_user.id:
        return redirect(url_for('patient.dashboard'))
    consent_redirect = require_sensitive_data_consent()
    if consent_redirect:
        return consent_redirect
    doctor = User.query.filter_by(id=chat_obj.doctor_id).execution_options(include_all_clinics=True).first()
    
    is_on_duty = is_doctor_currently_on_duty(doctor)
    is_read_only = chat_obj.mode == 'read-only'
    is_doctor_busy_elsewhere = (doctor.attending_chat_id is not None and doctor.attending_chat_id != chat_obj.id)

    if request.method == 'POST':
        # Seguridad principal
        if not is_on_duty or is_read_only or is_doctor_busy_elsewhere:
            if doctor and doctor.role in STAFF_ROLE_VALUES and not is_on_duty:
                flash('El personal está fuera de su horario laboral.')
            else:
                flash('No se puede enviar mensajes en este momento.')
            return redirect(url_for('patient.chat', chat_id=chat_obj.id))

        action = request.form.get('action')

        # Acción: Agendar desde el chat
        if action == 'schedule_appointment_chat':
            date = request.form.get('date'); time = request.form.get('time'); desc = bleach.clean((request.form.get('description') or '')[:1000])
            if date and time:
                occupied = Appointment.query.filter(
                    Appointment.doctor_id == chat_obj.doctor_id,
                    Appointment.clinic_id == chat_obj.clinic_id,
                    Appointment.date == date,
                    Appointment.time == time,
                    Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
                ).execution_options(include_all_clinics=True).first()
                if occupied:
                    flash('Esa hora ya esta ocupada.')
                else:
                    appt = Appointment(clinic_id=chat_obj.clinic_id, patient_id=chat_obj.patient_id,
                                       doctor_id=chat_obj.doctor_id, date=date, time=time, description=desc)
                    db.session.add(appt)
                    # El texto se guarda cifrado en la base y sale en la exportacion
                    # de historia clinica: sin emoji, que en Android antiguo puede
                    # renderizar como un cuadro vacio en un registro clinico.
                    db.session.add(Message(
                        clinic_id=chat_obj.clinic_id, chat_id=chat_obj.id,
                        sender_id=current_user.id,
                        content=f'Cita agendada: {date} a las {time}',
                    ))
                    try:
                        db.session.flush()
                        flash('Cita agendada desde el chat.')
                    except IntegrityError:
                        db.session.rollback()
                        flash('Esa hora acaba de ser tomada. Seleccione otra.')
                        return redirect(url_for('patient.chat', chat_id=chat_obj.id))

        # Acción: Respuesta a Flujo (Botones)
        elif 'flow_response' in request.form:
            option_key = request.form.get('flow_response')
            if chat_obj.active_flow_id:
                flow = db.session.get(QuestionFlow, chat_obj.active_flow_id)
                flow_dict = json.loads(flow.flow_data)
                current_node = flow_dict['nodes'].get(chat_obj.active_flow_node)
                if current_node and option_key in current_node['options']:
                    ans_msg = Message(clinic_id=chat_obj.clinic_id, chat_id=chat_obj.id, sender_id=current_user.id, content=current_node['options'][option_key].get('text', option_key))
                    db.session.add(ans_msg)
                    process_next_flow_node(chat_obj, current_node['options'][option_key].get('next'), flow_dict)
        
        # Acción: Respuesta a Flujo (Texto Abierto)
        elif 'flow_response_open' in request.form:
            open_answer = bleach.clean((request.form.get('flow_response_open') or '').strip()[:5000])
            if chat_obj.active_flow_id and open_answer:
                flow = db.session.get(QuestionFlow, chat_obj.active_flow_id)
                flow_dict = json.loads(flow.flow_data)
                current_node = flow_dict['nodes'].get(chat_obj.active_flow_node)
                ans_msg = Message(clinic_id=chat_obj.clinic_id, chat_id=chat_obj.id, sender_id=current_user.id, content=open_answer)
                db.session.add(ans_msg)
                process_next_flow_node(chat_obj, current_node.get('next'), flow_dict)
        
        # Acción: Actualizar Motivo
        elif action == 'update_reason':
            chat_obj.reason = bleach.clean((request.form.get('reason') or '')[:200])
        
        # Acción: Mensaje Normal o Archivo
        else:
            content = bleach.clean(request.form.get('content', '').strip()[:5000])
            file = request.files.get('file')
            file_path = None
            if file and file.filename != '':
                try:
                    filename = save_secure_upload(file, current_app.config['UPLOAD_FOLDER'], current_user.clinic_id)
                except ValueError as error:
                    flash(str(error))
                    return redirect(url_for('patient.chat', chat_id=chat_obj.id))
                file_path = filename
                ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
                if not content: 
                    content = "Imagen" if ext in ['jpg', 'jpeg', 'png', 'webp'] else "Archivo"
            
            if content or file_path:
                msg = Message(clinic_id=chat_obj.clinic_id, chat_id=chat_obj.id, sender_id=current_user.id, content=content or " ", file_path=file_path)
                db.session.add(msg)
        
        audit('patient_chat_updated', details=f"chat_id={chat_obj.id}; action={action or 'message'}")
        db.session.commit()
        return redirect(url_for('patient.chat', chat_id=chat_obj.id))
    
    # --- LÓGICA GET ---
    messages = Message.query.filter_by(chat_id=chat_obj.id).execution_options(include_all_clinics=True).order_by(Message.timestamp.asc()).all()
    for msg in messages:
        if msg.is_flow_question and msg.flow_options:
            try: msg.parsed_flow_options = json.loads(msg.flow_options)
            except: msg.parsed_flow_options = {}

    # Marcar como leídos
    unread_msgs = Message.query.filter(Message.chat_id == chat_obj.id, Message.sender_id == doctor.id, Message.is_read == False).execution_options(include_all_clinics=True).all()
    for msg in unread_msgs: msg.is_read = True
    db.session.commit()
                
    has_rated = Rating.query.filter_by(chat_id=chat_obj.id, patient_id=current_user.id).first()
    can_rate = chat_obj.status == 'closed' and not has_rated
    
    return render_template('patient_chat.html', chat=chat_obj, messages=messages, doctor=doctor, can_rate=can_rate, can_send=is_on_duty and not is_read_only and not is_doctor_busy_elsewhere, is_on_duty=is_on_duty, is_read_only=is_read_only, is_doctor_busy_elsewhere=is_doctor_busy_elsewhere)

# --- FAVORITOS ---
@patient_bp.route('/toggle_favorite/<int:doctor_id>', methods=['POST'])
@login_required
@role_required('patient')
def toggle_favorite(doctor_id):
    existing = Favorite.query.filter_by(patient_id=current_user.id, clinic_id=current_user.clinic_id, doctor_id=doctor_id).first()
    if existing:
        db.session.delete(existing)
        flash('Doctor eliminado de favoritos')
    else:
        new_fav = Favorite(clinic_id=current_user.clinic_id, patient_id=current_user.id, doctor_id=doctor_id)
        db.session.add(new_fav)
        flash('Doctor agregado a favoritos ')
    db.session.commit()
    return redirect(url_for('patient.dashboard'))


@patient_bp.route('/doctor_payment/<int:doctor_id>', methods=['GET', 'POST'])
@login_required
@role_required('patient')
def doctor_payment(doctor_id):
    doctor = User.query.filter_by(id=doctor_id, role='doctor', is_autonomous=True).execution_options(include_all_clinics=True).first_or_404()
    tariff = tariff_for_doctor(doctor)
    discount_percent, policy_enrollment, policy_provider = best_policy_discount(current_user, doctor)
    prices = tariff_prices(tariff, discount_percent)
    if not tariff or not prices:
        flash('Este medico aun no ha publicado precios para pagos manuales.')
        return redirect(url_for('patient.dashboard'))

    if request.method == 'POST':
        plan_type = request.form.get('plan_type')
        amount = price_for_plan(tariff, plan_type)
        if amount is None:
            flash('Seleccione un plan valido.')
            return redirect(url_for('patient.doctor_payment', doctor_id=doctor.id))
        proof = request.files.get('proof')
        if not proof or not proof.filename:
            flash('Adjunta una imagen o PDF del comprobante de pago.')
            return redirect(url_for('patient.doctor_payment', doctor_id=doctor.id))
        try:
            proof_path = save_secure_upload(proof, current_app.config['UPLOAD_FOLDER'], doctor.clinic_id)
        except ValueError as error:
            flash(str(error))
            return redirect(url_for('patient.doctor_payment', doctor_id=doctor.id))

        ticket = PaymentVerificationTicket(
            clinic_id=doctor.clinic_id,
            patient_id=current_user.id,
            doctor_id=doctor.id,
            plan_type=plan_type,
            amount=float(amount),
            discount_percent=float(discount_percent or 0),
            final_amount=discounted_amount(amount, discount_percent),
            proof_image=proof_path,
            status=PAYMENT_PENDING,
            patient_note=bleach.clean((request.form.get('patient_note') or '').strip()[:1000]) or None,
        )
        db.session.add(ticket)
        
        # Crear notificación para el doctor
        try:
            from models import Notification
            notif = Notification(
                user_id=doctor.id,
                clinic_id=doctor.clinic_id,
                title="Nuevo Comprobante de Pago",
                message=f"El paciente {current_user.name} ha enviado un comprobante de pago para el plan '{plan_type}' por un valor de ${discounted_amount(amount, discount_percent):,.0f}.",
                type="payment_ticket",
                timestamp=colombia_now()
            )
            db.session.add(notif)
        except Exception:
            # El comprobante ya quedo registrado; que falle la notificacion no
            # debe deshacerlo. Pero el fallo no puede desaparecer: `print` en un
            # worker de Gunicorn no lo lee nadie.
            current_app.logger.exception(
                'No se pudo notificar el comprobante de pago del paciente %s al medico %s',
                current_user.id, doctor.id,
            )


        audit('manual_payment_ticket_created', details=f'doctor_id={doctor.id}; plan={plan_type}')
        db.session.commit()
        flash('Comprobante enviado. El medico revisara y aprobara o rechazara el acceso.')
        return redirect(url_for('patient.dashboard'))

    tickets = PaymentVerificationTicket.query.filter_by(patient_id=current_user.id, doctor_id=doctor.id).execution_options(include_all_clinics=True).order_by(PaymentVerificationTicket.created_at.desc()).limit(5).all()
    return render_template(
        'patient_doctor_payment.html',
        doctor=doctor,
        tariff=tariff,
        prices=prices,
        plan_labels=PLAN_LABELS,
        discount_percent=discount_percent,
        policy_enrollment=policy_enrollment,
        tickets=tickets,
    )

# --- INICIAR CHAT ---
@patient_bp.route('/start_chat/<int:doctor_id>', methods=['POST'])
@login_required
@role_required('patient')
def start_chat(doctor_id):
    consent_redirect = require_sensitive_data_consent()
    if consent_redirect:
        return consent_redirect
    doctor = User.query.filter_by(id=doctor_id, role='doctor').execution_options(include_all_clinics=True).first_or_404()
    if (doctor.clinic_id != current_user.clinic_id and not doctor.is_autonomous) or not doctor.is_available:
        flash('Este doctor se encuentra ocupado.')
        return redirect(url_for('patient.dashboard'))
    if not patient_has_doctor_access(current_user, doctor):
        if doctor.is_autonomous:
            flash('Este medico independiente requiere pago o poliza activa antes de iniciar consulta.')
            return redirect(url_for('patient.doctor_payment', doctor_id=doctor.id))
        flash('Para acceder a esta clinica se requiere un codigo de acceso activo.')
        return redirect(url_for('settings.index'))
    chat_clinic_id = doctor.clinic_id if doctor.is_autonomous else current_user.clinic_id
    existing_chat = Chat.query.filter_by(patient_id=current_user.id, clinic_id=chat_clinic_id, doctor_id=doctor_id, status='open').execution_options(include_all_clinics=True).first()
    if existing_chat: return redirect(url_for('patient.chat', chat_id=existing_chat.id))
    reason = bleach.clean((request.form.get('reason') or 'Consulta General')[:200])
    new_chat = Chat(clinic_id=chat_clinic_id, patient_id=current_user.id, doctor_id=doctor_id, reason=reason)
    db.session.add(new_chat)
    db.session.commit()
    return redirect(url_for('patient.chat', chat_id=new_chat.id))

# --- CERRAR CHAT ---
@patient_bp.route('/close_chat/<int:chat_id>', methods=['POST'])
@login_required
@role_required('patient')
def close_chat(chat_id):
    chat_obj = Chat.query.filter_by(id=chat_id).execution_options(include_all_clinics=True).first_or_404()
    if chat_obj.patient_id != current_user.id:
        return redirect(url_for('patient.dashboard'))
    chat_obj.status = 'closed'; chat_obj.closed_by = 'patient'; chat_obj.active_flow_id = None
    # Solución: Marcar la cita como atendida si el chat se cierra naturalmente
    if chat_obj.appointment and chat_obj.appointment.status == 'in_progress':
        chat_obj.appointment.status = 'attended'
    db.session.commit(); flash('Consulta cerrada.')
    return redirect(url_for('patient.dashboard'))

# --- CALIFICAR CONSULTA (CON VALIDACIÓN ANTI-DUPLICADO POR CHAT) ---
@patient_bp.route('/rate_chat/<int:chat_id>', methods=['POST'])
@login_required
@role_required('patient')
def rate_chat(chat_id):
    chat_obj = Chat.query.filter_by(id=chat_id).execution_options(include_all_clinics=True).first_or_404()
    if chat_obj.patient_id != current_user.id or chat_obj.status != 'closed':
        flash('Solo pueden calificarse las consultas cerradas.')
        return redirect(url_for('patient.dashboard'))

    existing_rating = Rating.query.filter_by(chat_id=chat_obj.id, patient_id=current_user.id).first()
    if existing_rating:
        flash('Ya calificaste esta consulta.')
        return redirect(url_for('patient.chat', chat_id=chat_obj.id))

    # Acotada al rango real, y tolerante a lo que no sea un numero.
    #
    # Antes era `int(request.form.get('stars', 5))` a secas: un valor absurdo
    # entraba tal cual y desplazaba el promedio del profesional -que es lo que
    # el paciente mira para elegir a quien consultar- y un valor no numerico
    # reventaba con un 500 en la cara de quien acababa de ser atendido.
    try:
        stars = int(request.form.get('stars', 5))
    except (TypeError, ValueError):
        stars = 5
    stars = min(max(stars, 1), 5)
    comment = bleach.clean(request.form.get('comment', '').strip()[:1000])
    tags_list = [bleach.clean(t) for t in request.form.getlist('tags')]
    tags_str = ",".join(tags_list)
    
    rating = Rating(
        clinic_id=current_user.clinic_id,
        doctor_id=chat_obj.doctor_id,
        patient_id=current_user.id,
        chat_id=chat_obj.id,
        appointment_id=chat_obj.appointment_id,
        stars=stars,
        comment=comment,
        tags=tags_str
    )
    db.session.add(rating)
    
    doctor = db.session.get(User, chat_obj.doctor_id)
    all_ratings = Rating.query.filter_by(doctor_id=chat_obj.doctor_id).all()
    avg = sum(r.stars for r in all_ratings) / len(all_ratings)
    doctor.rating = round(avg, 1)
    
    db.session.commit()
    flash('Su calificacion quedo registrada. Gracias.')
    return redirect(url_for('patient.chat', chat_id=chat_obj.id))

@patient_bp.route('/rate_doctor/<int:doctor_id>', methods=['POST'])
@login_required
@role_required('patient')
def rate_doctor(doctor_id):
    closed_chat = Chat.query.filter_by(patient_id=current_user.id, doctor_id=doctor_id, status='closed').execution_options(include_all_clinics=True).order_by(Chat.id.desc()).first()
    if not closed_chat:
        flash('No hay una consulta cerrada con este profesional para calificar.')
        return redirect(url_for('patient.dashboard'))
    return rate_chat(closed_chat.id)

# --- AGENDAR CITA CALENDARIO MENSUAL ---
@patient_bp.route('/book_appointment/<int:doctor_id>', methods=['GET', 'POST'])
@login_required
@role_required('patient')
def book_appointment(doctor_id):
    consent_redirect = require_sensitive_data_consent()
    if consent_redirect:
        return consent_redirect
    
    doctor = User.query.filter_by(id=doctor_id, role='doctor').execution_options(include_all_clinics=True).first_or_404()
    if doctor.clinic_id != current_user.clinic_id and not doctor.is_autonomous:
        return redirect(url_for('patient.dashboard'))
    if not patient_has_doctor_access(current_user, doctor):
        if doctor.is_autonomous:
            flash('Para agendar con este medico independiente se requiere pago aprobado o poliza activa.')
            return redirect(url_for('patient.doctor_payment', doctor_id=doctor.id))
        flash('Esta clinica no esta habilitada para su cuenta. Redima un codigo en Configuracion.')
        return redirect(url_for('settings.index'))
    appointment_clinic_id = doctor.clinic_id if doctor.is_autonomous else current_user.clinic_id
    c = calendar.Calendar(firstweekday=0)
    month_str = request.args.get('month', colombia_now().strftime('%Y-%m'))
    current_view_date = datetime.strptime(month_str, '%Y-%m')
    
    month_days = []
    for d in c.itermonthdates(current_view_date.year, current_view_date.month):
        day_of_week = d.weekday()
        works = DoctorSchedule.query.filter_by(doctor_id=doctor.id, clinic_id=doctor.clinic_id, day_of_week=day_of_week, specific_date=None, is_available=True).first() is not None
        month_days.append({
            'date_str': d.strftime('%Y-%m-%d'), 'day_num': d.day,
            'is_current_month': d.month == current_view_date.month,
            'is_working_day': works, 'is_past': d < colombia_now().date()
        })
        
    next_month = (current_view_date.replace(day=28) + timedelta(days=5)).replace(day=1)
    prev_month = (current_view_date.replace(day=1) - timedelta(days=1)).replace(day=1)

    if request.method == 'POST':
        date = request.form.get('date'); time = request.form.get('time'); desc = bleach.clean((request.form.get('description') or '')[:1000])
        
        # Solución: Validar que no sea en el pasado
        try:
            appt_datetime = datetime.strptime(f"{date} {time}", '%Y-%m-%d %H:%M')
            if appt_datetime < colombia_now() - timedelta(minutes=5):  # 5 min de margen
                flash('No es posible agendar citas en una fecha pasada.')
                return redirect(request.referrer or url_for('patient.dashboard'))
        except ValueError:
            flash('Formato de fecha u hora inválido.')
            return redirect(request.referrer or url_for('patient.dashboard'))

        # La comprobacion previa da un mensaje claro en el caso normal, pero no es
        # la que garantiza la exclusion: entre esta consulta y la insercion cabe
        # otra reserva. Quien decide es el indice unico de la base de datos.
        taken = Appointment.query.filter(
            Appointment.doctor_id == doctor_id,
            Appointment.clinic_id == appointment_clinic_id,
            Appointment.date == date,
            Appointment.time == time,
            Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
        ).execution_options(include_all_clinics=True).first()
        if taken:
            flash('Esa hora ya esta ocupada. Seleccione otra.')
            return redirect(url_for('patient.book_appointment', doctor_id=doctor_id, date=date))

        appt = Appointment(clinic_id=appointment_clinic_id, patient_id=current_user.id,
                           doctor_id=doctor_id, date=date, time=time, description=desc)
        db.session.add(appt)
        try:
            db.session.commit()
        except IntegrityError:
            # Otro paciente tomo el horario en el intervalo entre la consulta y
            # la insercion. Sin esto, ambos quedarian citados a la misma hora.
            db.session.rollback()
            flash('Esa hora acaba de ser tomada por otro paciente. Seleccione otra.')
            return redirect(url_for('patient.book_appointment', doctor_id=doctor_id, date=date))

        audit('appointment_booked', details=f'doctor_id={doctor_id}; fecha={date} {time}')
        db.session.commit()
        flash('Cita agendada correctamente.')
        return redirect(url_for('patient.dashboard'))
    
    selected_date = request.args.get('date')
    final_slots = []
    
    if selected_date:
        date_obj = datetime.strptime(selected_date, '%Y-%m-%d')
        day_of_week = date_obj.weekday()
        weekly_sched = DoctorSchedule.query.filter_by(doctor_id=doctor_id, clinic_id=appointment_clinic_id, day_of_week=day_of_week, specific_date=None, is_available=True).execution_options(include_all_clinics=True).first()
        slots = []
        if weekly_sched:
            start_dt = datetime.strptime(weekly_sched.start_time, '%H:%M')
            end_dt = datetime.strptime(weekly_sched.end_time, '%H:%M')
            current_slot = start_dt
            while current_slot < end_dt:
                slots.append(current_slot.strftime('%H:%M'))
                current_slot += timedelta(minutes=15)
        
        # Una cita cancelada o no asistida libera el horario. Antes se contaban
        # todas, asi que un paciente que no se presento dejaba ese cupo inutilizado
        # de forma permanente: consulta perdida en una agenda que suele ser escasa.
        booked_slots = [
            appt.time for appt in Appointment.query.filter(
                Appointment.doctor_id == doctor_id,
                Appointment.clinic_id == appointment_clinic_id,
                Appointment.date == selected_date,
                Appointment.status.notin_(APPOINTMENT_FREEING_STATUSES),
            ).execution_options(include_all_clinics=True).all()
        ]
        blockouts = DoctorSchedule.query.filter_by(doctor_id=doctor_id, clinic_id=appointment_clinic_id, specific_date=selected_date, is_available=False).execution_options(include_all_clinics=True).all()
        blocked_slots = []
        for block in blockouts:
            block_start = datetime.strptime(block.start_time, '%H:%M')
            block_end = datetime.strptime(block.end_time, '%H:%M')
            current_b = block_start
            while current_b < block_end:
                blocked_slots.append(current_b.strftime('%H:%M'))
                current_b += timedelta(minutes=15)
        
        for s in slots:
            if s in blocked_slots: final_slots.append({'time': s, 'status': 'blocked'})
            elif s in booked_slots: final_slots.append({'time': s, 'status': 'booked'})
            else: final_slots.append({'time': s, 'status': 'available'})
                
    return render_template('patient_appointment.html', doctor=doctor, selected_date=selected_date, slots=final_slots, month_days=month_days, current_view_date=current_view_date, prev_month=prev_month, next_month=next_month)

# --- RUTA: UNIRSE A LA CITA (SALA DE ESPERA) ---
@patient_bp.route('/join_appointment/<int:appt_id>')
@login_required
@role_required('patient')
def join_appointment(appt_id):
    consent_redirect = require_sensitive_data_consent()
    if consent_redirect:
        return consent_redirect
    appt = Appointment.query.filter_by(id=appt_id).execution_options(include_all_clinics=True).first_or_404()
    
    # Seguridad: Verificar que es su cita y que esté pendiente
    if appt.patient_id != current_user.id or appt.status != 'pending':
        flash('No es posible unirse a esta cita.')
        return redirect(url_for('patient.dashboard'))
        
    # --- LÓGICA DE CONTROL DE TIEMPO ---
    now = colombia_now()
    
    try:
        appt_datetime = datetime.strptime(f"{appt.date} {appt.time}", '%Y-%m-%d %H:%M')
    except ValueError:
        flash('Formato de fecha/hora inválido.')
        return redirect(url_for('patient.dashboard'))

    # 1. Bloquear entrada ANTES de la hora exacta
    if now < appt_datetime:
        flash(f'Aún no es la hora de su cita. Podra unirse a partir de las {appt.time}.')
        return redirect(url_for('patient.dashboard'))
        
    # 2. Tolerancia máxima de 2 minutos después de la hora
    if now > appt_datetime + timedelta(minutes=2):
        flash('El tiempo para unirte a esta cita ha expirado (tolerancia de 2 minutos).')
        return redirect(url_for('patient.dashboard'))
    # --------------------------------------

    # Marcar como en progreso
    appt.status = 'in_progress'
    appt.started_at = now
    
    # Crear o reutilizar chat asociado
    chat = Chat.query.filter_by(appointment_id=appt.id, clinic_id=appt.clinic_id).execution_options(include_all_clinics=True).first()
    if not chat:
        chat = Chat(
            clinic_id=appt.clinic_id,
            patient_id=appt.patient_id,
            doctor_id=appt.doctor_id,
            reason=f"Cita: {appt.description or 'General'}",
            appointment_id=appt.id
        )
        db.session.add(chat)
        
    db.session.commit()
    
    return redirect(url_for('patient.chat', chat_id=chat.id))

# --- RUTA: MARCAR COMO NO ASISTIDA (No-Show automático o manual) ---
@patient_bp.route('/mark_no_show/<int:appt_id>', methods=['POST'])
@login_required
@role_required('patient')
def mark_no_show(appt_id):
    appt = Appointment.query.filter_by(id=appt_id).execution_options(include_all_clinics=True).first_or_404()
    if appt.patient_id != current_user.id:
        return redirect(url_for('patient.dashboard'))
    appt.status = 'no_show'
    
    # Cerrar el chat si existe
    chat = Chat.query.filter_by(appointment_id=appt.id, clinic_id=appt.clinic_id).execution_options(include_all_clinics=True).first()
    if chat:
        chat.status = 'closed'
        chat.closed_by = 'system'
        
    db.session.commit()
    flash('La cita se marcó como No Asistida por falta de presencia.')
    return redirect(url_for('patient.dashboard'))
