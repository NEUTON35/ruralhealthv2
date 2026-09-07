# -*- coding: utf-8 -*-
"""Terminologia del Resumen Digital de Atencion (RDA).

Todo lo que hay aqui esta tomado de la guia de implementacion oficial publicada
en https://vulcano.ihcecol.gov.co/ y del Manual de operaciones de
interoperabilidad IHCE v1.4 del Ministerio de Salud. No se inventa ningun
codigo: cuando un dato no se tiene, la seccion viaja con `emptyReason`, que es
lo que la norma prevee para ese caso.

Las URL canonicas y los codigos LOINC de cada seccion se extrajeron de
`StructureDefinition-CompositionAmbulatoryRDA`, donde estan como valores fijos.
Si el Ministerio publica una version nueva de la guia hay que revisarlos: por eso
`GUIA_VERSION` queda registrado en cada envio.
"""

GUIA_VERSION = '1.0.0'
FHIR_VERSION = '4.0.1'

BASE_PERFIL = 'https://fhir.minsalud.gov.co/rda/StructureDefinition/'
BASE_CS = 'https://fhir.minsalud.gov.co/rda/CodeSystem/'

LOINC = 'http://loinc.org'
SCT = 'http://snomed.info/sct'
CIE10_SYSTEM = BASE_CS + 'CIE10'
CUPS_SYSTEM = BASE_CS + 'CUPS'
CUM_SYSTEM = BASE_CS + 'CUM'
IDENT_SYSTEM = BASE_CS + 'ColombianPersonIdentifier'
HABILITACION_SYSTEM = BASE_CS + 'REPSCodes'
EAPB_SYSTEM = BASE_CS + 'EAPBCodes'
RAZON_VACIA_SYSTEM = 'http://terminology.hl7.org/CodeSystem/list-empty-reason'
V3_ACT_CODE = 'http://terminology.hl7.org/CodeSystem/v3-ActCode'
V2_0203 = 'http://terminology.hl7.org/CodeSystem/v2-0203'

PERFIL_COMPOSITION_CONSULTA = BASE_PERFIL + 'CompositionAmbulatoryRDA'
PERFIL_PATIENT = BASE_PERFIL + 'PatientRDA'
PERFIL_PRACTITIONER = BASE_PERFIL + 'PractitionerRDA'
PERFIL_ENCOUNTER_CONSULTA = BASE_PERFIL + 'EncounterAmbulatoryRDA'
PERFIL_CONDITION = BASE_PERFIL + 'ConditionRDA'
PERFIL_ALERGIA = BASE_PERFIL + 'AllergyIntoleranceRDA'
PERFIL_ORGANIZACION_IPS = BASE_PERFIL + 'CareDeliveryOrganizationRDA'
PERFIL_MEDICATION_REQUEST = BASE_PERFIL + 'MedicationRequestRDA'

# Ruta de la operacion, relativa a la URL base que entrega el Ministerio junto
# con las credenciales. Manual de operaciones v1.4, seccion 5.5.
OPERACION_RDA_CONSULTA = '/Composition/$enviar-rda-consulta'
OPERACION_RDA_PACIENTE = '/Composition/$enviar-rda-paciente'
OPERACION_RDA_URGENCIAS = '/Composition/$enviar-rda-urgencias'
OPERACION_RDA_HOSPITALIZACION = '/Composition/$enviar-rda-hospitalizacion'
OPERACION_PACIENTE_EXACTO = '/Patient/$consultar-paciente-exacto'

CONTENT_TYPE_FHIR = 'application/fhir+json'


