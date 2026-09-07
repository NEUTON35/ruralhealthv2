# -*- coding: utf-8 -*-
"""Vigilancia en salud publica: deteccion de eventos notificables al Sivigila.

Marco normativo
---------------
El Decreto 3518 de 2006, compilado en el Decreto 780 de 2016, obliga a toda
institucion o profesional que genere informacion de interes en salud publica a
notificar los eventos de reporte obligatorio. No distingue entre publico y
privado ni por tamano, y preve sanciones. El prestador actua como Unidad
Primaria Generadora de Datos.

Que hace este modulo y que no
-----------------------------
**No radica la notificacion.** Eso ocurre en el sistema del INS (Sivigila Web),
con credenciales del prestador y con la ficha del evento, que pide datos que
esta aplicacion no captura. Fingir una radicacion seria peor que no tener nada:
dejaria al prestador creyendo que cumplio.

Lo que hace es cerrar el hueco real, que era que **el sistema no sabia que un
caso era notificable**. Al guardar una atencion, contrasta el diagnostico con
el catalogo de eventos y, si corresponde, abre un pendiente con su plazo, avisa
a quien atendio y conserva la constancia de si se notifico, cuando y quien.

Sobre la deteccion por CIE-10
-----------------------------
Es deliberadamente amplia. Un falso positivo cuesta que alguien revise un caso
y lo descarte con una nota; un falso negativo cuesta un brote sin notificar. En
vigilancia epidemiologica esa asimetria no esta en discusion, asi que el
umbral se pone del lado de avisar de mas.

Por eso tampoco cierra el pendiente por su cuenta: descartarlo exige que una
persona escriba por que.
"""

import logging
from datetime import timedelta

from time_utils import colombia_now

logger = logging.getLogger('surveillance')

# Plazo de la notificacion inmediata. Los lineamientos del INS la definen como
# "tan pronto se identifique el caso"; 24 horas es el limite operativo con el
# que se mide.
PLAZO_INMEDIATA_HORAS = 24
# La notificacion semanal cierra con la semana epidemiologica.
PLAZO_SEMANAL_DIAS = 7


def _normalizar(codigo):
    return ''.join(ch for ch in (codigo or '').strip().upper() if ch.isalnum())


def eventos_para_diagnostico(cie10):
    """Eventos del catalogo cuyo prefijo CIE-10 coincide con el diagnostico."""
    from models import NotifiableEvent

    codigo = _normalizar(cie10)
    if not codigo:
        return []

    coincidencias = []
    for evento in NotifiableEvent.query.filter_by(active=True).all():
        for prefijo in evento.prefixes:
            if codigo.startswith(_normalizar(prefijo)):
                coincidencias.append(evento)
                break
    return coincidencias


def _vencimiento(evento, desde):
    if evento.is_immediate:
        return desde + timedelta(hours=PLAZO_INMEDIATA_HORAS)
    return desde + timedelta(days=PLAZO_SEMANAL_DIAS)


def detectar(db, historia, commit=False):
    """Abre los pendientes de notificacion que correspondan a una atencion.

    Idempotente: si ya existe el pendiente para ese evento y esa atencion, no
    lo duplica. Devuelve la lista de eventos detectados.
    """
    from models import SIVIGILA_PENDIENTE, SivigilaNotification

    if historia is None or not getattr(historia, 'id', None):
        return []
    if not getattr(historia, 'cie10_code', None):
        return []

    ahora = colombia_now()
    abiertos = []
    for evento in eventos_para_diagnostico(historia.cie10_code):
        existente = SivigilaNotification.query.filter_by(
            medical_history_id=historia.id, event_code=evento.code).first()
        if existente is not None:
            continue
        db.session.add(SivigilaNotification(
            clinic_id=getattr(historia, 'clinic_id', None),
            medical_history_id=historia.id,
            patient_id=historia.patient_id,
            event_code=evento.code,
            event_name=evento.name,
            periodicity=evento.periodicity,
            cie10_code=historia.cie10_code,
            status=SIVIGILA_PENDIENTE,
            detected_at=ahora,
            due_at=_vencimiento(evento, ahora),
        ))
        abiertos.append(evento)

    if abiertos and commit:
        db.session.commit()
    return abiertos


def pendientes(clinic_id=None, solo_vencidos=False):
    """Notificaciones sin resolver, las mas urgentes primero."""
    from models import SIVIGILA_PENDIENTE, SivigilaNotification

    consulta = SivigilaNotification.query.filter_by(status=SIVIGILA_PENDIENTE)
    if clinic_id is not None:
        consulta = consulta.filter_by(clinic_id=clinic_id)
    filas = consulta.order_by(SivigilaNotification.due_at.asc()).all()
    if solo_vencidos:
        filas = [f for f in filas if f.is_overdue]
    return filas


def marcar_notificada(db, notificacion, usuario_id, ficha, nota=None):
    """Registra que el caso se radico en Sivigila.

    Exige la referencia de la ficha: sin ella no hay constancia de nada, y el
    registro serviria solo para tranquilizar a quien lo marca.
    """
    from models import SIVIGILA_NOTIFICADA

    ficha = (ficha or '').strip()
    if not ficha:
        raise ValueError('Debe registrarse el numero de la ficha radicada en '
                         'Sivigila. Es la constancia de la notificacion.')

    notificacion.status = SIVIGILA_NOTIFICADA
    notificacion.notified_at = colombia_now()
    notificacion.notified_by_id = usuario_id
    notificacion.ficha_reference = ficha[:60]
    if nota:
        notificacion.resolution_note = nota
    db.session.commit()
    return notificacion


def descartar(db, notificacion, usuario_id, motivo):
    """Cierra un pendiente sin notificar. Exige justificacion escrita.

    La deteccion es amplia a proposito, asi que habra falsos positivos. Pero
    descartar un caso de vigilancia sin dejar dicho por que convierte la lista
    en algo que se vacia por incomodidad.
    """
    from models import SIVIGILA_DESCARTADA

    motivo = (motivo or '').strip()
    if len(motivo) < 15:
        raise ValueError('Explique por que el caso no es notificable '
                         '(minimo 15 caracteres). Queda en el registro.')

    notificacion.status = SIVIGILA_DESCARTADA
    notificacion.notified_at = colombia_now()
    notificacion.notified_by_id = usuario_id
    notificacion.resolution_note = motivo
    db.session.commit()
    return notificacion


def resumen(clinic_id=None):
    """Conteo por estado, para el panel."""
    from models import (SIVIGILA_DESCARTADA, SIVIGILA_NOTIFICADA,
                        SIVIGILA_PENDIENTE, SivigilaNotification)

    consulta = SivigilaNotification.query
    if clinic_id is not None:
        consulta = consulta.filter_by(clinic_id=clinic_id)
    filas = consulta.all()
    return {
        'pendientes': sum(1 for f in filas if f.status == SIVIGILA_PENDIENTE),
        'vencidas': sum(1 for f in filas if f.is_overdue),
        'notificadas': sum(1 for f in filas if f.status == SIVIGILA_NOTIFICADA),
        'descartadas': sum(1 for f in filas if f.status == SIVIGILA_DESCARTADA),
    }
