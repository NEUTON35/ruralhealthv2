# -*- coding: utf-8 -*-
"""Construye el Bundle FHIR del Resumen Digital de Atencion.

Reglas que impone el Manual de operaciones v1.4 y que este modulo respeta:

- `Bundle.type` es `document` (regla 1).
- La primera entrada es el `Composition` y solo puede haber uno (reglas 2 y 3a).
- Todas las secciones del perfil viajan siempre. La que no tenga entradas lleva
  `emptyReason` con el codigo y el texto que fija el manual (regla 3b).
- Los recursos que se crean usan ids `TipoRecurso-N` y se referencian con
  `#TipoRecurso-N`; los que ya existen en el IHCE se referencian por su
  identificador natural (seccion 5.3).

Lo que este modulo NO hace: inventar datos. Si el paciente no tiene fecha de
nacimiento o el profesional no tiene registro medico, el Bundle sale incompleto
y `validation.validar_bundle` lo rechaza antes de gastar una llamada de red. Es
preferible un rechazo local explicito a un 400 del Ministerio.
"""

from datetime import datetime

from . import terminology as T


def _texto_narrativo(texto):
    return {
        'status': 'generated',
        'div': "<div xmlns='http://www.w3.org/1999/xhtml'>%s</div>" % texto,
    }


def _seccion_vacia(seccion):
    return {
        'title': seccion['titulo'],
        'code': {'coding': [{'system': T.LOINC, 'code': seccion['codigo'],
                             'display': seccion['display']}]},
        'emptyReason': {'coding': [{'system': T.RAZON_VACIA_SYSTEM,
                                    'code': T.RAZON_VACIA_CODIGO,
                                    'display': T.RAZON_VACIA_DISPLAY}]},
        'text': _texto_narrativo(T.RAZON_VACIA_TEXTO),
    }


def _seccion_con_entradas(seccion, referencias):
    if not referencias:
        return _seccion_vacia(seccion)
    return {
        'title': seccion['titulo'],
        'code': {'coding': [{'system': T.LOINC, 'code': seccion['codigo'],
                             'display': seccion['display']}]},
        'entry': [{'reference': r} for r in referencias],
    }


def _instante(valor):
    """Fecha en el formato que exige FHIR, con la zona horaria de Colombia."""
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            return valor.strftime('%Y-%m-%dT%H:%M:%S-05:00')
        return valor.isoformat()
    return valor.strftime('%Y-%m-%d')


def _fecha(valor):
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor[:10]
    return valor.strftime('%Y-%m-%d')


def _nombres(usuario):
    """Nombres de pila, maximo dos: el perfil fija given 1..2."""
    dados = [p for p in [getattr(usuario, 'first_name', None),
                         getattr(usuario, 'second_name', None)] if p]
    if not dados and getattr(usuario, 'name', None):
        dados = usuario.name.split()[:1]
    return dados[:2]


def _apellidos(usuario):
    partes = [p for p in [getattr(usuario, 'first_surname', None),
                          getattr(usuario, 'second_surname', None)] if p]
    if partes:
        return ' '.join(partes)
    nombre = getattr(usuario, 'name', '') or ''
    trozos = nombre.split()
    return trozos[-1] if len(trozos) > 1 else ''


def construir_paciente(paciente):
    """Recurso Patient. Se referencia por TipoDocumento-Numero."""
    tipo = (getattr(paciente, 'document_type', None) or 'CC').strip().upper()
    numero = (getattr(paciente, 'cedula', None) or '').strip()
    ident = T.referencia_paciente(tipo, numero)

    recurso = {
        'resourceType': 'Patient',
        'id': ident,
        'meta': {'profile': [T.PERFIL_PATIENT]},
        'identifier': [{
            'use': 'official',
            'type': {'coding': [{'system': T.IDENT_SYSTEM, 'code': tipo,
                                 'display': T.TIPOS_DOCUMENTO.get(tipo, tipo)}]},
            'system': T.IDENT_SYSTEM,
            'value': numero,
        }],
        'active': bool(getattr(paciente, 'is_active_account', True)),
        'name': [{
            'use': 'official',
            'family': _apellidos(paciente),
            'given': _nombres(paciente),
        }],
    }

    sexo = T.SEXO_FHIR.get((getattr(paciente, 'sex', None) or '').strip())
    if sexo:
        recurso['gender'] = sexo
    nacimiento = _fecha(getattr(paciente, 'birth_date', None))
    if nacimiento:
        recurso['birthDate'] = nacimiento

    direccion = {'use': 'home', 'type': 'physical', 'country': 'CO'}
    if getattr(paciente, 'municipality_code', None):
        direccion['city'] = paciente.municipality_code
    if getattr(paciente, 'department_code', None):
        direccion['district'] = paciente.department_code
    recurso['address'] = [direccion]

    if getattr(paciente, 'phone', None):
        recurso['telecom'] = [{'system': 'phone', 'value': paciente.phone,
                               'use': 'mobile'}]
    return recurso


