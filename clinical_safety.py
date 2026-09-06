"""Motor de verificacion de seguridad clinica en la prescripcion.

Que hace
--------
Antes de que una orden medica se firme, este modulo la contrasta con:

  1. Las alergias registradas del paciente, incluida la reactividad cruzada
     entre familias farmacologicas.
  2. Interacciones entre los medicamentos de la propia orden y con el
     tratamiento activo del paciente.
  3. Duplicidad terapeutica (dos principios activos de la misma clase).
  4. Contraindicaciones en embarazo.
  5. Cantidades fuera de rango y medicamentos de control especial.

Alcance y limites
-----------------
Esto es **soporte a la decision**, no un sustituto del criterio del profesional.
La base de conocimiento cubre las alertas de mayor consecuencia en atencion
primaria (anafilaxia, hemorragia, sindrome serotoninergico, arritmia por QT
prolongado, rabdomiolisis, toxicidad renal), no la totalidad de la farmacologia.

Un resultado sin hallazgos significa "no se detecto ninguno de los patrones
conocidos por este motor", nunca "la prescripcion es segura".

Por eso el diseno es explicito en dos puntos:

  - Las alertas de nivel ``BLOCK`` detienen la firma. El profesional puede
    continuar, pero solo declarando por escrito la justificacion clinica, que
    queda en la orden y en el registro de auditoria. La decision sigue siendo
    suya; lo que el sistema garantiza es que quede constancia de que fue
    consciente y deliberada.
  - ``KNOWLEDGE_BASE_VERSION`` y ``KNOWLEDGE_BASE_SOURCE`` acompanan cada
    evaluacion, para que una alerta emitida hoy pueda auditarse manana sabiendo
    con que version del conocimiento se genero.

Mantenimiento
-------------
Las tablas de este modulo deben revisarse contra el listado vigente del INVIMA
y las fichas tecnicas de los productos autorizados en Colombia. El comando
``python manage.py check-knowledge-base`` informa la antiguedad de la revision.
"""

import re
import unicodedata
from datetime import date

# Version de la base de conocimiento. Incrementar en cada revision clinica y
# registrar la fecha, para que las alertas historicas sean auditables.
KNOWLEDGE_BASE_VERSION = '2026.09.1'
KNOWLEDGE_BASE_REVIEWED = date(2026, 9, 5)
KNOWLEDGE_BASE_SOURCE = (
    'Revision inicial de atencion primaria. Requiere validacion por quimico '
    'farmaceutico contra el listado vigente del INVIMA antes del uso asistencial.'
)
# Meses tras los cuales la base se considera desactualizada.
KNOWLEDGE_BASE_MAX_AGE_MONTHS = 6


# --- Severidad ---------------------------------------------------------------

BLOCK = 'block'      # exige justificacion escrita para continuar
WARN = 'warn'        # se muestra y se registra; no detiene
INFO = 'info'        # informativo

SEVERITY_ORDER = {BLOCK: 0, WARN: 1, INFO: 2}

SEVERITY_LABEL = {
    BLOCK: 'Bloqueante',
    WARN: 'Advertencia',
    INFO: 'Informativo',
}


class SafetyFinding:
    """Un hallazgo unico de la verificacion."""

    __slots__ = ('severity', 'category', 'title', 'detail', 'medications', 'recommendation')

    def __init__(self, severity, category, title, detail, medications=None, recommendation=None):
        self.severity = severity
        self.category = category
        self.title = title
        self.detail = detail
        self.medications = list(medications or [])
        self.recommendation = recommendation

    @property
    def is_blocking(self):
        return self.severity == BLOCK

    @property
    def severity_label(self):
        return SEVERITY_LABEL.get(self.severity, self.severity)

    def to_dict(self):
        return {
            'severity': self.severity,
            'severity_label': self.severity_label,
            'category': self.category,
            'title': self.title,
            'detail': self.detail,
            'medications': self.medications,
            'recommendation': self.recommendation,
        }

    def __repr__(self):
        return f'<SafetyFinding {self.severity} {self.category}: {self.title}>'


class SafetyReport:
    """Resultado completo de la verificacion de una orden."""

    def __init__(self, findings=None):
        self.findings = sorted(
            findings or [],
            key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.category, f.title),
        )
        self.knowledge_base_version = KNOWLEDGE_BASE_VERSION

    @property
    def blocking(self):
        return [f for f in self.findings if f.is_blocking]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == WARN]

    @property
    def requires_override(self):
        return bool(self.blocking)

    @property
    def is_clear(self):
        return not self.findings

    def to_dict(self):
        return {
            'knowledge_base_version': self.knowledge_base_version,
            'requires_override': self.requires_override,
            'findings': [f.to_dict() for f in self.findings],
        }

    def summary_line(self):
        if self.is_clear:
            return 'Sin hallazgos de seguridad clinica.'
        parts = []
        if self.blocking:
            parts.append(f'{len(self.blocking)} bloqueante(s)')
        if self.warnings:
            parts.append(f'{len(self.warnings)} advertencia(s)')
        info = [f for f in self.findings if f.severity == INFO]
        if info:
            parts.append(f'{len(info)} informativo(s)')
        return ', '.join(parts)

    def __len__(self):
        return len(self.findings)


# --- Normalizacion de nombres ------------------------------------------------