# --- Tipos de documento -----------------------------------------------------
# ValueSet ColombianPersonIdentifierCodes de la guia. Se conserva completo
# aunque hoy la aplicacion solo capture cedula: un puesto de salud rural atiende
# menores con registro civil y poblacion migrante con PPT, y el dia que se
# habilite el campo la tabla ya esta.
TIPOS_DOCUMENTO = {
    'CN': 'Certificado de nacido vivo',
    'RC': 'Registro civil',
    'TI': 'Tarjeta de Identidad',
    'CC': 'Cédula ciudadanía',
    'PA': 'Pasaporte',
    'CD': 'Carné diplomático',
    'CE': 'Cédula de extranjería',
    'DE': 'Documento Extranjero',
    'SC': 'Salvoconducto de permanencia',
    'PE': 'Permiso Especial de Permanencia',
    'PT': 'Permiso Temporal de Permanencia',
    'PPT': 'Permiso por protección temporal',
    'PC': 'PEP-TUTOR',
    'RUT': 'Registro Único Tributario',
    'AS': 'Adulto sin identificar',
    'MS': 'Menor sin identificar',
    'SI': 'Sin identificación',
}

# Documentos que no se validan contra el registro nacional EVOL porque no
# corresponden a un colombiano identificado (manual v1.4, regla 4b y 4c).
DOCUMENTOS_SIN_VALIDACION_EVOL = {'PA', 'CD', 'CE', 'DE', 'SC', 'PE', 'PT',
                                  'PPT', 'PC', 'AS', 'MS', 'SI'}

SEXO_FHIR = {
    'M': 'male', 'F': 'female',
    'masculino': 'male', 'femenino': 'female',
    'male': 'male', 'female': 'female',
    'O': 'other', 'I': 'other',
}


# --- Extensiones obligatorias del PatientRDA --------------------------------
# El perfil las marca 1..1 o 1..*, con binding required a estos catalogos.
# Tomados de los CodeSystem publicados en la guia.

EXT_NACIONALIDAD = BASE_PERFIL + 'ExtensionPatientNationality'
EXT_ETNIA = BASE_PERFIL + 'ExtensionPatientEthnicity'
EXT_DISCAPACIDAD = BASE_PERFIL + 'ExtensionPatientDisability'
EXT_ZONA_RESIDENCIA = BASE_PERFIL + 'ExtensionResidenceZone'
EXT_SEXO_BIOLOGICO = BASE_PERFIL + 'ExtensionBiologicalGender'

CS_NACIONALIDAD = BASE_CS + 'ISO31661'
CS_ETNIA = BASE_CS + 'ColombianEthnicGroup'
CS_DISCAPACIDAD = BASE_CS + 'ColombianDisabilityClassification'
CS_ZONA = BASE_CS + 'ColombianResidenceZone'

# Codigo ISO 3166-1 numerico de Colombia. Es el valor por defecto porque es el
# caso mayoritario, no una suposicion sobre la persona: el formulario permite
# cambiarlo y en zona de frontera hay que hacerlo.
NACIONALIDAD_COLOMBIA = '170'

GRUPOS_ETNICOS = {
    '1': 'Indigena',
    '2': 'ROM (Gitano)',
    '3': 'Raizal (Archipielago San Andrés y Providencia)',
    '4': 'Palenquero de San Basilio',
    '5': 'Negro(a) o mulato(a) o afrocolombiano(a) o afrodescendiente',
    '6': 'Otras etnias',
    '99': 'Ninguna de las anteriores',
}

DISCAPACIDADES = {
    '01': 'Discapacidad física',
    '02': 'Discapacidad visual',
    '03': 'Discapacidad auditiva',
    '04': 'Discapacidad intelectual',
    '05': 'Discapacidad sicosocial',
    '06': 'Sordoceguera',
    '07': 'Discapacidad múltiple',
    '08': 'Sin discapacidad',
}

ZONAS_RESIDENCIA = {
    '01': 'Urbana',
    '02': 'Rural',
}

# La aplicacion guarda la zona como U/R desde antes de esta integracion.
ZONA_A_CODIGO = {'U': '01', 'R': '02'}