def construir_profesional(profesional):
    """Recurso Practitioner.

    El numero de registro medico es obligatorio: el manual exige que el
    Practitioner este activo en RETHUS (regla 8), y sin registro no hay forma de
    identificarlo alli.
    """
    tipo = (getattr(profesional, 'document_type', None) or 'CC').strip().upper()
    numero = (getattr(profesional, 'cedula', None) or '').strip()
    ident = T.referencia_profesional(tipo, numero)

    recurso = {
        'resourceType': 'Practitioner',
        'id': ident,
        'meta': {'profile': [T.PERFIL_PRACTITIONER]},
        'identifier': [{
            'use': 'official',
            'type': {'coding': [{'system': T.IDENT_SYSTEM, 'code': tipo,
                                 'display': T.TIPOS_DOCUMENTO.get(tipo, tipo)}]},
            'system': T.IDENT_SYSTEM,
            'value': numero,
        }],
        'active': True,
        'name': [{
            'use': 'official',
            'family': _apellidos(profesional),
            'given': _nombres(profesional),
        }],
    }
    registro = getattr(profesional, 'medical_registration', None)
    if registro:
        recurso['identifier'].append({
            'use': 'secondary',
            'type': {'coding': [{'system': T.V2_0203, 'code': 'MD',
                                 'display': 'Medical License number'}]},
            'value': str(registro).strip(),
        })
    return recurso


def construir_organizacion(clinica):
    """Recurso Organization de la IPS, referenciado por su codigo de habilitacion."""
    codigo = T.referencia_ips(getattr(clinica, 'habilitacion_code', None))
    recurso = {
        'resourceType': 'Organization',
        'id': codigo,
        'meta': {'profile': [T.PERFIL_ORGANIZACION_IPS]},
        'identifier': [{'system': T.HABILITACION_SYSTEM, 'value': codigo}],
        'active': bool(getattr(clinica, 'activa', True)),
        'name': getattr(clinica, 'legal_name', None) or getattr(clinica, 'name', ''),
    }
    return recurso


def construir_encuentro(historia, ref_paciente, ref_profesional, ref_ips, indice=0):
    """Recurso Encounter de la atencion ambulatoria.

    `period` sale de la fecha del registro clinico. El manual usa subject,
    period, serviceProvider y participant para detectar duplicados (regla 5), asi
    que estos cuatro campos determinan la identidad del RDA.
    """
    inicio = _instante(getattr(historia, 'created_at', None))
    recurso = {
        'resourceType': 'Encounter',
        'id': 'Encounter-%d' % indice,
        'meta': {'profile': [T.PERFIL_ENCOUNTER_CONSULTA]},
        'status': 'finished',
        'class': {'system': T.V3_ACT_CODE, 'code': 'AMB', 'display': 'ambulatory'},
        'subject': {'reference': 'Patient/' + ref_paciente},
        'participant': [{
            'individual': {'reference': 'Practitioner/' + ref_profesional},
        }],
        'period': {'start': inicio, 'end': inicio},
        'serviceProvider': {'reference': 'Organization/' + ref_ips},
    }
    return recurso


