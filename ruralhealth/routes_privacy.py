"""Derechos del titular sobre sus datos personales (Ley 1581 de 2012).

El artículo 8 reconoce al titular el derecho a conocer, actualizar y rectificar
sus datos, a solicitar prueba de la autorización otorgada, a ser informado del
uso que se les da, a revocar la autorización y a solicitar su supresión.

Antes de esta incorporación el sistema no ofrecía ninguna de esas vías: no había
forma de que un paciente obtuviera copia de sus datos ni de que pidiera su
eliminación, y tampoco quedaba constancia de las solicitudes recibidas, que es lo
que permite acreditar ante la autoridad que se atendieron en plazo.

Límite deliberado de la supresión
---------------------------------
La historia clínica **no se elimina a petición del titular**. La Resolución 839
de 2017 obliga al prestador a conservarla un mínimo de quince años, y ese deber
legal prevalece sobre la solicitud de supresión (Ley 1581, artículo 9, y la
excepción del artículo 4 literal f). Lo que sí procede es desactivar la cuenta,
cesar los tratamientos no obligatorios y dejar registrada la solicitud. Ese
límite se le explica al titular en la propia respuesta, no se aplica en silencio.
"""

import csv
import hashlib
import io
import json
import zipfile
from datetime import timedelta

from flask import (
    Blueprint, Response, abort, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required

from models import (
    Appointment, Chat, DataSubjectRequest, InformedConsentLog, MedicalHistory,
    MedicalOrder, MedicationPickupTicket, Message, PatientAllergy,
    PatientChronicCondition, Rating, User, db,
)
from legal_documents import get_document, render_document
from security import audit, csv_safe_row, role_required
from time_utils import colombia_now, colombia_strftime

privacy_bp = Blueprint('privacy', __name__)


def _legal_values():
    """Datos del prestador que completan los textos legales."""
    from models import LegalConfiguration
    try:
        return {row.key: row.value for row in LegalConfiguration.query.all() if row.value}
    except Exception:
        return {}

# Plazos del Decreto 1377 de 2013: diez días hábiles para consulta, quince para
# reclamos. Se usa el calendario natural con margen, porque calcular días hábiles
# exige el calendario de festivos colombianos y errar por exceso es lo seguro.
RESPONSE_DAYS = {
    'acceso': 10,
    'rectificacion': 15,
    'supresion': 15,
    'revocatoria': 15,
    'portabilidad': 15,
}

REQUEST_LABELS = {
    'acceso': 'Consulta de datos',
    'rectificacion': 'Rectificacion',
    'supresion': 'Supresion',
    'revocatoria': 'Revocatoria de autorizacion',
    'portabilidad': 'Portabilidad',
}


@privacy_bp.route('/', methods=['GET'])
@login_required
def index():
    """Panel del titular: qué datos hay, qué puede hacer y qué ha solicitado."""
    requests_made = DataSubjectRequest.query.filter_by(
        user_id=current_user.id
    ).order_by(DataSubjectRequest.requested_at.desc()).limit(20).all()

    consents = InformedConsentLog.query.filter_by(
        patient_id=current_user.id
    ).order_by(InformedConsentLog.timestamp.desc()).all()

    inventory = {
        'citas': Appointment.query.filter_by(patient_id=current_user.id).count(),
        'consultas': Chat.query.filter_by(patient_id=current_user.id).count(),
        'historias': MedicalHistory.query.filter_by(patient_id=current_user.id).count(),
        'ordenes': MedicalOrder.query.filter_by(patient_id=current_user.id).count(),
        'tickets': MedicationPickupTicket.query.filter_by(patient_id=current_user.id).count(),
        'alergias': PatientAllergy.query.filter_by(patient_id=current_user.id).count(),
    }

    return render_template(
        'privacy_center.html',
        requests_made=requests_made,
        consents=consents,
        inventory=inventory,
        request_labels=REQUEST_LABELS,
    )


@privacy_bp.route('/exportar', methods=['POST'])
@login_required
def export_my_data():
    """Entrega al titular una copia completa de sus datos.

    El derecho de acceso solo se cumple si la copia es legible y completa. Se
    entrega un ZIP con un CSV por categoría más un resumen en JSON, formatos que
    el titular puede abrir sin depender de esta aplicación.
    """
    buffer = io.BytesIO()

    def sheet(rows, header):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(header)
        for row in rows:
            writer.writerow(csv_safe_row(row))
        return '﻿' + output.getvalue()

    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:

        profile = {
            'usuario': current_user.username,
            'nombre': current_user.name,
            'tipo_documento': current_user.document_type,
            'documento': current_user.cedula,
            'telefono': current_user.phone,
            'correo': current_user.email,
            'direccion': current_user.address,
            'fecha_nacimiento': str(current_user.birth_date) if current_user.birth_date else None,
            'sexo': current_user.sex,
            'rol': current_user.role,
            'clinica_id': current_user.clinic_id,
            'creado': colombia_strftime(current_user.created_at, '%Y-%m-%d %H:%M'),
            'ultimo_ingreso': colombia_strftime(current_user.last_login_at, '%Y-%m-%d %H:%M'),
        }
        archive.writestr(
            'perfil.json',
            json.dumps(profile, ensure_ascii=False, indent=2),
        )

        appointments = Appointment.query.filter_by(patient_id=current_user.id).all()
        archive.writestr('citas.csv', sheet(
            [[a.date, a.time, a.appointment_type, a.status,
              a.doctor.name if a.doctor else '', a.description or '']
             for a in appointments],
            ['fecha', 'hora', 'tipo', 'estado', 'profesional', 'descripcion'],
        ))

        chats = Chat.query.filter_by(patient_id=current_user.id).all()
        message_rows = []
        for chat in chats:
            for message in Message.query.filter_by(chat_id=chat.id).order_by(
                    Message.timestamp.asc()).all():
                message_rows.append([
                    chat.id,
                    colombia_strftime(message.timestamp, '%Y-%m-%d %H:%M'),
                    'yo' if message.sender_id == current_user.id else (
                        message.sender.name if message.sender else 'sistema'),
                    message.content or '',
                ])
        archive.writestr('consultas_mensajes.csv', sheet(
            message_rows, ['consulta_id', 'fecha', 'de', 'mensaje'],
        ))

        histories = MedicalHistory.query.filter_by(patient_id=current_user.id).all()
        archive.writestr('historia_clinica.csv', sheet(
            [[colombia_strftime(h.created_at, '%Y-%m-%d %H:%M'), h.record_type,
              h.doctor.name if h.doctor else '', h.cie10_code or '', h.cups_code or '',
              h.summary or '', h.diagnosis or '', h.treatment or '']
             for h in histories],
            ['fecha', 'tipo', 'profesional', 'cie10', 'cups', 'resumen',
             'diagnostico', 'tratamiento'],
        ))

        orders = MedicalOrder.query.filter_by(patient_id=current_user.id).all()
        archive.writestr('ordenes_medicas.csv', sheet(
            [[o.order_number or o.id, colombia_strftime(o.created_at, '%Y-%m-%d'),
              o.doctor.name if o.doctor else '', o.status,
              colombia_strftime(o.expires_at, '%Y-%m-%d'), o.meds_json or '']
             for o in orders],
            ['numero', 'fecha', 'profesional', 'estado', 'vence', 'medicamentos'],
        ))

        allergies = PatientAllergy.query.filter_by(patient_id=current_user.id).all()
        archive.writestr('alergias.csv', sheet(
            [[a.substance, a.reaction or '', a.severity, a.status,
              colombia_strftime(a.created_at, '%Y-%m-%d')]
             for a in allergies],
            ['sustancia', 'reaccion', 'severidad', 'estado', 'registrada'],
        ))

        conditions = PatientChronicCondition.query.filter_by(patient_id=current_user.id).all()
        archive.writestr('condiciones.csv', sheet(
            [[c.condition, c.cie10_code or '', c.status,
              str(c.diagnosed_on) if c.diagnosed_on else '']
             for c in conditions],
            ['condicion', 'cie10', 'estado', 'diagnosticada'],
        ))

        consents = InformedConsentLog.query.filter_by(patient_id=current_user.id).all()
        archive.writestr('consentimientos.csv', sheet(
            [[colombia_strftime(c.timestamp, '%Y-%m-%d %H:%M'), c.consent_type,
              c.document_version or '', 'otorgado' if c.granted else 'revocado',
              c.digital_signature_hash]
             for c in consents],
            ['fecha', 'tipo', 'version_documento', 'estado', 'firma_digital'],
        ))

        tickets = MedicationPickupTicket.query.filter_by(patient_id=current_user.id).all()
        archive.writestr('entregas_medicamentos.csv', sheet(
            [[t.pickup_code, t.pickup_date, t.pickup_location or '', t.status,
              t.meds_json or '', t.delivered_json or '']
             for t in tickets],
            ['codigo', 'fecha', 'lugar', 'estado', 'solicitado', 'entregado'],
        ))

        ratings = Rating.query.filter_by(patient_id=current_user.id).all()
        archive.writestr('calificaciones.csv', sheet(
            [[r.doctor.name if r.doctor else '', r.stars, r.comment or '', r.tags or '']
             for r in ratings],
            ['profesional', 'estrellas', 'comentario', 'etiquetas'],
        ))

        archive.writestr('LEEME.txt', (
            'Copia de sus datos personales en RuralHealth Connect\n'
            f'Generada el {colombia_strftime(colombia_now(), "%d/%m/%Y a las %H:%M")}\n\n'
            'Este paquete contiene la informacion que el sistema tiene sobre usted,\n'
            'entregada en cumplimiento del derecho de acceso reconocido en el\n'
            'articulo 8 de la Ley 1581 de 2012.\n\n'
            'Los archivos .csv se abren con cualquier hoja de calculo.\n'
            'El archivo perfil.json contiene sus datos de identificacion.\n\n'
            'Contiene informacion de salud. Conservelo en un lugar seguro y tenga\n'
            'cuidado al compartirlo.\n'
        ))

    audit('data_subject_export', details=f'user_id={current_user.id}')
    db.session.add(DataSubjectRequest(
        user_id=current_user.id,
        clinic_id=current_user.clinic_id,
        request_type='acceso',
        status='atendida',
        detail='Descarga directa de la copia de datos por el titular.',
        requested_at=colombia_now(),
        resolved_at=colombia_now(),
        requester_ip=request.remote_addr,
    ))
    db.session.commit()

    filename = f'mis_datos_{current_user.username}_{colombia_now().strftime("%Y%m%d")}.zip'
    return Response(
        buffer.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@privacy_bp.route('/solicitar', methods=['POST'])
@login_required
def create_request():
    """Registra una solicitud de rectificación, supresión o revocatoria."""
    request_type = (request.form.get('request_type') or '').strip().lower()
    if request_type not in RESPONSE_DAYS:
        flash('Tipo de solicitud no reconocido.')
        return redirect(url_for('privacy.index'))

    detail = (request.form.get('detail') or '').strip()[:2000]
    if len(detail) < 10:
        flash('Describa su solicitud con al menos 10 caracteres para poder atenderla.')
        return redirect(url_for('privacy.index'))

    now = colombia_now()
    entry = DataSubjectRequest(
        user_id=current_user.id,
        clinic_id=current_user.clinic_id,
        request_type=request_type,
        status='recibida',
        detail=detail,
        requested_at=now,
        due_at=now + timedelta(days=RESPONSE_DAYS[request_type]),
        requester_ip=request.remote_addr,
    )
    db.session.add(entry)
    audit('data_subject_request_created', details=f'tipo={request_type}')
    db.session.commit()

    if request_type == 'supresion':
        # El límite se explica al titular en el momento, no después.
        flash(
            'Solicitud registrada. Ten en cuenta que la historia clinica no puede '
            'eliminarse a peticion del titular: la ley obliga al prestador a '
            'conservarla un minimo de 15 anos (Resolucion 839 de 2017). A su solicitud '
            'si es posible desactivar la cuenta y cesar los usos no obligatorios de sus datos.'
        )
    else:
        flash(
            f'Solicitud registrada. Recibiras respuesta dentro de los '
            f'{RESPONSE_DAYS[request_type]} dias siguientes.'
        )
    return redirect(url_for('privacy.index'))


@privacy_bp.route('/consentimiento-telemedicina', methods=['GET', 'POST'])
@login_required
def telemedicine_consent():
    """Consentimiento informado específico para atención por telemedicina.

    La Resolución 2654 de 2019 lo exige aparte del consentimiento general de
    datos, y por una razón sustantiva: lo que el paciente debe entender aquí no
    es cómo se tratan sus datos, sino que **no habrá examen físico** y qué
    implica eso para su diagnóstico.

    Antes no existía: se emitían órdenes por telemedicina sin constancia de que
    el paciente conociera los límites de esa modalidad.
    """
    documento = get_document('telemedicine')
    valores = _legal_values()
    rendered = render_document('telemedicine', valores)

    vigente = InformedConsentLog.query.filter_by(
        patient_id=current_user.id,
        consent_type='telemedicine',
        granted=True,
        revoked_at=None,
    ).order_by(InformedConsentLog.timestamp.desc()).first()

    if request.method == 'POST':
        if request.form.get('accept_telemedicine') != 'on':
            flash('Debe marcar la aceptación para recibir atención por telemedicina.')
            return redirect(url_for('privacy.telemedicine_consent'))

        now = colombia_now()
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        agente = (request.user_agent.string or '')[:300]

        # La firma vincula al titular, el texto exacto que acepto y el momento.
        firma = hashlib.sha256(
            '|'.join([
                str(current_user.id),
                current_user.cedula_hash or '',
                documento.version,
                documento.content_hash,
                now.isoformat(),
                str(ip),
                agente,
            ]).encode('utf-8')
        ).hexdigest()

        db.session.add(InformedConsentLog(
            patient_id=current_user.id,
            clinic_id=current_user.clinic_id,
            consent_type='telemedicine',
            document_version=documento.version,
            document_hash=documento.content_hash,
            granted=True,
            ip_address=ip,
            user_agent=agente,
            digital_signature_hash=firma,
            timestamp=now,
        ))
        audit(
            'telemedicine_consent_accepted',
            details=f'version={documento.version}; hash={documento.content_hash[:12]}',
        )
        db.session.commit()

        flash('Consentimiento registrado. Ya puede recibir atención por telemedicina.')
        return redirect(url_for('privacy.index'))

    return render_template(
        'telemedicine_consent.html',
        document=rendered,
        current_consent=vigente,
    )


@privacy_bp.route('/revocar-consentimiento', methods=['POST'])
@login_required
def revoke_consent():
    """Revoca la autorización de tratamiento de datos sensibles de salud.

    Revocar cierra el acceso a las funciones clínicas: sin autorización el
    prestador no puede seguir usando esos datos para consultas nuevas. Lo ya
    registrado permanece bajo el deber legal de conservación.
    """
    consent_type = (request.form.get('consent_type') or 'sensitive_data').strip()[:50]

    latest = InformedConsentLog.query.filter_by(
        patient_id=current_user.id, consent_type=consent_type, granted=True, revoked_at=None,
    ).order_by(InformedConsentLog.timestamp.desc()).first()

    if not latest:
        flash('No hay una autorizacion vigente de ese tipo para revocar.')
        return redirect(url_for('privacy.index'))

    now = colombia_now()
    latest.revoked_at = now

    db.session.add(InformedConsentLog(
        patient_id=current_user.id,
        clinic_id=current_user.clinic_id,
        consent_type=consent_type,
        granted=False,
        document_version=latest.document_version,
        document_hash=latest.document_hash,
        ip_address=request.remote_addr,
        user_agent=(request.user_agent.string or '')[:300],
        digital_signature_hash=latest.digital_signature_hash,
        timestamp=now,
    ))

    if consent_type == 'sensitive_data':
        current_user.sensitive_data_consent_at = None

    db.session.add(DataSubjectRequest(
        user_id=current_user.id,
        clinic_id=current_user.clinic_id,
        request_type='revocatoria',
        status='atendida',
        detail=f'Revocatoria de la autorizacion "{consent_type}".',
        requested_at=now,
        resolved_at=now,
        requester_ip=request.remote_addr,
    ))

    audit('consent_revoked', details=f'tipo={consent_type}')
    db.session.commit()

    flash(
        'Autorizacion revocada. Para usar de nuevo las funciones clinicas tendras '
        'que otorgarla otra vez. Los registros ya existentes se conservan por el '
        'plazo que exige la ley.'
    )
    return redirect(url_for('privacy.index'))


# =============================================================================
# Gestion administrativa de las solicitudes
# =============================================================================

@privacy_bp.route('/solicitudes', methods=['GET'])
@login_required
@role_required('admin', 'super')
def manage_requests():
    """Bandeja de solicitudes, con las vencidas primero."""
    status_filter = (request.args.get('status') or '').strip()

    query = DataSubjectRequest.query
    if current_user.role != 'super':
        query = query.filter(DataSubjectRequest.clinic_id == current_user.clinic_id)
    if status_filter:
        query = query.filter(DataSubjectRequest.status == status_filter)

    pending = query.filter(DataSubjectRequest.resolved_at.is_(None)).order_by(
        DataSubjectRequest.due_at.asc().nullslast()
    ).limit(100).all()

    resolved = query.filter(DataSubjectRequest.resolved_at.isnot(None)).order_by(
        DataSubjectRequest.resolved_at.desc()
    ).limit(50).all()

    return render_template(
        'privacy_requests.html',
        pending=pending,
        resolved=resolved,
        request_labels=REQUEST_LABELS,
        overdue_count=sum(1 for item in pending if item.is_overdue),
    )


@privacy_bp.route('/solicitudes/<int:request_id>/resolver', methods=['POST'])
@login_required
@role_required('admin', 'super')
def resolve_request(request_id):
    entry = DataSubjectRequest.query.filter_by(id=request_id).first_or_404()
    if current_user.role != 'super' and entry.clinic_id != current_user.clinic_id:
        abort(403)

    note = (request.form.get('resolution_note') or '').strip()[:2000]
    outcome = (request.form.get('outcome') or 'atendida').strip()
    if outcome not in {'atendida', 'rechazada'}:
        outcome = 'atendida'

    if len(note) < 10:
        flash('Registre la respuesta dada al titular (minimo 10 caracteres).')
        return redirect(url_for('privacy.manage_requests'))

    entry.status = outcome
    entry.resolution_note = note
    entry.resolved_at = colombia_now()
    entry.resolved_by_id = current_user.id

    audit(
        'data_subject_request_resolved',
        details=f'request_id={entry.id}; tipo={entry.request_type}; resultado={outcome}',
    )
    db.session.commit()

    flash('Solicitud cerrada y respuesta registrada.')
    return redirect(url_for('privacy.manage_requests'))


@privacy_bp.route('/solicitudes/<int:request_id>/desactivar-cuenta', methods=['POST'])
@login_required
@role_required('admin', 'super')
def deactivate_account(request_id):
    """Desactiva la cuenta del titular que pidió supresión.

    La cuenta deja de poder usarse y sus datos dejan de tratarse para fines
    nuevos. La historia clínica permanece bajo el deber legal de conservación:
    eliminarla incumpliría la Resolución 839 de 2017.
    """
    entry = DataSubjectRequest.query.filter_by(id=request_id).first_or_404()
    if current_user.role != 'super' and entry.clinic_id != current_user.clinic_id:
        abort(403)

    target = db.session.get(User, entry.user_id)
    if not target:
        flash('El titular de la solicitud ya no existe.')
        return redirect(url_for('privacy.manage_requests'))

    if target.role != 'patient':
        flash('Esta via aplica unicamente a cuentas de paciente.')
        return redirect(url_for('privacy.manage_requests'))

    target.is_active_account = False
    target.deactivated_at = colombia_now()
    target.sensitive_data_consent_at = None

    audit(
        'account_deactivated_by_request',
        details=f'user_id={target.id}; request_id={entry.id}',
    )
    db.session.commit()

    flash(
        f'Cuenta desactivada. La historia clinica se conserva por el plazo legal '
        f'de 15 anos y no se elimina.'
    )
    return redirect(url_for('privacy.manage_requests'))
