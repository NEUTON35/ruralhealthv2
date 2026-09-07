# -*- coding: utf-8 -*-
"""Cola de salida de los Resumenes Digitales de Atencion.

Encolar y transmitir estan separados a proposito. Ver la explicacion en
`models.RDASubmission`: la atencion clinica no puede quedar atada a que el
servidor del Ministerio responda.

El reintento usa espera exponencial con techo. No hay reintento infinito: tras
`MAX_INTENTOS` el envio queda en `rechazado` y aparece en el panel para que
alguien lo mire. Un envio que lleva cien intentos fallidos no se arregla con el
ciento uno, y la cola dejaria de ser legible.
"""

import hashlib
import json
import logging
from datetime import timedelta

from time_utils import colombia_now

from . import terminology as T
from .client import DuplicadoError, IHCEClient, IHCEError
from .mapping import construir_bundle_consulta
from .validation import validar_bundle, validar_datos_minimos

logger = logging.getLogger('ihce')

MAX_INTENTOS = 8
ESPERA_BASE_MINUTOS = 5
ESPERA_MAXIMA_MINUTOS = 24 * 60


def _espera(intentos):
    """Espera creciente: 5, 10, 20, 40... minutos, con techo de 24 horas."""
    minutos = min(ESPERA_BASE_MINUTOS * (2 ** max(0, intentos - 1)),
                  ESPERA_MAXIMA_MINUTOS)
    return timedelta(minutes=minutos)


def encolar(db, historia, commit=False):
    """Registra que una atencion debe remitirse. Idempotente.

    Se llama al cerrar la atencion. No hace red ni valida: si algo falta, se
    detecta al transmitir y el envio queda en `bloqueado` con el motivo. Encolar
    tiene que ser barato y no puede fallar, porque corre dentro de la
    transaccion de la atencion.
    """
    from models import RDASubmission, RDA_PENDIENTE

    if historia is None or not getattr(historia, 'id', None):
        return None

    existente = RDASubmission.query.filter_by(
        medical_history_id=historia.id).first()
    if existente is not None:
        return existente

    envio = RDASubmission(
        medical_history_id=historia.id,
        patient_id=historia.patient_id,
        doctor_id=historia.doctor_id,
        clinic_id=getattr(historia, 'clinic_id', None),
        status=RDA_PENDIENTE,
        next_attempt_at=colombia_now(),
        guide_version=T.GUIA_VERSION,
    )
    db.session.add(envio)
    if commit:
        db.session.commit()
    return envio


def armar_bundle(historia):
    """Construye el Bundle de una atencion reuniendo lo que exige el perfil.

    Devuelve `(bundle, errores)`. Si hay errores, el bundle puede ser None.
    """
    from models import PatientAllergy, PatientChronicCondition, MedicalOrder, User, Clinic
    from models import db as _db  # noqa: F401  (asegura el registro de modelos)

    paciente = historia.patient
    profesional = historia.doctor
    clinica = Clinic.query.get(historia.clinic_id) if historia.clinic_id else None

    if paciente is None:
        return None, ['La atencion no tiene paciente asociado.']
    if profesional is None:
        return None, ['La atencion no tiene profesional asociado.']
    if clinica is None:
        return None, ['La atencion no tiene institucion asociada.']

    errores = validar_datos_minimos(paciente, profesional, clinica, historia)
    if errores:
        return None, errores

    alergias = (PatientAllergy.query
                .filter_by(patient_id=paciente.id)
                .filter(PatientAllergy.status != 'descartada')
                .all())
    condiciones = (PatientChronicCondition.query
                   .filter_by(patient_id=paciente.id, status='activa')
                   .all())

    medicamentos = []
    orden = (MedicalOrder.query
             .filter_by(patient_id=paciente.id, doctor_id=profesional.id)
             .filter(MedicalOrder.annulled_at.is_(None))
             .order_by(MedicalOrder.id.desc())
             .first())
    if orden is not None and orden.meds_json:
        try:
            crudo = json.loads(orden.meds_json)
            if isinstance(crudo, list):
                medicamentos = [m for m in crudo if isinstance(m, dict)]
        except (ValueError, TypeError):
            medicamentos = []

    bundle = construir_bundle_consulta(
        historia, paciente, profesional, clinica,
        alergias=alergias, condiciones=condiciones, medicamentos=medicamentos,
        identificador=historia.id)

    return bundle, validar_bundle(bundle)


