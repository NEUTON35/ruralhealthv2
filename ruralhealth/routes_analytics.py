import json
from collections import Counter
from datetime import timedelta

from flask import Blueprint, jsonify, render_template
from flask_login import current_user, login_required
from sqlalchemy import func

from models import (
    Appointment, Chat, MedicalHistory, MedicalOrder,
    MedicationPickupTicket, Rating, Stock, User, db,
)
from security import role_required
from time_utils import colombia_now

analytics_bp = Blueprint('analytics', __name__)


def _month_label(dt):
    names = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
             'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    return f"{names[dt.month - 1]} {dt.year}"


@analytics_bp.route('/analytics')
@login_required
@role_required('admin')
def dashboard():
    return render_template('admin_analytics.html')


@analytics_bp.route('/api/analytics_data')
@login_required
@role_required('admin')
def analytics_data():
    clinic_id = current_user.clinic_id
    now = colombia_now()

    # --- KPI 1: Consultas este mes ---
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

    consultations_this_month = Appointment.query.filter(
        Appointment.clinic_id == clinic_id,
        Appointment.status == 'attended',
        Appointment.date >= month_start.strftime('%Y-%m-%d'),
    ).count()

    consultations_prev_month = Appointment.query.filter(
        Appointment.clinic_id == clinic_id,
        Appointment.status == 'attended',
        Appointment.date >= prev_month_start.strftime('%Y-%m-%d'),
        Appointment.date < month_start.strftime('%Y-%m-%d'),
    ).count()

    # --- KPI 2: Pacientes nuevos este mes ---
    # Este indicador estaba mal: contaba *todos* los pacientes historicos de la
    # clinica y lo presentaba como altas del mes, porque `User` no tenia columna
    # de fecha de creacion. El numero crecia y nunca bajaba, de modo que la
    # grafica de captacion no reflejaba nada real.
    new_patients = User.query.filter(
        User.clinic_id == clinic_id,
        User.role == 'patient',
        User.created_at >= month_start,
    ).count()

    new_patients_prev_month = User.query.filter(
        User.clinic_id == clinic_id,
        User.role == 'patient',
        User.created_at >= prev_month_start,
        User.created_at < month_start,
    ).count()

    total_patients = User.query.filter(
        User.clinic_id == clinic_id,
        User.role == 'patient',
    ).count()

    # --- KPI 3: Satisfacción promedio ---
    avg_rating_result = db.session.query(func.avg(Rating.stars)).filter(
        Rating.clinic_id == clinic_id,
    ).scalar()
    avg_rating = round(float(avg_rating_result), 1) if avg_rating_result else 0.0
    total_ratings = Rating.query.filter(Rating.clinic_id == clinic_id).count()

    # --- KPI 4: Chats activos ---
    active_chats = Chat.query.filter(
        Chat.clinic_id == clinic_id,
        Chat.status == 'open',
    ).count()

    # --- Tendencia: Consultas últimos 6 meses ---
    trend_labels = []
    trend_data = []
    # Los meses se recorren contando meses, no restando bloques de 30 dias.
    #
    # La version anterior hacia `now.replace(day=1) - timedelta(days=i * 30)`. Como
    # los meses no duran 30 dias, la serie salia mal: en marzo de 2026 la grafica
    # mostraba diciembre dos veces y se saltaba febrero por completo. El
    # administrador tomaba decisiones sobre una curva con un mes inventado y otro
    # ausente.
    def month_start(reference, months_back):
        total = reference.year * 12 + (reference.month - 1) - months_back
        return reference.replace(
            year=total // 12, month=total % 12 + 1, day=1,
            hour=0, minute=0, second=0, microsecond=0,
        )

    for i in range(5, -1, -1):
        cursor = month_start(now, i)
        next_month = month_start(now, i - 1)
        label = _month_label(cursor)
        count = Appointment.query.filter(
            Appointment.clinic_id == clinic_id,
            Appointment.status == 'attended',
            Appointment.date >= cursor.strftime('%Y-%m-%d'),
            Appointment.date < next_month.strftime('%Y-%m-%d'),
        ).count()
        trend_labels.append(label)
        trend_data.append(count)

    # --- Top 10 Medicamentos más recetados ---
    orders = MedicalOrder.query.filter(
        MedicalOrder.clinic_id == clinic_id,
    ).all()
    med_counter = Counter()
    for order in orders:
        try:
            meds = json.loads(order.meds_json or '[]')
            for med in meds:
                name = med.get('medicamento') or med.get('nombre_med', 'Desconocido')
                med_counter[name] += med.get('cantidad', 1)
        except (json.JSONDecodeError, TypeError):
            pass
    top_meds = med_counter.most_common(10)
    top_meds_labels = [m[0] for m in top_meds]
    top_meds_data = [m[1] for m in top_meds]

    # --- Top diagnósticos CIE-10 ---
    dx_counter = Counter()
    histories = MedicalHistory.query.filter(
        MedicalHistory.clinic_id == clinic_id,
        MedicalHistory.cie10_code.isnot(None),
    ).all()
    for h in histories:
        dx_counter[h.cie10_code] += 1
    top_dx = dx_counter.most_common(8)
    dx_labels = [d[0] for d in top_dx]
    dx_data = [d[1] for d in top_dx]

    # --- Productividad por doctor ---
    doctors = User.query.filter(
        User.clinic_id == clinic_id,
        User.role == 'doctor',
    ).all()
    doctor_stats = []
    for doc in doctors:
        attended = Appointment.query.filter(
            Appointment.clinic_id == clinic_id,
            Appointment.doctor_id == doc.id,
            Appointment.status == 'attended',
        ).count()
        chats_closed = Chat.query.filter(
            Chat.clinic_id == clinic_id,
            Chat.doctor_id == doc.id,
            Chat.status == 'closed',
        ).count()
        doc_rating = db.session.query(func.avg(Rating.stars)).filter(
            Rating.clinic_id == clinic_id,
            Rating.doctor_id == doc.id,
        ).scalar()
        doctor_stats.append({
            'name': doc.name,
            'specialty': doc.specialty or 'General',
            'attended': attended,
            'chats_closed': chats_closed,
            'rating': round(float(doc_rating), 1) if doc_rating else 0.0,
        })
    doctor_stats.sort(key=lambda x: x['attended'], reverse=True)

    # --- Stock bajo ---
    low_stock_count = Stock.query.filter(
        Stock.clinic_id == clinic_id,
        Stock.cantidad <= 5,
    ).count()

    # --- Tickets pendientes de farmacia ---
    pending_tickets = MedicationPickupTicket.query.filter(
        MedicationPickupTicket.clinic_id == clinic_id,
        MedicationPickupTicket.status.in_(['autorizado', 'sin_stock', 'parcial']),
    ).count()

    return jsonify({
        'kpis': {
            'consultations_month': consultations_this_month,
            'consultations_prev_month': consultations_prev_month,
            'new_patients': new_patients,
            'new_patients_prev_month': new_patients_prev_month,
            'total_patients': total_patients,
            'avg_rating': avg_rating,
            'total_ratings': total_ratings,
            'active_chats': active_chats,
            'low_stock': low_stock_count,
            'pending_tickets': pending_tickets,
        },
        'trend': {
            'labels': trend_labels,
            'data': trend_data,
        },
        'top_meds': {
            'labels': top_meds_labels,
            'data': top_meds_data,
        },
        'diagnostics': {
            'labels': dx_labels,
            'data': dx_data,
        },
        'doctors': doctor_stats,
    })
