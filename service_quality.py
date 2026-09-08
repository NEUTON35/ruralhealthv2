# -*- coding: utf-8 -*-
"""PQRS del servicio de salud y farmacovigilancia.

Dos obligaciones que no existían y que la plataforma ya prometía por escrito o
que la norma exige al servicio farmacéutico.

PQRS
----
Los términos y condiciones de la plataforma se comprometen a recibir peticiones,
quejas y reclamos y a responder dentro de los quince días hábiles. No había
dónde radicarlos. Prometer un plazo sin el sistema que lo sostiene deja la
constancia del incumplimiento y ninguna del cumplimiento.

Es distinto del habeas data: `routes_privacy` atiende lo que la Ley 1581 obliga
sobre los **datos** del titular; esto atiende lo que el paciente reclama sobre
**la atención**.

Farmacovigilancia
-----------------
La Resolución 1403 de 2007 obliga al servicio farmacéutico a tener un programa
de farmacovigilancia y a reportar las reacciones adversas al INVIMA.

`PatientAllergy` no cubría esto aunque lo pareciera: registra la alergia de un
paciente para impedir una prescripción futura. Mira hacia adelante y es de uso
clínico. El reporte mira hacia atrás y es de uso poblacional: sirve para que el
INVIMA detecte que un lote o un principio activo están dañando a mucha gente.

Ninguno de los dos radica ante la autoridad. El INVIMA recibe por su propio
formato y sus canales. Lo que se cierra aquí es el hueco anterior: que el evento
no existiera como registro con plazo y con responsable.
"""

import secrets
from datetime import timedelta

from time_utils import colombia_now

# Días hábiles aproximados a calendario. Se toma el margen corto a propósito:
# vencer antes en el sistema que en la realidad avisa a tiempo; al revés, no.
DIAS_HABILES_A_CALENDARIO = 1.4


def _codigo_radicado():
    """Código visible del radicado. Legible al teléfono y sin ambigüedades.

    Se excluyen I, O, 0 y 1: en una llamada, o dictados a alguien que anota a
    mano, se confunden entre sí.
    """
    alfabeto = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    cuerpo = ''.join(secrets.choice(alfabeto) for _ in range(8))
    return 'PQRS-%s' % cuerpo


# --- PQRS -------------------------------------------------------------------

def radicar_pqrs(db, usuario, tipo, asunto, detalle, clinic_id=None,
                 history_id=None, commit=True):
    """Radica una PQRS y devuelve el registro con su número.

    El plazo se fija al radicar, no al atenderla: es lo que permite saber que
    algo está vencido sin que nadie lo haya mirado.
    """
    from models import PQRS_DIAS_RESPUESTA, PQRS_RECIBIDA, PQRS_TIPOS, ServiceComplaint

    tipo = (tipo or '').strip().lower()
    if tipo not in PQRS_TIPOS:
        raise ValueError('Tipo de solicitud no reconocido.')

    asunto = (asunto or '').strip()
    detalle = (detalle or '').strip()
    if len(asunto) < 5:
        raise ValueError('Indique un asunto de al menos 5 caracteres.')
    if len(detalle) < 15:
        raise ValueError('Describa su solicitud con al menos 15 caracteres '
                         'para poder atenderla.')

    ahora = colombia_now()
    dias = int(PQRS_DIAS_RESPUESTA * DIAS_HABILES_A_CALENDARIO)

    for _ in range(10):
        codigo = _codigo_radicado()
        if not ServiceComplaint.query.filter_by(ticket_code=codigo).first():
            break

    registro = ServiceComplaint(
        ticket_code=codigo,
        user_id=usuario.id,
        clinic_id=clinic_id if clinic_id is not None else getattr(usuario, 'clinic_id', None),
        complaint_type=tipo,
        subject=asunto[:200],
        detail=detalle,
        status=PQRS_RECIBIDA,
        submitted_at=ahora,
        due_at=ahora + timedelta(days=dias),
        related_history_id=history_id,
    )
    db.session.add(registro)
    if commit:
        db.session.commit()
    return registro


def responder_pqrs(db, registro, usuario_id, respuesta):
    """Cierra una PQRS. Exige respuesta escrita."""
    from models import PQRS_RESUELTA

    respuesta = (respuesta or '').strip()
    if len(respuesta) < 20:
        raise ValueError('Escriba la respuesta que se le dio al usuario '
                         '(mínimo 20 caracteres). Es lo que acredita que se '
                         'atendió.')

    registro.status = PQRS_RESUELTA
    registro.resolution = respuesta
    registro.resolved_at = colombia_now()
    registro.resolved_by_id = usuario_id
    db.session.commit()
    return registro


def pqrs_abiertas(clinic_id=None, solo_vencidas=False):
    from models import PQRS_RESUELTA, ServiceComplaint

    consulta = ServiceComplaint.query.filter(
        ServiceComplaint.status != PQRS_RESUELTA)
    if clinic_id is not None:
        consulta = consulta.filter_by(clinic_id=clinic_id)
    filas = consulta.order_by(ServiceComplaint.due_at.asc()).all()
    if solo_vencidas:
        filas = [f for f in filas if f.is_overdue]
    return filas