# Sinonimos y marcas comerciales frecuentes en Colombia, mapeados al principio
# activo. La lista es deliberadamente conservadora: ante la duda se prefiere no
# normalizar (y perder una alerta que el profesional vera igual) antes que
# normalizar mal (y emitir una alerta sobre un farmaco que no se prescribio).
SYNONYMS = {
    'acetaminofen': 'paracetamol',
    'acetaminofeno': 'paracetamol',
    'tylenol': 'paracetamol',
    'dolex': 'paracetamol',
    'asa': 'acido acetilsalicilico',
    'aspirina': 'acido acetilsalicilico',
    'aas': 'acido acetilsalicilico',
    'amoxicilina acido clavulanico': 'amoxicilina',
    'amoxiclav': 'amoxicilina',
    'penicilina g': 'penicilina',
    'penicilina benzatinica': 'penicilina',
    'bencilpenicilina': 'penicilina',
    'trimetoprim sulfametoxazol': 'sulfametoxazol',
    'cotrimoxazol': 'sulfametoxazol',
    'tmp smx': 'sulfametoxazol',
    'diclofenaco sodico': 'diclofenaco',
    'diclofenaco potasico': 'diclofenaco',
    'ibuprofeno': 'ibuprofeno',
    'advil': 'ibuprofeno',
    'omeprazol sodico': 'omeprazol',
    'losartan potasico': 'losartan',
    'metformina clorhidrato': 'metformina',
    'insulina nph': 'insulina',
    'insulina cristalina': 'insulina',
    'insulina glargina': 'insulina',
    'salbutamol sulfato': 'salbutamol',
    'albuterol': 'salbutamol',
    'acetilcisteina': 'n acetilcisteina',
}