def construir_condicion(codigo_cie10, descripcion, ref_paciente, ref_encuentro,
                        indice=0, estado='active', fecha=None):
    recurso = {
        'resourceType': 'Condition',
        'id': 'Condition-%d' % indice,
        'meta': {'profile': [T.PERFIL_CONDITION]},
        'clinicalStatus': {'coding': [{
            'system': 'http://terminology.hl7.org/CodeSystem/condition-clinical',
            'code': estado,
        }]},
        'code': {
            'coding': [{'system': T.CIE10_SYSTEM, 'code': (codigo_cie10 or '').strip().upper()}],
            'text': descripcion or '',
        },
        'subject': {'reference': 'Patient/' + ref_paciente},
        'encounter': {'reference': ref_encuentro},
    }
    if descripcion:
        recurso['code']['coding'][0]['display'] = descripcion
    inicio = _instante(fecha)
    if inicio:
        recurso['onsetDateTime'] = inicio
    return recurso


def construir_alergia(alergia, ref_paciente, indice=0):
    sustancia = getattr(alergia, 'substance', None) or ''
    recurso = {
        'resourceType': 'AllergyIntolerance',
        'id': 'AllergyIntolerance-%d' % indice,
        'meta': {'profile': [T.PERFIL_ALERGIA]},
        'clinicalStatus': {'coding': [{
            'system': 'http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical',
            'code': 'active' if getattr(alergia, 'is_active', True) else 'inactive',
        }]},
        'verificationStatus': {'coding': [{
            'system': 'http://terminology.hl7.org/CodeSystem/allergyintolerance-verification',
            'code': T.ESTADO_ALERGIA.get(getattr(alergia, 'status', ''), 'unconfirmed'),
        }]},
        'code': {'text': sustancia},
        'patient': {'reference': 'Patient/' + ref_paciente},
    }
    severidad = T.SEVERIDAD_ALERGIA.get(getattr(alergia, 'severity', '') or '')
    reaccion = getattr(alergia, 'reaction', None)
    if severidad or reaccion:
        manifestacion = {'manifestation': [{'text': reaccion or sustancia}]}
        if severidad:
            manifestacion['severity'] = severidad
        recurso['reaction'] = [manifestacion]
    inicio = _fecha(getattr(alergia, 'onset_date', None))
    if inicio:
        recurso['onsetDateTime'] = inicio
    return recurso


def construir_prescripcion(medicamento, ref_paciente, ref_profesional,
                           ref_encuentro, indice=0, autoria=None):
    """MedicationRequest a partir de un medicamento de la orden.

    `medicamento` es el diccionario que guarda `MedicalOrder.meds_json`.
    """
    nombre = (medicamento.get('nombre_med') or medicamento.get('nombre') or '').strip()
    recurso = {
        'resourceType': 'MedicationRequest',
        'id': 'MedicationRequest-%d' % indice,
        'meta': {'profile': [T.PERFIL_MEDICATION_REQUEST]},
        'status': 'active',
        'intent': 'order',
        'medicationCodeableConcept': {'text': nombre},
        'subject': {'reference': 'Patient/' + ref_paciente},
        'encounter': {'reference': ref_encuentro},
        'requester': {'reference': 'Practitioner/' + ref_profesional},
    }
    codigo_cum = medicamento.get('cum') or medicamento.get('codigo_cum')
    if codigo_cum:
        recurso['medicationCodeableConcept']['coding'] = [
            {'system': T.CUM_SYSTEM, 'code': str(codigo_cum).strip()}
        ]
    if autoria:
        recurso['authoredOn'] = _instante(autoria)

    posologia = {}
    indicaciones = medicamento.get('indicaciones') or medicamento.get('dosis')
    if indicaciones:
        posologia['text'] = str(indicaciones)
    cantidad = medicamento.get('cantidad')
    if cantidad:
        try:
            posologia['doseAndRate'] = [{'doseQuantity': {'value': float(cantidad)}}]
        except (TypeError, ValueError):
            pass
    if posologia:
        recurso['dosageInstruction'] = [posologia]
    return recurso