# --- Secciones del RDA de consulta ------------------------------------------
# Orden, titulo literal y codigo LOINC tal como los fija el perfil
# CompositionAmbulatoryRDA. Los titulos son valores fijos: cambiarlos hace que
# el servidor rechace el documento.
#
# `obligatoria` refleja la cardinalidad del slice. Las 1..1 deben viajar
# siempre, con entradas o con emptyReason.
SECCIONES_CONSULTA = [
    {
        'nombre': 'sectionPayers',
        'titulo': 'Entidad(es) responsable(s) por el plan de beneficios en salud (consulta)',
        'codigo': '48768-6',
        'display': 'Payment sources Document',
        'obligatoria': True,
    },
    {
        'nombre': 'sectionHistoryOfOccupation',
        'titulo': 'Otros datos demográficos',
        'codigo': '74208-0',
        'display': 'Demographic information + History of occupation Document',
        'obligatoria': True,
    },
    {
        'nombre': 'sectionAttendanceAllowance',
        'titulo': 'Datos incapacidad (SIPE – Sistema de Incapacidades y Prestaciones Economicas)',
        'codigo': '105583-9',
        'display': 'Worker Sick leave form',
        'obligatoria': True,
    },
    {
        'nombre': 'sectionMedications',
        'titulo': 'Historial de medicamentos',
        'codigo': '10160-0',
        'display': 'History of Medication use Narrative',
        'obligatoria': True,
    },
    {
        'nombre': 'sectionAllergies',
        'titulo': 'Historial de alergias, intolerancias y reacciones adversas',
        'codigo': '48765-2',
        'display': 'Allergies and adverse reactions Document',
        'obligatoria': True,
    },
    {
        'nombre': 'sectionProblems',
        'titulo': 'Historial de diagnósticos de problemas de salud',
        'codigo': '11450-4',
        'display': 'Problem list - Reported',
        'obligatoria': True,
        # Unica seccion cuyo entry es 1..*: un RDA sin diagnostico se rechaza.
        'exige_entrada': True,
    },
    {
        'nombre': 'sectionRiskFactors',
        'titulo': 'Factores de riesgo',
        'codigo': '75492-9',
        'display': 'Risk assessment and screening note',
        'obligatoria': True,
    },
    {
        'nombre': 'sectionServiceRequests',
        'titulo': 'Órdenes, prescripciones o solicitudes de servicio',
        'codigo': '61146-1',
        'display': 'Orders for services Document',
        'obligatoria': True,
    },
]

# Texto y codigo con que viaja una seccion sin entradas. El manual v1.4 fija
# ambos; no es texto libre.
RAZON_VACIA_CODIGO = 'nilknown'
RAZON_VACIA_DISPLAY = 'Nil Known'
RAZON_VACIA_TEXTO = (
    'No existen elementos conocidos para esta lista y/o el paciente no declara '
    'información'
)

SEVERIDAD_ALERGIA = {
    'leve': 'mild',
    'moderada': 'moderate',
    'grave': 'severe',
    'anafilaxia': 'severe',
}

# AllergyIntolerance.verificationStatus
ESTADO_ALERGIA = {
    'confirmada': 'confirmed',
    'reportada': 'unconfirmed',
    'descartada': 'refuted',
}

# Condition.clinicalStatus para las condiciones cronicas
ESTADO_CONDICION = {
    'activa': 'active',
    'resuelta': 'resolved',
    'inactiva': 'inactive',
}


def tipo_documento_valido(codigo):
    return (codigo or '').strip().upper() in TIPOS_DOCUMENTO


def referencia_paciente(tipo_documento, numero):
    """Identificador persistido del paciente: patron TipoIdent-NumIdent.

    Manual v1.4, seccion 5.3, literal a.
    """
    return '%s-%s' % ((tipo_documento or 'CC').strip().upper(), (numero or '').strip())


def referencia_profesional(tipo_documento, numero):
    """Mismo patron que el paciente. Manual v1.4, seccion 5.3, literal b."""
    return referencia_paciente(tipo_documento, numero)


def referencia_ips(codigo_habilitacion):
    """La IPS se referencia por su codigo de habilitacion en REPS.

    Manual v1.4, seccion 5.3, literal c.
    """
    return (codigo_habilitacion or '').strip()


def referencia_sede(codigo_habilitacion, numero_sede='00'):
    """Sede: CodigoSedePrestador-NumeroSede. Manual v1.4, seccion 5.3, literal d."""
    return '%s-%s' % ((codigo_habilitacion or '').strip(), (numero_sede or '00').strip())