def normalize_drug(name):
    """Reduce un nombre de medicamento a una forma comparable.

    Quita tildes, dosis, formas farmaceuticas y puntuacion, y resuelve sinonimos.
    'Amoxicilina 500 mg cap.' y 'AMOXICILINA' llegan al mismo valor.
    """
    if not name:
        return ''

    text = unicodedata.normalize('NFKD', str(name))
    text = ''.join(ch for ch in text if not unicodedata.combining(ch)).lower()

    # Dosis y concentraciones: "500 mg", "5mg/ml", "10 ui", "2%".
    text = re.sub(r'\d+([.,]\d+)?\s*(mg|g|mcg|ug|ml|l|ui|u|%|mg/ml|mg/5ml|meq)\b', ' ', text)
    text = re.sub(r'\d+\s*/\s*\d+', ' ', text)

    # Formas farmaceuticas y presentaciones.
    text = re.sub(
        r'\b(tab|tabs|tableta|tabletas|cap|caps|capsula|capsulas|comprimido|comprimidos|'
        r'amp|ampolla|ampollas|jarabe|suspension|solucion|inyectable|iny|crema|unguento|'
        r'gotas|spray|inhalador|supositorio|parche|sobre|sobres|frasco|vial|'
        r'oral|via|im|iv|sc|vo|topico|recubierta|recubiertas|liberacion|prolongada)\b',
        ' ',
        text,
    )

    text = re.sub(r'[^a-z\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    return SYNONYMS.get(text, text)


# --- Familias farmacologicas -------------------------------------------------

# principio activo -> clase. La clase se usa para duplicidad terapeutica y para
# reactividad cruzada en alergias.
DRUG_CLASSES = {
    # Betalactamicos - penicilinas
    'penicilina': 'penicilinas',
    'amoxicilina': 'penicilinas',
    'ampicilina': 'penicilinas',
    'dicloxacilina': 'penicilinas',
    'oxacilina': 'penicilinas',
    'piperacilina': 'penicilinas',
    # Betalactamicos - cefalosporinas
    'cefalexina': 'cefalosporinas',
    'cefradina': 'cefalosporinas',
    'cefazolina': 'cefalosporinas',
    'cefuroxima': 'cefalosporinas',
    'ceftriaxona': 'cefalosporinas',
    'cefotaxima': 'cefalosporinas',
    'ceftazidima': 'cefalosporinas',
    'cefepime': 'cefalosporinas',
    # Betalactamicos - carbapenemicos
    'meropenem': 'carbapenemicos',
    'imipenem': 'carbapenemicos',
    'ertapenem': 'carbapenemicos',
    # Sulfas
    'sulfametoxazol': 'sulfonamidas',
    'sulfadiazina': 'sulfonamidas',
    'sulfasalazina': 'sulfonamidas',
    # Macrolidos
    'azitromicina': 'macrolidos',
    'claritromicina': 'macrolidos',
    'eritromicina': 'macrolidos',
    # Quinolonas
    'ciprofloxacino': 'quinolonas',
    'levofloxacino': 'quinolonas',
    'moxifloxacino': 'quinolonas',
    'norfloxacino': 'quinolonas',
    # Aminoglucosidos
    'gentamicina': 'aminoglucosidos',
    'amikacina': 'aminoglucosidos',
    'estreptomicina': 'aminoglucosidos',
    # Otros antibioticos
    'clindamicina': 'lincosamidas',
    'metronidazol': 'nitroimidazoles',
    'vancomicina': 'glucopeptidos',
    'doxiciclina': 'tetraciclinas',
    'tetraciclina': 'tetraciclinas',
    'nitrofurantoina': 'nitrofuranos',
    # AINE
    'ibuprofeno': 'aine',
    'naproxeno': 'aine',
    'diclofenaco': 'aine',
    'ketoprofeno': 'aine',
    'ketorolaco': 'aine',
    'meloxicam': 'aine',
    'piroxicam': 'aine',
    'celecoxib': 'aine',
    'indometacina': 'aine',
    'acido acetilsalicilico': 'aine',
    # Analgesicos no AINE
    'paracetamol': 'analgesicos_antipireticos',
    'dipirona': 'analgesicos_antipireticos',
    'metamizol': 'analgesicos_antipireticos',
    # Opioides
    'morfina': 'opioides',
    'tramadol': 'opioides',
    'codeina': 'opioides',
    'hidrocodona': 'opioides',
    'oxicodona': 'opioides',
    'fentanilo': 'opioides',
    'meperidina': 'opioides',
    'metadona': 'opioides',
    # Benzodiacepinas
    'diazepam': 'benzodiacepinas',
    'lorazepam': 'benzodiacepinas',
    'alprazolam': 'benzodiacepinas',
    'clonazepam': 'benzodiacepinas',
    'midazolam': 'benzodiacepinas',
    # Anticoagulantes y antiagregantes
    'warfarina': 'anticoagulantes',
    'enoxaparina': 'anticoagulantes',
    'heparina': 'anticoagulantes',
    'rivaroxaban': 'anticoagulantes',
    'apixaban': 'anticoagulantes',
    'dabigatran': 'anticoagulantes',
    'clopidogrel': 'antiagregantes',
    # Cardiovascular
    'enalapril': 'ieca',
    'captopril': 'ieca',
    'lisinopril': 'ieca',
    'losartan': 'ara2',
    'valsartan': 'ara2',
    'irbesartan': 'ara2',
    'amlodipino': 'calcioantagonistas',
    'nifedipino': 'calcioantagonistas',
    'verapamilo': 'calcioantagonistas',
    'diltiazem': 'calcioantagonistas',
    'metoprolol': 'betabloqueadores',
    'atenolol': 'betabloqueadores',
    'propranolol': 'betabloqueadores',
    'carvedilol': 'betabloqueadores',
    'furosemida': 'diureticos_asa',
    'hidroclorotiazida': 'diureticos_tiazidicos',
    'espironolactona': 'diureticos_ahorradores_potasio',
    'digoxina': 'digitalicos',
    'amiodarona': 'antiarritmicos',
    # Estatinas
    'atorvastatina': 'estatinas',
    'simvastatina': 'estatinas',
    'lovastatina': 'estatinas',
    'rosuvastatina': 'estatinas',
    'pravastatina': 'estatinas',
    # Antidiabeticos
    'metformina': 'biguanidas',
    'glibenclamida': 'sulfonilureas',
    'glimepirida': 'sulfonilureas',
    'insulina': 'insulinas',
    # Psiquiatria
    'fluoxetina': 'isrs',
    'sertralina': 'isrs',
    'paroxetina': 'isrs',
    'escitalopram': 'isrs',
    'citalopram': 'isrs',
    'amitriptilina': 'antidepresivos_triciclicos',
    'imipramina': 'antidepresivos_triciclicos',
    'venlafaxina': 'isrn',
    'duloxetina': 'isrn',
    'haloperidol': 'antipsicoticos',
    'risperidona': 'antipsicoticos',
    'quetiapina': 'antipsicoticos',
    'olanzapina': 'antipsicoticos',
    'litio': 'estabilizadores_animo',
    # Anticonvulsivantes
    'fenitoina': 'anticonvulsivantes',
    'carbamazepina': 'anticonvulsivantes',
    'acido valproico': 'anticonvulsivantes',
    'levetiracetam': 'anticonvulsivantes',
    'fenobarbital': 'barbituricos',
    # Gastro
    'omeprazol': 'inhibidores_bomba_protones',
    'esomeprazol': 'inhibidores_bomba_protones',
    'pantoprazol': 'inhibidores_bomba_protones',
    'ranitidina': 'antih2',
    'famotidina': 'antih2',
    # Respiratorio
    'salbutamol': 'beta2_agonistas',
    'salmeterol': 'beta2_agonistas',
    'formoterol': 'beta2_agonistas',
    'budesonida': 'corticoides_inhalados',
    'beclometasona': 'corticoides_inhalados',
    'ipratropio': 'anticolinergicos_inhalados',
    # Corticoides sistemicos
    'prednisona': 'corticoides_sistemicos',
    'prednisolona': 'corticoides_sistemicos',
    'dexametasona': 'corticoides_sistemicos',
    'hidrocortisona': 'corticoides_sistemicos',
    'metilprednisolona': 'corticoides_sistemicos',
    # Antihistaminicos
    'loratadina': 'antihistaminicos',
    'cetirizina': 'antihistaminicos',
    'clorfeniramina': 'antihistaminicos',
    'difenhidramina': 'antihistaminicos',
    # Tiroides
    'levotiroxina': 'hormonas_tiroideas',
    # Antiparasitarios
    'albendazol': 'antiparasitarios',
    'mebendazol': 'antiparasitarios',
    'ivermectina': 'antiparasitarios',
    'praziquantel': 'antiparasitarios',
    # Antimalaricos - relevantes en zona rural endemica
    'cloroquina': 'antimalaricos',
    'primaquina': 'antimalaricos',
    'artemeter': 'antimalaricos',
    'lumefantrina': 'antimalaricos',
}

CLASS_LABELS = {
    'penicilinas': 'penicilinas',
    'cefalosporinas': 'cefalosporinas',
    'carbapenemicos': 'carbapenemicos',
    'sulfonamidas': 'sulfas',
    'macrolidos': 'macrolidos',
    'quinolonas': 'quinolonas',
    'aine': 'antiinflamatorios no esteroideos (AINE)',
    'opioides': 'opioides',
    'benzodiacepinas': 'benzodiacepinas',
    'anticoagulantes': 'anticoagulantes',
    'ieca': 'inhibidores de la ECA',
    'ara2': 'antagonistas del receptor de angiotensina II',
    'isrs': 'inhibidores selectivos de recaptacion de serotonina',
    'estatinas': 'estatinas',
    'corticoides_sistemicos': 'corticoides sistemicos',
}


def drug_class(name):
    return DRUG_CLASSES.get(normalize_drug(name))


def class_label(class_key):
    return CLASS_LABELS.get(class_key, (class_key or '').replace('_', ' '))


# --- Reactividad cruzada en alergias -----------------------------------------

# Alergia declarada -> clases que tambien deben alertar, con la severidad y el
# motivo. La reactividad cruzada betalactamica es el caso clasico: una alergia a
# penicilina no siempre implica alergia a cefalosporinas, pero la probabilidad es
# suficiente para exigir decision consciente del profesional.
CROSS_REACTIVITY = {
    'penicilinas': [
        ('cefalosporinas', BLOCK,
         'Reactividad cruzada betalactamica descrita entre penicilinas y cefalosporinas, '
         'mayor con cefalosporinas de primera generacion.'),
        ('carbapenemicos', WARN,
         'Reactividad cruzada betalactamica de baja frecuencia con carbapenemicos.'),
    ],
    'cefalosporinas': [
        ('penicilinas', BLOCK,
         'Reactividad cruzada betalactamica descrita entre cefalosporinas y penicilinas.'),
        ('carbapenemicos', WARN,
         'Reactividad cruzada betalactamica de baja frecuencia con carbapenemicos.'),
    ],
    'sulfonamidas': [
        ('sulfonamidas', BLOCK, 'Misma familia de sulfonamidas.'),
    ],
    'aine': [
        ('aine', BLOCK,
         'Reactividad cruzada entre AINE por inhibicion compartida de la ciclooxigenasa. '
         'Riesgo de broncoespasmo y de reaccion anafilactoide.'),
    ],
    'quinolonas': [
        ('quinolonas', BLOCK, 'Misma familia de quinolonas.'),
    ],
    'macrolidos': [
        ('macrolidos', BLOCK, 'Misma familia de macrolidos.'),
    ],
    'opioides': [
        ('opioides', WARN,
         'Reactividad cruzada variable entre opioides; distinguir alergia verdadera '
         'de efecto adverso previsible (nausea, prurito por liberacion de histamina).'),
    ],
}


# --- Interacciones -----------------------------------------------------------

def _pair(a, b):
    return tuple(sorted((a, b)))


# (principio_a, principio_b) -> (severidad, mecanismo, conducta)
# Seleccion centrada en interacciones con desenlace grave y de aparicion realista
# en atencion primaria.
INTERACTIONS = {
    _pair('warfarina', 'acido acetilsalicilico'): (
        BLOCK,
        'Suma de efecto anticoagulante y antiagregante, con desplazamiento de la union '
        'a proteinas plasmaticas.',
        'Riesgo de hemorragia mayor. Si la combinacion es necesaria, definir indicacion, '
        'ajustar dosis y controlar INR de forma estrecha.',
    ),
    _pair('warfarina', 'ibuprofeno'): (
        BLOCK,
        'Los AINE inhiben la agregacion plaquetaria y lesionan la mucosa gastrica.',
        'Riesgo de hemorragia digestiva. Preferir paracetamol como analgesico.',
    ),
    _pair('warfarina', 'diclofenaco'): (
        BLOCK,
        'Los AINE inhiben la agregacion plaquetaria y lesionan la mucosa gastrica.',
        'Riesgo de hemorragia digestiva. Preferir paracetamol como analgesico.',
    ),
    _pair('warfarina', 'naproxeno'): (
        BLOCK,
        'Los AINE inhiben la agregacion plaquetaria y lesionan la mucosa gastrica.',
        'Riesgo de hemorragia digestiva. Preferir paracetamol como analgesico.',
    ),
    _pair('warfarina', 'sulfametoxazol'): (
        BLOCK,
        'Inhibicion del CYP2C9 con aumento marcado del efecto de la warfarina.',
        'Elevacion importante del INR. Si no hay alternativa, controlar INR a las 72 horas.',
    ),
    _pair('warfarina', 'metronidazol'): (
        BLOCK,
        'Inhibicion del metabolismo de la warfarina.',
        'Elevacion del INR con riesgo de sangrado. Controlar INR.',
    ),
    _pair('warfarina', 'fluconazol'): (
        WARN,
        'Inhibicion del CYP2C9.',
        'Vigilar INR durante el tratamiento antifungico.',
    ),
    _pair('clopidogrel', 'omeprazol'): (
        WARN,
        'El omeprazol inhibe el CYP2C19 y reduce la activacion del clopidogrel.',
        'Perdida de eficacia antiagregante. Preferir pantoprazol.',
    ),
    _pair('fluoxetina', 'tramadol'): (
        BLOCK,
        'Suma de actividad serotoninergica.',
        'Riesgo de sindrome serotoninergico y descenso del umbral convulsivo.',
    ),
    _pair('sertralina', 'tramadol'): (
        BLOCK,
        'Suma de actividad serotoninergica.',
        'Riesgo de sindrome serotoninergico y descenso del umbral convulsivo.',
    ),
    _pair('paroxetina', 'tramadol'): (
        BLOCK,
        'Suma de actividad serotoninergica.',
        'Riesgo de sindrome serotoninergico y descenso del umbral convulsivo.',
    ),
    _pair('fluoxetina', 'amitriptilina'): (
        WARN,
        'Inhibicion del CYP2D6 con aumento de la concentracion del triciclico.',
        'Vigilar toxicidad anticolinergica y prolongacion del QT.',
    ),
    _pair('claritromicina', 'simvastatina'): (
        BLOCK,
        'Inhibicion potente del CYP3A4 con acumulacion de la estatina.',
        'Riesgo de rabdomiolisis. Suspender la estatina durante el tratamiento antibiotico.',
    ),
    _pair('claritromicina', 'atorvastatina'): (
        WARN,
        'Inhibicion del CYP3A4 con aumento de la concentracion de la estatina.',
        'Vigilar mialgias; considerar reducir dosis de la estatina.',
    ),
    _pair('amiodarona', 'simvastatina'): (
        BLOCK,
        'Inhibicion del metabolismo de la estatina.',
        'Riesgo de miopatia y rabdomiolisis. Limitar simvastatina a 20 mg/dia.',
    ),
    _pair('amiodarona', 'digoxina'): (
        BLOCK,
        'La amiodarona reduce el aclaramiento de la digoxina.',
        'Riesgo de intoxicacion digitalica. Reducir la digoxina a la mitad y medir niveles.',
    ),
    _pair('amiodarona', 'levofloxacino'): (
        BLOCK,
        'Prolongacion aditiva del intervalo QT.',
        'Riesgo de torsade de pointes. Evitar la combinacion.',
    ),
    _pair('amiodarona', 'moxifloxacino'): (
        BLOCK,
        'Prolongacion aditiva del intervalo QT.',
        'Riesgo de torsade de pointes. Evitar la combinacion.',
    ),
    _pair('haloperidol', 'levofloxacino'): (
        WARN,
        'Prolongacion aditiva del intervalo QT.',
        'Valorar electrocardiograma antes de iniciar.',
    ),
    _pair('digoxina', 'furosemida'): (
        WARN,
        'La hipopotasemia inducida por el diuretico aumenta la toxicidad digitalica.',
        'Controlar potasio y magnesio.',
    ),
    _pair('enalapril', 'espironolactona'): (
        BLOCK,
        'Retencion aditiva de potasio.',
        'Riesgo de hiperpotasemia grave y arritmia. Controlar potasio y creatinina.',
    ),
    _pair('losartan', 'espironolactona'): (
        BLOCK,
        'Retencion aditiva de potasio.',
        'Riesgo de hiperpotasemia grave. Controlar potasio y creatinina.',
    ),
    _pair('enalapril', 'losartan'): (
        BLOCK,
        'Doble bloqueo del sistema renina-angiotensina.',
        'Aumenta hiperpotasemia, hipotension y dano renal sin beneficio demostrado.',
    ),
    _pair('enalapril', 'ibuprofeno'): (
        WARN,
        'Los AINE reducen la sintesis renal de prostaglandinas.',
        'Perdida de control tensional y riesgo de lesion renal aguda, mayor si hay diuretico.',
    ),
    _pair('metformina', 'furosemida'): (
        WARN,
        'La deplecion de volumen puede deteriorar la funcion renal.',
        'Riesgo de acidosis lactica si cae el filtrado glomerular. Controlar creatinina.',
    ),
    _pair('litio', 'ibuprofeno'): (
        BLOCK,
        'Los AINE reducen la excrecion renal de litio.',
        'Riesgo de intoxicacion por litio. Medir litemia.',
    ),
    _pair('litio', 'hidroclorotiazida'): (
        BLOCK,
        'Las tiazidas aumentan la reabsorcion tubular de litio.',
        'Riesgo de intoxicacion por litio. Medir litemia.',
    ),
    _pair('litio', 'enalapril'): (
        WARN,
        'Reduccion de la excrecion renal de litio.',
        'Medir litemia tras iniciar.',
    ),
    _pair('fenitoina', 'carbamazepina'): (
        WARN,
        'Induccion enzimatica reciproca.',
        'Ambas concentraciones pueden caer. Medir niveles.',
    ),
    _pair('ciprofloxacino', 'tizanidina'): (
        BLOCK,
        'Inhibicion potente del CYP1A2.',
        'Hipotension y sedacion graves. Combinacion contraindicada.',
    ),
    _pair('metronidazol', 'alcohol'): (
        WARN,
        'Inhibicion de la aldehido deshidrogenasa.',
        'Reaccion tipo disulfiram. Advertir al paciente que evite alcohol durante '
        'el tratamiento y 48 horas despues.',
    ),
    _pair('levotiroxina', 'omeprazol'): (
        INFO,
        'El aumento del pH gastrico reduce la absorcion de levotiroxina.',
        'Separar las tomas al menos 4 horas.',
    ),
    _pair('levotiroxina', 'carbonato de calcio'): (
        INFO,
        'Quelacion en la luz intestinal.',
        'Separar las tomas al menos 4 horas.',
    ),
    _pair('ciprofloxacino', 'carbonato de calcio'): (
        WARN,
        'Quelacion con cationes divalentes.',
        'Perdida de eficacia del antibiotico. Separar las tomas 2 horas antes o 6 despues.',
    ),
    _pair('doxiciclina', 'carbonato de calcio'): (
        WARN,
        'Quelacion con cationes divalentes.',
        'Perdida de eficacia. Separar las tomas.',
    ),
    _pair('espironolactona', 'ibuprofeno'): (
        WARN,
        'Los AINE reducen la excrecion de potasio.',
        'Riesgo de hiperpotasemia. Controlar potasio.',
    ),
    _pair('prednisona', 'ibuprofeno'): (
        WARN,
        'Lesion aditiva de la mucosa gastrica.',
        'Riesgo de ulcera y hemorragia digestiva. Valorar gastroproteccion.',
    ),
    _pair('tramadol', 'amitriptilina'): (
        WARN,
        'Suma serotoninergica y descenso del umbral convulsivo.',
        'Vigilar sintomas de sindrome serotoninergico.',
    ),
    _pair('diazepam', 'morfina'): (
        BLOCK,
        'Depresion respiratoria aditiva del sistema nervioso central.',
        'Riesgo de parada respiratoria. Evitar la combinacion en el ambito ambulatorio.',
    ),
    _pair('alprazolam', 'tramadol'): (
        WARN,
        'Depresion aditiva del sistema nervioso central.',
        'Advertir sobre sedacion y no conducir.',
    ),
}


# --- Interacciones por clase -------------------------------------------------

# Cuando la alerta aplica a toda la familia y no a un principio activo concreto.
CLASS_INTERACTIONS = {
    _pair('anticoagulantes', 'aine'): (
        BLOCK,
        'Los AINE inhiben la agregacion plaquetaria y lesionan la mucosa gastrica.',
        'Riesgo de hemorragia mayor sobre anticoagulacion. Preferir paracetamol.',
    ),
    _pair('anticoagulantes', 'antiagregantes'): (
        WARN,
        'Efecto antitrombotico aditivo.',
        'Solo con indicacion explicita y por el tiempo minimo necesario.',
    ),
    _pair('ieca', 'ara2'): (
        BLOCK,
        'Doble bloqueo del sistema renina-angiotensina.',
        'Aumenta hiperpotasemia y dano renal sin beneficio demostrado.',
    ),
    _pair('benzodiacepinas', 'opioides'): (
        BLOCK,
        'Depresion respiratoria aditiva.',
        'Combinacion asociada a mortalidad por sobredosis. Evitar en ambulatorio.',
    ),
    _pair('isrs', 'isrn'): (
        BLOCK,
        'Suma de actividad serotoninergica.',
        'Riesgo de sindrome serotoninergico.',
    ),
}


# --- Embarazo ----------------------------------------------------------------

PREGNANCY_CONTRAINDICATED = {
    'warfarina': 'Embriopatia por warfarina; teratogeno en el primer trimestre.',
    'enalapril': 'Los IECA producen dano renal fetal y oligohidramnios en 2.o y 3.er trimestre.',
    'captopril': 'Los IECA producen dano renal fetal y oligohidramnios en 2.o y 3.er trimestre.',
    'lisinopril': 'Los IECA producen dano renal fetal y oligohidramnios en 2.o y 3.er trimestre.',
    'losartan': 'Los ARA II producen dano renal fetal y oligohidramnios.',
    'valsartan': 'Los ARA II producen dano renal fetal y oligohidramnios.',
    'irbesartan': 'Los ARA II producen dano renal fetal y oligohidramnios.',
    'atorvastatina': 'Las estatinas estan contraindicadas: el colesterol es esencial para el desarrollo fetal.',
    'simvastatina': 'Las estatinas estan contraindicadas en el embarazo.',
    'lovastatina': 'Las estatinas estan contraindicadas en el embarazo.',
    'rosuvastatina': 'Las estatinas estan contraindicadas en el embarazo.',
    'doxiciclina': 'Las tetraciclinas afectan el desarrollo oseo y dental fetal.',
    'tetraciclina': 'Las tetraciclinas afectan el desarrollo oseo y dental fetal.',
    'acido valproico': 'Riesgo elevado de defectos del tubo neural y deficit cognitivo.',
    'carbamazepina': 'Riesgo de defectos del tubo neural.',
    'fenitoina': 'Sindrome de hidantoina fetal.',
    'metotrexato': 'Abortivo y teratogeno.',
    'isotretinoina': 'Teratogeno mayor. Contraindicacion absoluta.',
    'misoprostol': 'Induce contracciones uterinas y aborto.',
    'ribavirina': 'Teratogeno.',
    'finasterida': 'Riesgo de anomalias genitales en feto masculino.',
}

PREGNANCY_CAUTION = {
    'ibuprofeno': 'Los AINE se evitan despues de la semana 20 por oligohidramnios y '
                  'cierre prematuro del ductus arterioso.',
    'naproxeno': 'Los AINE se evitan despues de la semana 20 por oligohidramnios y '
                 'cierre prematuro del ductus arterioso.',
    'diclofenaco': 'Los AINE se evitan despues de la semana 20 por oligohidramnios y '
                   'cierre prematuro del ductus arterioso.',
    'acido acetilsalicilico': 'Salvo dosis baja indicada para prevencion de preeclampsia.',
    'ciprofloxacino': 'Las quinolonas se evitan por efecto sobre el cartilago en desarrollo.',
    'levofloxacino': 'Las quinolonas se evitan por efecto sobre el cartilago en desarrollo.',
    'fluconazol': 'Dosis altas y prolongadas se asocian a malformaciones.',
    'sulfametoxazol': 'Antagonismo del folato en el primer trimestre; riesgo de kernicterus al termino.',
    'litio': 'Anomalia de Ebstein; requiere valoracion especializada.',
    'metronidazol': 'Evitar en el primer trimestre si existe alternativa.',
}


# --- Medicamentos de control especial ---------------------------------------

# Colombia: monopolio del Estado sobre estupefacientes y psicotropicos
# (Resolucion 1478 de 2006 y normas concordantes). Requieren receta oficial
# numerada, no la prescripcion ordinaria.
CONTROLLED_SUBSTANCES = {
    'morfina': 'Estupefaciente',
    'metadona': 'Estupefaciente',
    'fentanilo': 'Estupefaciente',
    'oxicodona': 'Estupefaciente',
    'hidrocodona': 'Estupefaciente',
    'meperidina': 'Estupefaciente',
    'codeina': 'Estupefaciente',
    'tramadol': 'Sujeto a control especial',
    'diazepam': 'Psicotropico',
    'lorazepam': 'Psicotropico',
    'alprazolam': 'Psicotropico',
    'clonazepam': 'Psicotropico',
    'midazolam': 'Psicotropico',
    'fenobarbital': 'Psicotropico',
    'metilfenidato': 'Psicotropico',
}


# --- Limites de cantidad -----------------------------------------------------

# Tope por linea de prescripcion. No es una dosis maxima: es un filtro contra el
# error de digitacion (escribir 300 donde iban 30) y contra la desviacion de
# medicamento controlado.
MAX_UNITS_DEFAULT = 180
MAX_UNITS_CONTROLLED = 30
ABSURD_QUANTITY = 1000


# --- Motor -------------------------------------------------------------------

def _med_display(med):
    return (med.get('medicamento') or med.get('nombre_med') or med.get('name') or '').strip()


def check_allergies(medications, allergies):
    """Contrasta la orden con las alergias registradas del paciente."""
    findings = []
    if not allergies:
        return findings

    for med in medications:
        display = _med_display(med)
        norm = normalize_drug(display)
        if not norm:
            continue
        med_class = DRUG_CLASSES.get(norm)

        for allergy in allergies:
            allergen_raw = (getattr(allergy, 'substance', None) or str(allergy)).strip()
            allergen = normalize_drug(allergen_raw)
            if not allergen:
                continue
            reaction = getattr(allergy, 'reaction', None)
            severity_note = getattr(allergy, 'severity', None)

            # 1. Coincidencia exacta del principio activo.
            if allergen == norm:
                findings.append(SafetyFinding(
                    severity=BLOCK,
                    category='alergia',
                    title=f'Alergia registrada a {allergen_raw}',
                    detail=(
                        f'El paciente tiene registrada alergia a "{allergen_raw}" y la orden '
                        f'incluye "{display}", el mismo principio activo.'
                        + (f' Reaccion previa: {reaction}.' if reaction else '')
                        + (f' Severidad registrada: {severity_note}.' if severity_note else '')
                    ),
                    medications=[display],
                    recommendation='Seleccionar un principio activo de otra familia farmacologica.',
                ))
                continue

            allergen_class = DRUG_CLASSES.get(allergen)

            # 2. Misma familia farmacologica.
            if allergen_class and med_class and allergen_class == med_class:
                findings.append(SafetyFinding(
                    severity=BLOCK,
                    category='alergia',
                    title=f'{display} pertenece a la familia de {allergen_raw}',
                    detail=(
                        f'El paciente es alergico a "{allergen_raw}". "{display}" pertenece a la '
                        f'misma familia ({class_label(med_class)}), con riesgo de reaccion cruzada.'
                        + (f' Reaccion previa: {reaction}.' if reaction else '')
                    ),
                    medications=[display],
                    recommendation='Seleccionar un principio activo de otra familia farmacologica.',
                ))
                continue

            # 3. Reactividad cruzada entre familias distintas.
            for target_class, severity, mechanism in CROSS_REACTIVITY.get(allergen_class, []):
                if med_class == target_class and allergen_class != med_class:
                    findings.append(SafetyFinding(
                        severity=severity,
                        category='alergia_cruzada',
                        title=f'Posible reactividad cruzada: {allergen_raw} y {display}',
                        detail=(
                            f'El paciente es alergico a "{allergen_raw}" ({class_label(allergen_class)}). '
                            f'"{display}" es {class_label(med_class)}. {mechanism}'
                            + (f' Reaccion previa: {reaction}.' if reaction else '')
                        ),
                        medications=[display],
                        recommendation=(
                            'Verificar la naturaleza de la reaccion previa. Si fue anafilaxia, '
                            'angioedema o Stevens-Johnson, evitar toda la clase.'
                        ),
                    ))

    return findings


def check_interactions(medications, active_medications=None):
    """Busca interacciones dentro de la orden y con el tratamiento activo."""
    findings = []

    new_meds = [(_med_display(m), normalize_drug(_med_display(m))) for m in medications]
    new_meds = [(d, n) for d, n in new_meds if n]

    current = []
    for item in (active_medications or []):
        display = item if isinstance(item, str) else _med_display(item)
        norm = normalize_drug(display)
        if norm:
            current.append((display, norm))

    seen = set()

    def evaluate(a_display, a_norm, b_display, b_norm, context):
        key = _pair(a_norm, b_norm)
        if key in seen or a_norm == b_norm:
            return
        seen.add(key)

        entry = INTERACTIONS.get(key)
        if entry:
            severity, mechanism, action = entry
            findings.append(SafetyFinding(
                severity=severity,
                category='interaccion',
                title=f'Interaccion: {a_display} + {b_display}',
                detail=f'{context} {mechanism}',
                medications=[a_display, b_display],
                recommendation=action,
            ))
            return

        class_a = DRUG_CLASSES.get(a_norm)
        class_b = DRUG_CLASSES.get(b_norm)
        if class_a and class_b and class_a != class_b:
            class_entry = CLASS_INTERACTIONS.get(_pair(class_a, class_b))
            if class_entry:
                severity, mechanism, action = class_entry
                findings.append(SafetyFinding(
                    severity=severity,
                    category='interaccion',
                    title=f'Interaccion por clase: {a_display} + {b_display}',
                    detail=(
                        f'{context} {class_label(class_a)} junto a {class_label(class_b)}. {mechanism}'
                    ),
                    medications=[a_display, b_display],
                    recommendation=action,
                ))

    # Dentro de la orden.
    for i, (a_display, a_norm) in enumerate(new_meds):
        for b_display, b_norm in new_meds[i + 1:]:
            evaluate(a_display, a_norm, b_display, b_norm, 'Ambos en esta orden.')

    # Contra el tratamiento activo.
    for a_display, a_norm in new_meds:
        for b_display, b_norm in current:
            evaluate(a_display, a_norm, b_display, b_norm,
                     'El paciente ya recibe este medicamento.')

    return findings


def check_duplicate_therapy(medications, active_medications=None):
    """Detecta dos principios activos de la misma clase terapeutica."""
    findings = []
    by_class = {}

    for med in medications:
        display = _med_display(med)
        norm = normalize_drug(display)
        med_class = DRUG_CLASSES.get(norm)
        if med_class:
            by_class.setdefault(med_class, []).append((display, norm, 'orden'))

    for item in (active_medications or []):
        display = item if isinstance(item, str) else _med_display(item)
        norm = normalize_drug(display)
        med_class = DRUG_CLASSES.get(norm)
        if med_class:
            by_class.setdefault(med_class, []).append((display, norm, 'activo'))

    for med_class, entries in by_class.items():
        distinct = {norm: display for display, norm, _ in entries}
        if len(distinct) < 2:
            continue

        names = list(distinct.values())
        in_order = [d for d, _, source in entries if source == 'orden']
        # Duplicar analgesia de rescate es una pauta valida; duplicar
        # anticoagulacion o antihipertensivos del mismo grupo, no.
        severity = WARN if med_class in {'analgesicos_antipireticos', 'antihistaminicos'} else BLOCK

        findings.append(SafetyFinding(
            severity=severity,
            category='duplicidad',
            title=f'Duplicidad terapeutica: {class_label(med_class)}',
            detail=(
                f'La prescripcion combina {len(distinct)} principios activos de la clase '
                f'{class_label(med_class)}: {", ".join(names)}. La suma de efectos aumenta '
                'la toxicidad sin beneficio adicional esperado.'
            ),
            medications=in_order or names,
            recommendation='Dejar un solo representante de la clase, salvo indicacion documentada.',
        ))

    return findings


def check_pregnancy(medications, is_pregnant):
    """Contraindicaciones en embarazo."""
    findings = []
    if not is_pregnant:
        return findings

    for med in medications:
        display = _med_display(med)
        norm = normalize_drug(display)

        if norm in PREGNANCY_CONTRAINDICATED:
            findings.append(SafetyFinding(
                severity=BLOCK,
                category='embarazo',
                title=f'{display} esta contraindicado en el embarazo',
                detail=(
                    f'La paciente esta registrada como gestante. {PREGNANCY_CONTRAINDICATED[norm]}'
                ),
                medications=[display],
                recommendation='Seleccionar una alternativa con seguridad establecida en gestacion.',
            ))
        elif norm in PREGNANCY_CAUTION:
            findings.append(SafetyFinding(
                severity=WARN,
                category='embarazo',
                title=f'{display} requiere precaucion en el embarazo',
                detail=(
                    f'La paciente esta registrada como gestante. {PREGNANCY_CAUTION[norm]}'
                ),
                medications=[display],
                recommendation='Valorar relacion beneficio-riesgo y edad gestacional.',
            ))

    return findings


def check_quantities(medications):
    """Cantidades implausibles y medicamentos de control especial."""
    findings = []

    for med in medications:
        display = _med_display(med)
        norm = normalize_drug(display)
        try:
            quantity = int(med.get('cantidad') or 0)
        except (TypeError, ValueError):
            quantity = 0

        controlled = CONTROLLED_SUBSTANCES.get(norm)

        if controlled:
            findings.append(SafetyFinding(
                severity=WARN,
                category='control_especial',
                title=f'{display}: medicamento de control especial ({controlled})',
                detail=(
                    f'"{display}" esta sujeto al regimen de control especial. En Colombia su '
                    'prescripcion requiere receta oficial numerada del Fondo Rotatorio de '
                    'Estupefacientes, no la orden ordinaria del sistema.'
                ),
                medications=[display],
                recommendation=(
                    'Emitir ademas la receta oficial y registrar el numero en las observaciones '
                    'de la orden.'
                ),
            ))

        limit = MAX_UNITS_CONTROLLED if controlled else MAX_UNITS_DEFAULT

        if quantity >= ABSURD_QUANTITY:
            findings.append(SafetyFinding(
                severity=BLOCK,
                category='cantidad',
                title=f'Cantidad implausible: {quantity} unidades de {display}',
                detail=(
                    f'Se solicitan {quantity} unidades. Una cifra de esta magnitud suele indicar '
                    'un error de digitacion.'
                ),
                medications=[display],
                recommendation='Verificar la cantidad antes de firmar.',
            ))
        elif quantity > limit:
            findings.append(SafetyFinding(
                severity=WARN,
                category='cantidad',
                title=f'Cantidad elevada: {quantity} unidades de {display}',
                detail=(
                    f'Se solicitan {quantity} unidades, por encima del tope habitual de {limit} '
                    'para una orden de atencion primaria.'
                ),
                medications=[display],
                recommendation='Confirmar que corresponde a la duracion del tratamiento prevista.',
            ))
        elif quantity <= 0:
            findings.append(SafetyFinding(
                severity=BLOCK,
                category='cantidad',
                title=f'Cantidad no valida para {display}',
                detail='La cantidad debe ser un numero entero mayor que cero.',
                medications=[display],
                recommendation='Corregir la cantidad.',
            ))

    return findings


def check_pediatric(medications, patient_age_years):
    """Medicamentos desaconsejados en poblacion pediatrica."""
    findings = []
    if patient_age_years is None or patient_age_years >= 18:
        return findings

    pediatric_rules = {
        'acido acetilsalicilico': (
            BLOCK, 16,
            'Asociado a sindrome de Reye en menores con infeccion viral.',
        ),
        'doxiciclina': (
            WARN, 8,
            'Las tetraciclinas producen decoloracion dental permanente en menores de 8 anos.',
        ),
        'tetraciclina': (
            WARN, 8,
            'Las tetraciclinas producen decoloracion dental permanente en menores de 8 anos.',
        ),
        'ciprofloxacino': (
            WARN, 18,
            'Las quinolonas se reservan en pediatria por efecto sobre el cartilago de crecimiento.',
        ),
        'levofloxacino': (
            WARN, 18,
            'Las quinolonas se reservan en pediatria por efecto sobre el cartilago de crecimiento.',
        ),
        'codeina': (
            BLOCK, 12,
            'Contraindicada por depresion respiratoria en metabolizadores ultrarrapidos.',
        ),
        'tramadol': (
            BLOCK, 12,
            'Contraindicado por depresion respiratoria en metabolizadores ultrarrapidos.',
        ),
    }

    for med in medications:
        display = _med_display(med)
        norm = normalize_drug(display)
        rule = pediatric_rules.get(norm)
        if not rule:
            continue
        severity, age_limit, reason = rule
        if patient_age_years < age_limit:
            findings.append(SafetyFinding(
                severity=severity,
                category='pediatria',
                title=f'{display} en paciente de {patient_age_years} anos',
                detail=f'Limite de edad: {age_limit} anos. {reason}',
                medications=[display],
                recommendation='Seleccionar una alternativa apropiada para la edad.',
            ))

    return findings


def evaluate_prescription(
    medications,
    allergies=None,
    active_medications=None,
    is_pregnant=False,
    patient_age_years=None,
):
    """Ejecuta todas las verificaciones y devuelve un `SafetyReport`.

    Args:
        medications: lista de dicts de la orden, con al menos `nombre_med` y `cantidad`.
        allergies: objetos con atributos `substance`, `reaction`, `severity`, o cadenas.
        active_medications: tratamiento vigente del paciente (dicts o cadenas).
        is_pregnant: estado de gestacion registrado.
        patient_age_years: edad en anos cumplidos, o None si se desconoce.
    """
    medications = medications or []
    findings = []
    findings += check_allergies(medications, allergies)
    findings += check_interactions(medications, active_medications)
    findings += check_duplicate_therapy(medications, active_medications)
    findings += check_pregnancy(medications, is_pregnant)
    findings += check_quantities(medications)
    findings += check_pediatric(medications, patient_age_years)
    return SafetyReport(findings)


def knowledge_base_age_months(today=None):
    """Meses transcurridos desde la ultima revision clinica de las tablas."""
    today = today or date.today()
    return (today.year - KNOWLEDGE_BASE_REVIEWED.year) * 12 + (
        today.month - KNOWLEDGE_BASE_REVIEWED.month
    )


def knowledge_base_is_stale(today=None):
    return knowledge_base_age_months(today) > KNOWLEDGE_BASE_MAX_AGE_MONTHS