def huella(bundle):
    material = json.dumps(bundle, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


def procesar_envio(db, envio, cliente):
    """Transmite un envio y actualiza su estado. No lanza excepciones.

    Devuelve el estado resultante.
    """
    from models import (RDA_ACEPTADO, RDA_BLOQUEADO, RDA_DUPLICADO,
                        RDA_PENDIENTE, RDA_RECHAZADO)

    ahora = colombia_now()
    envio.attempts = (envio.attempts or 0) + 1
    envio.updated_at = ahora

    historia = envio.medical_history
    if historia is None:
        envio.status = RDA_BLOQUEADO
        envio.last_error = 'La atencion asociada ya no existe.'
        return envio.status

    bundle, errores = armar_bundle(historia)
    if errores:
        # Datos incompletos o estructura invalida. Reintentar no arregla nada:
        # alguien tiene que completar el perfil o el diagnostico.
        envio.status = RDA_BLOQUEADO
        envio.last_error = ' | '.join(errores)[:2000]
        envio.next_attempt_at = None
        return envio.status

    envio.bundle_hash = huella(bundle)
    envio.guide_version = T.GUIA_VERSION

    try:
        respuesta = cliente.enviar_rda(bundle)
    except DuplicadoError as e:
        # El Ministerio ya lo tenia. El deber esta cumplido.
        envio.status = RDA_DUPLICADO
        envio.last_error = e.detalle
        envio.sent_at = ahora
        envio.next_attempt_at = None
        return envio.status
    except IHCEError as e:
        envio.last_error = ('%s %s' % (e, e.detalle or '')).strip()[:2000]
        if e.permanente:
            envio.status = RDA_RECHAZADO
            envio.next_attempt_at = None
        elif envio.attempts >= MAX_INTENTOS:
            envio.status = RDA_RECHAZADO
            envio.next_attempt_at = None
            envio.last_error = ('Se agotaron los %d intentos. Ultimo error: %s'
                                % (MAX_INTENTOS, envio.last_error))[:2000]
        else:
            envio.status = RDA_PENDIENTE
            envio.next_attempt_at = ahora + _espera(envio.attempts)
        return envio.status

    envio.status = RDA_ACEPTADO
    envio.sent_at = ahora
    envio.next_attempt_at = None
    envio.last_error = None
    envio.remote_id = _extraer_id(respuesta)
    return envio.status


def _extraer_id(respuesta):
    """Identificador que devuelve el Ministerio como acuse de recibo."""
    if not isinstance(respuesta, dict):
        return None
    ident = respuesta.get('identifier')
    if isinstance(ident, dict) and ident.get('value'):
        return str(ident['value'])[:120]
    if respuesta.get('id'):
        return str(respuesta['id'])[:120]
    for entrada in respuesta.get('entry', []) or []:
        recurso = entrada.get('resource') or {}
        if recurso.get('id'):
            return str(recurso['id'])[:120]
    return None


def procesar_pendientes(db, config, limite=50, cliente=None):
    """Transmite los envios que toca. Devuelve un resumen por estado."""
    from models import RDASubmission, RDA_PENDIENTE

    resumen = {'procesados': 0, 'aceptados': 0, 'duplicados': 0,
               'rechazados': 0, 'bloqueados': 0, 'reintentar': 0}

    if not config.is_enabled:
        resumen['motivo'] = ('Transmision inactiva. Faltan: %s'
                             % ', '.join(config.faltantes()) if config.faltantes()
                             else 'Transmision desactivada por IHCE_ENABLED.')
        return resumen

    cliente = cliente or IHCEClient(config)
    ahora = colombia_now()

    pendientes = (RDASubmission.query
                  .filter(RDASubmission.status == RDA_PENDIENTE)
                  .filter((RDASubmission.next_attempt_at.is_(None))
                          | (RDASubmission.next_attempt_at <= ahora))
                  .order_by(RDASubmission.created_at)
                  .limit(limite)
                  .all())

    for envio in pendientes:
        estado = procesar_envio(db, envio, cliente)
        resumen['procesados'] += 1
        resumen[{'aceptado': 'aceptados', 'duplicado': 'duplicados',
                 'rechazado': 'rechazados', 'bloqueado': 'bloqueados',
                 'pendiente': 'reintentar'}.get(estado, 'reintentar')] += 1
        db.session.commit()

    return resumen


def resumen_estado(db):
    """Cuantos envios hay en cada estado. Para el panel y para `manage.py`.

    Devuelve None si la tabla todavia no existe. Pasa entre desplegar el codigo
    y correr la migracion, y un panel de estado no puede ser lo que tumbe la
    aplicacion: quien consulta el estado es justamente quien esta averiguando
    que pasa.
    """
    from sqlalchemy.exc import DatabaseError, OperationalError, ProgrammingError

    from models import RDASubmission

    try:
        filas = db.session.query(
            RDASubmission.status, db.func.count(RDASubmission.id)
        ).group_by(RDASubmission.status).all()
    except (OperationalError, ProgrammingError, DatabaseError):
        db.session.rollback()
        return None
    return {estado: cantidad for estado, cantidad in filas}