# --- Farmacovigilancia ------------------------------------------------------

def registrar_evento_adverso(db, paciente, medicamento, descripcion,
                             reportado_por_id=None, seriedad=None,
                             history_id=None, cum=None, lote=None,
                             fecha_inicio=None, clinic_id=None, commit=True):
    """Abre una sospecha de reacción adversa.

    El plazo depende de la seriedad: una reacción seria se reporta en 72 horas,
    el resto dentro del mes.
    """
    from models import (AdverseDrugEvent, FARMACO_DIAS_NO_SERIA,
                        FARMACO_HORAS_SERIA, FARMACO_SOSPECHA,
                        SERIEDAD_NO_SERIA, SERIEDAD_SERIA)

    medicamento = (medicamento or '').strip()
    descripcion = (descripcion or '').strip()
    if not medicamento:
        raise ValueError('Indique el medicamento sospechoso.')
    if len(descripcion) < 15:
        raise ValueError('Describa la reacción observada (mínimo 15 caracteres).')

    seriedad = seriedad if seriedad in (SERIEDAD_SERIA, SERIEDAD_NO_SERIA) else SERIEDAD_NO_SERIA
    ahora = colombia_now()
    vencimiento = (ahora + timedelta(hours=FARMACO_HORAS_SERIA)
                   if seriedad == SERIEDAD_SERIA
                   else ahora + timedelta(days=FARMACO_DIAS_NO_SERIA))

    try:
        from clinical_safety import normalize_drug
        normalizado = normalize_drug(medicamento)
    except Exception:
        normalizado = medicamento.strip().lower()

    evento = AdverseDrugEvent(
        clinic_id=clinic_id if clinic_id is not None else getattr(paciente, 'clinic_id', None),
        patient_id=paciente.id,
        reported_by_id=reportado_por_id,
        medical_history_id=history_id,
        medication=medicamento,
        medication_normalized=(normalizado or '')[:180] or None,
        cum_code=(cum or '').strip()[:30] or None,
        batch_number=(lote or '').strip()[:60] or None,
        description=descripcion,
        seriousness=seriedad,
        onset_date=fecha_inicio,
        status=FARMACO_SOSPECHA,
        detected_at=ahora,
        due_at=vencimiento,
    )
    db.session.add(evento)
    if commit:
        db.session.commit()
    return evento


def marcar_reportado_invima(db, evento, usuario_id, referencia, nota=None):
    """Registra que el evento se radicó ante el INVIMA."""
    from models import FARMACO_REPORTADO

    referencia = (referencia or '').strip()
    if not referencia:
        raise ValueError('Registre el número del reporte radicado ante el '
                         'INVIMA. Es la constancia de que se reportó.')

    evento.status = FARMACO_REPORTADO
    evento.reported_at = colombia_now()
    evento.reported_by_id = usuario_id
    evento.invima_reference = referencia[:60]
    if nota:
        evento.resolution_note = nota
    db.session.commit()
    return evento


def descartar_evento(db, evento, usuario_id, motivo):
    from models import FARMACO_DESCARTADO

    motivo = (motivo or '').strip()
    if len(motivo) < 15:
        raise ValueError('Explique por qué se descarta la relación con el '
                         'medicamento (mínimo 15 caracteres).')

    evento.status = FARMACO_DESCARTADO
    evento.reported_at = colombia_now()
    evento.reported_by_id = usuario_id
    evento.resolution_note = motivo
    db.session.commit()
    return evento


def eventos_pendientes(clinic_id=None, solo_vencidos=False):
    from models import AdverseDrugEvent, FARMACO_SOSPECHA

    consulta = AdverseDrugEvent.query.filter_by(status=FARMACO_SOSPECHA)
    if clinic_id is not None:
        consulta = consulta.filter_by(clinic_id=clinic_id)
    filas = consulta.order_by(AdverseDrugEvent.due_at.asc()).all()
    if solo_vencidos:
        filas = [f for f in filas if f.is_overdue]
    return filas


def senal_por_medicamento(clinic_id=None, minimo=3):
    """Medicamentos con varios eventos registrados.

    No es análisis de señal en el sentido farmacoepidemiológico, que exige
    denominadores de exposición que esta aplicación no tiene. Es lo que sí
    puede hacerse con los datos disponibles: mostrar que un mismo principio
    activo lleva varias sospechas, para que alguien lo mire.
    """
    from models import AdverseDrugEvent, FARMACO_DESCARTADO

    consulta = AdverseDrugEvent.query.filter(
        AdverseDrugEvent.status != FARMACO_DESCARTADO)
    if clinic_id is not None:
        consulta = consulta.filter_by(clinic_id=clinic_id)

    conteo = {}
    for evento in consulta.all():
        clave = evento.medication_normalized or 'sin normalizar'
        conteo[clave] = conteo.get(clave, 0) + 1
    return sorted(((k, v) for k, v in conteo.items() if v >= minimo),
                  key=lambda par: par[1], reverse=True)
