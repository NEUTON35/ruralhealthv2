# -*- coding: utf-8 -*-
"""Canal de PQRS sobre la prestación del servicio de salud.

Los términos y condiciones de la plataforma ya prometían por escrito recibir
peticiones, quejas y reclamos y responder dentro de los quince días hábiles.
No existía dónde radicarlos.

Es distinto del habeas data. `routes_privacy` atiende lo que la Ley 1581 obliga
sobre los **datos** del titular. Esto atiende lo que el paciente reclama sobre
**la atención**: que no le dieron cita, que lo trataron mal, que el medicamento
no llegó.

Quien radica recibe un número. Sin él no puede hacer seguimiento ni acreditar
que radicó, que es justamente lo que convierte un canal de quejas en un buzón
sin fondo.
"""

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from models import (PQRS_RESUELTA, PQRS_TIPOS, ServiceComplaint, db)
from security import audit, role_required
from service_quality import pqrs_abiertas, radicar_pqrs, responder_pqrs

pqrs_bp = Blueprint('pqrs', __name__)

ETIQUETAS = {
    'peticion': 'Petición',
    'queja': 'Queja',
    'reclamo': 'Reclamo',
    'sugerencia': 'Sugerencia',
    'felicitacion': 'Felicitación',
}


@pqrs_bp.route('/', methods=['GET'])
@login_required
def index():
    """Radicados del propio usuario."""
    mios = (ServiceComplaint.query
            .filter_by(user_id=current_user.id)
            .order_by(ServiceComplaint.submitted_at.desc())
            .all())
    return render_template('pqrs.html', radicados=mios, etiquetas=ETIQUETAS,
                           tipos=PQRS_TIPOS)


@pqrs_bp.route('/radicar', methods=['POST'])
@login_required
def create():
    try:
        registro = radicar_pqrs(
            db, current_user,
            request.form.get('complaint_type'),
            request.form.get('subject'),
            request.form.get('detail'),
        )
    except ValueError as error:
        flash(str(error))
        return redirect(url_for('pqrs.index'))

    # No se registra el contenido: una queja puede describir una situación
    # clínica, y la auditoría la lee más gente que la historia clínica.
    audit('pqrs_submitted', details=f'ticket={registro.ticket_code}; '
                                    f'tipo={registro.complaint_type}')
    flash(f'Su solicitud quedó radicada con el número {registro.ticket_code}. '
          f'Recibirá respuesta a más tardar el '
          f'{registro.due_at.strftime("%d/%m/%Y")}.')
    return redirect(url_for('pqrs.index'))


@pqrs_bp.route('/gestion', methods=['GET'])
@login_required
@role_required('admin', 'super')
def manage():
    """Bandeja del prestador, lo más vencido primero."""
    abiertas = pqrs_abiertas(clinic_id=current_user.clinic_id)
    resueltas = (ServiceComplaint.query
                 .filter_by(clinic_id=current_user.clinic_id, status=PQRS_RESUELTA)
                 .order_by(ServiceComplaint.resolved_at.desc())
                 .limit(50).all())
    return render_template('pqrs_manage.html', abiertas=abiertas,
                           resueltas=resueltas, etiquetas=ETIQUETAS,
                           vencidas=sum(1 for f in abiertas if f.is_overdue))


@pqrs_bp.route('/gestion/<int:complaint_id>/responder', methods=['POST'])
@login_required
@role_required('admin', 'super')
def resolve(complaint_id):
    registro = ServiceComplaint.query.filter_by(
        id=complaint_id, clinic_id=current_user.clinic_id).first_or_404()
    try:
        responder_pqrs(db, registro, current_user.id,
                       request.form.get('resolution'))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for('pqrs.manage'))

    audit('pqrs_resolved', details=f'ticket={registro.ticket_code}')
    flash(f'Radicado {registro.ticket_code} respondido.')
    return redirect(url_for('pqrs.manage'))