def construir_bundle_consulta(historia, paciente, profesional, clinica,
                              alergias=(), condiciones=(), medicamentos=(),
                              identificador=None):
    """Bundle `document` completo del RDA de consulta.

    Devuelve el diccionario listo para serializar. No hace red ni toca la base.
    """
    ref_paciente = T.referencia_paciente(
        getattr(paciente, 'document_type', None) or 'CC',
        getattr(paciente, 'cedula', None))
    ref_profesional = T.referencia_profesional(
        getattr(profesional, 'document_type', None) or 'CC',
        getattr(profesional, 'cedula', None))
    ref_ips = T.referencia_ips(getattr(clinica, 'habilitacion_code', None))

    recursos = [
        construir_paciente(paciente),
        construir_profesional(profesional),
        construir_organizacion(clinica),
    ]

    encuentro = construir_encuentro(historia, ref_paciente, ref_profesional, ref_ips)
    recursos.append(encuentro)
    ref_encuentro = 'Encounter/' + encuentro['id']

    # --- Diagnosticos -------------------------------------------------------
    # La seccion de problemas es la unica con entry 1..*: el diagnostico de la
    # atencion es obligatorio, y las condiciones cronicas se suman a el.
    condiciones_ref = []
    indice = 0
    if getattr(historia, 'cie10_code', None):
        recursos.append(construir_condicion(
            historia.cie10_code, getattr(historia, 'diagnosis', None),
            ref_paciente, ref_encuentro, indice,
            fecha=getattr(historia, 'created_at', None)))
        condiciones_ref.append('Condition/Condition-%d' % indice)
        indice += 1
    for cronica in condiciones:
        if not getattr(cronica, 'cie10_code', None):
            continue
        recursos.append(construir_condicion(
            cronica.cie10_code, getattr(cronica, 'condition', None),
            ref_paciente, ref_encuentro, indice,
            estado=T.ESTADO_CONDICION.get(getattr(cronica, 'status', ''), 'active'),
            fecha=getattr(cronica, 'diagnosed_on', None)))
        condiciones_ref.append('Condition/Condition-%d' % indice)
        indice += 1

    # --- Alergias -----------------------------------------------------------
    alergias_ref = []
    for i, alergia in enumerate(alergias):
        recursos.append(construir_alergia(alergia, ref_paciente, i))
        alergias_ref.append('AllergyIntolerance/AllergyIntolerance-%d' % i)

    # --- Medicamentos -------------------------------------------------------
    medicamentos_ref = []
    for i, med in enumerate(medicamentos):
        recursos.append(construir_prescripcion(
            med, ref_paciente, ref_profesional, ref_encuentro, i,
            autoria=getattr(historia, 'created_at', None)))
        medicamentos_ref.append('MedicationRequest/MedicationRequest-%d' % i)

    # --- Secciones ----------------------------------------------------------
    por_nombre = {
        'sectionProblems': condiciones_ref,
        'sectionAllergies': alergias_ref,
        'sectionMedications': medicamentos_ref,
        'sectionServiceRequests': medicamentos_ref,
    }
    secciones = [
        _seccion_con_entradas(s, por_nombre.get(s['nombre'], []))
        for s in T.SECCIONES_CONSULTA
    ]

    fecha = _instante(getattr(historia, 'created_at', None))
    composition = {
        'resourceType': 'Composition',
        'id': 'Composition-0',
        'meta': {'profile': [T.PERFIL_COMPOSITION_CONSULTA]},
        'status': 'final',
        'type': {'coding': [{'system': T.LOINC, 'code': '34133-9',
                             'display': 'Summarization of episode note'}]},
        'subject': {'reference': 'Patient/' + ref_paciente},
        'encounter': {'reference': ref_encuentro},
        'date': fecha,
        'author': [{'reference': 'Practitioner/' + ref_profesional}],
        'title': 'RDA Consulta',
        'confidentiality': 'N',
        'custodian': {'reference': 'Organization/' + ref_ips},
        'attester': [{
            'mode': 'legal',
            'time': fecha,
            'party': {'reference': 'Practitioner/' + ref_profesional},
        }],
        'section': secciones,
    }

    bundle = {
        'resourceType': 'Bundle',
        'type': 'document',
        'timestamp': fecha,
        'entry': [{'resource': composition}] + [{'resource': r} for r in recursos],
    }
    if identificador:
        bundle['identifier'] = {
            'system': 'urn:ruralhealth:rda',
            'value': str(identificador),
        }
    return bundle
