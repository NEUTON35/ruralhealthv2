"""Generación del Registro Individual de Prestación de Servicios de Salud (RIPS).

Por qué se reescribió
---------------------
La versión anterior rellenaba con literales inventados cada campo que no tenía a
mano: los apellidos salían como ``"Apellido1"`` y ``"Apellido2"``, la edad como
``30``, el sexo como ``M``, la entidad como ``EPS000``, la factura como
``FAC-001`` y el municipio como ``11/001`` para todos los pacientes.

Un RIPS es una declaración ante el sistema de salud. Radicar datos fabricados es
reporte de información falsa, con las consecuencias que eso acarrea para el
prestador y para el profesional que lo firma. Y hay un daño adicional y silencioso:
esos registros alimentan las estadísticas con las que se planifica la salud
pública del territorio, de modo que datos inventados en un municipio rural
distorsionan las decisiones que se toman sobre él.

Cómo funciona ahora
-------------------
El generador **no inventa nada**. Valida cada registro contra los campos que la
norma exige y, si falta información, **rechaza la exportación** devolviendo un
informe de qué falta y en qué paciente, para que se complete antes de radicar.

Es deliberadamente más incómodo que la versión anterior: producía un archivo
siempre, y ese archivo era inservible.

Alcance
-------
Cubre la estructura de archivos planos CT, AF, US y AC. La radicación electrónica
vigente (Resolución 2275 de 2023) exige además factura electrónica validada por
la DIAN y el envío en JSON al MinSalud; eso queda fuera de este módulo y debe
integrarse con el operador de facturación del prestador.
"""

import csv
import io
import zipfile
from datetime import date

from models import Appointment, Clinic, MedicalHistory, User
from time_utils import colombia_now


class RIPSValidationError(Exception):
    """La exportación no puede generarse porque faltan datos obligatorios."""

    def __init__(self, issues):
        self.issues = issues
        count = len(issues)
        super().__init__(
            f'No se puede generar el RIPS: {count} registro(s) con datos incompletos.'
        )


# Campos que la norma exige y que no admiten valor supuesto.
REQUIRED_PATIENT_FIELDS = (
    ('document_type', 'tipo de documento'),
    ('cedula', 'numero de documento'),
    ('first_surname', 'primer apellido'),
    ('first_name', 'primer nombre'),
    ('birth_date', 'fecha de nacimiento'),
    ('sex', 'sexo'),
    ('department_code', 'codigo de departamento (DANE)'),
    ('municipality_code', 'codigo de municipio (DANE)'),
)

REQUIRED_CLINIC_FIELDS = (
    ('habilitacion_code', 'codigo de habilitacion (REPS)'),
    ('nit', 'NIT'),
    ('name', 'razon social'),
)

# Tipo de usuario según régimen (Resolución 3374 de 2000, anexo técnico).
USER_TYPE_BY_REGIME = {
    'contributivo': '1',
    'subsidiado': '2',
    'vinculado': '3',
    'particular': '4',
    'otro': '5',
    'desplazado': '6',
}


def _csv_cell(value):
    """Neutraliza una celda: los RIPS se abren en hoja de cálculo antes de radicar."""
    if value is None:
        return ''
    text = str(value)
    if text and text[0] in ('=', '+', '-', '@', '\t', '\r'):
        text = "'" + text
    return text.replace('\r', ' ').replace('\n', ' ').replace(',', ' ')


def _age_and_unit(birth_date, reference):
    """Edad y unidad de medida según la norma: 1 años, 2 meses, 3 días."""
    if not birth_date:
        return None, None

    years = reference.year - birth_date.year - (
        (reference.month, reference.day) < (birth_date.month, birth_date.day)
    )
    if years >= 1:
        return years, '1'

    months = (reference.year - birth_date.year) * 12 + (reference.month - birth_date.month)
    if reference.day < birth_date.day:
        months -= 1
    if months >= 1:
        return months, '2'

    return max(0, (reference - birth_date).days), '3'


def validate_clinic(clinic):
    issues = []
    for field, label in REQUIRED_CLINIC_FIELDS:
        if not getattr(clinic, field, None):
            issues.append({
                'scope': 'prestador',
                'entity': clinic.name,
                'field': field,
                'message': f'La clinica no tiene registrado su {label}.',
            })
    return issues


def validate_patient(patient):
    issues = []
    for field, label in REQUIRED_PATIENT_FIELDS:
        if not getattr(patient, field, None):
            issues.append({
                'scope': 'paciente',
                'entity_id': patient.id,
                'entity': patient.name or f'Usuario {patient.id}',
                'field': field,
                'message': f'Falta el {label}.',
            })

    if getattr(patient, 'sex', None) and patient.sex not in {'M', 'F'}:
        issues.append({
            'scope': 'paciente',
            'entity_id': patient.id,
            'entity': patient.name,
            'field': 'sex',
            'message': f'El sexo registrado ("{patient.sex}") no es M ni F.',
        })

    if getattr(patient, 'birth_date', None) and patient.birth_date > date.today():
        issues.append({
            'scope': 'paciente',
            'entity_id': patient.id,
            'entity': patient.name,
            'field': 'birth_date',
            'message': 'La fecha de nacimiento esta en el futuro.',
        })

    return issues


def validate_history(history):
    issues = []
    label = f'Registro clinico {history.id}'

    if not history.cie10_code:
        issues.append({
            'scope': 'atencion',
            'entity_id': history.id,
            'entity': label,
            'field': 'cie10_code',
            'message': 'La atencion no tiene diagnostico CIE-10.',
        })
    if not history.cups_code:
        issues.append({
            'scope': 'atencion',
            'entity_id': history.id,
            'entity': label,
            'field': 'cups_code',
            'message': 'La atencion no tiene procedimiento CUPS.',
        })
    if not history.external_cause:
        issues.append({
            'scope': 'atencion',
            'entity_id': history.id,
            'entity': label,
            'field': 'external_cause',
            'message': (
                'La atencion no tiene causa externa. Ese campo distingue una '
                'enfermedad general de un accidente de trabajo, uno de transito '
                'o una lesion por agresion, y de el depende que la atencion se '
                'reporte al pagador correcto.'
            ),
        })
    if not history.consultation_purpose:
        issues.append({
            'scope': 'atencion',
            'entity_id': history.id,
            'entity': label,
            'field': 'consultation_purpose',
            'message': 'La atencion no tiene finalidad de consulta.',
        })
    if not history.doctor_id:
        issues.append({
            'scope': 'atencion',
            'entity_id': history.id,
            'entity': label,
            'field': 'doctor_id',
            'message': 'La atencion no tiene profesional asociado.',
        })
    elif not getattr(history.doctor, 'medical_registration', None):
        issues.append({
            'scope': 'atencion',
            'entity_id': history.id,
            'entity': label,
            'field': 'medical_registration',
            'message': (
                f'El profesional {history.doctor.name} no tiene registro medico '
                'profesional en el sistema.'
            ),
        })

    return issues


def preview_validation(clinic_id, start_date, end_date):
    """Comprueba si el periodo puede exportarse, sin generar el archivo.

    Permite al administrador ver y corregir los faltantes antes de intentar la
    radicación, en lugar de descubrirlos al ser rechazado por el validador oficial.
    """
    clinic = Clinic.query.get(clinic_id)
    if not clinic:
        raise ValueError('Clinica no encontrada.')

    issues = validate_clinic(clinic)

    histories = MedicalHistory.query.filter(
        MedicalHistory.clinic_id == clinic_id,
        MedicalHistory.created_at >= start_date,
        MedicalHistory.created_at <= end_date,
    ).all()

    seen_patients = set()
    for history in histories:
        issues.extend(validate_history(history))
        if history.patient_id not in seen_patients:
            seen_patients.add(history.patient_id)
            if history.patient:
                issues.extend(validate_patient(history.patient))

    return {
        'clinic': clinic,
        'total_records': len(histories),
        'total_patients': len(seen_patients),
        'issues': issues,
        'can_export': not issues and bool(histories),
        'empty': not histories,
    }


def generate_rips(clinic_id, start_date, end_date, invoice_number=None, strict=True):
    """Genera el paquete ZIP con los archivos planos del RIPS.

    Args:
        invoice_number: número de la factura asociada. La norma no admite un
            valor supuesto; sin él la exportación se marca como borrador.
        strict: si es True (recomendado), la exportación se detiene cuando falta
            cualquier dato obligatorio, en vez de rellenarlo con un valor inventado.

    Raises:
        RIPSValidationError: en modo estricto, cuando hay datos faltantes.
    """
    validation = preview_validation(clinic_id, start_date, end_date)
    clinic = validation['clinic']

    if strict and validation['issues']:
        raise RIPSValidationError(validation['issues'])

    if validation['empty']:
        raise ValueError('No hay atenciones registradas en el periodo seleccionado.')

    provider_code = clinic.habilitacion_code or clinic.nit or ''
    submission_date = colombia_now().strftime('%d/%m/%Y')
    invoice = invoice_number or f'SIN-FACTURA-{colombia_now().strftime("%Y%m%d")}'

    histories = MedicalHistory.query.filter(
        MedicalHistory.clinic_id == clinic_id,
        MedicalHistory.created_at >= start_date,
        MedicalHistory.created_at <= end_date,
    ).order_by(MedicalHistory.created_at.asc()).all()

    files = {}

    # --- US: usuarios atendidos ---------------------------------------------
    us_rows = []
    processed = set()
    for history in histories:
        patient = history.patient
        if not patient or patient.id in processed:
            continue
        processed.add(patient.id)

        age, age_unit = _age_and_unit(patient.birth_date, history.created_at.date())
        user_type = USER_TYPE_BY_REGIME.get(
            (patient.affiliation_regime or '').strip().lower(), '5'
        )

        us_rows.append([
            patient.document_type or '',
            patient.cedula or '',
            patient.insurer_code or '',
            user_type,
            patient.first_surname or '',
            patient.second_surname or '',
            patient.first_name or '',
            patient.second_name or '',
            age if age is not None else '',
            age_unit or '',
            patient.sex or '',
            patient.department_code or '',
            patient.municipality_code or '',
            patient.zone or 'R',
        ])
    files['US'] = us_rows

    # --- AC: consultas -------------------------------------------------------
    ac_rows = []
    for index, history in enumerate(histories, start=1):
        patient = history.patient
        if not patient:
            continue
        ac_rows.append([
            invoice,
            provider_code,
            patient.document_type or '',
            patient.cedula or '',
            history.created_at.strftime('%d/%m/%Y'),
            '',                                   # numero de autorizacion
            history.cups_code or '',
            history.consultation_purpose or '',
            history.external_cause or '',
            history.cie10_code or '',
            '',                                   # diagnostico relacionado 1
            '',                                   # diagnostico relacionado 2
            '1',                                  # tipo de diagnostico principal
            '0',                                  # valor de la consulta
            '0',                                  # valor de la cuota moderadora
            '0',                                  # valor neto a pagar
        ])
    files['AC'] = ac_rows

    # --- AF: transacciones ---------------------------------------------------
    files['AF'] = [[
        provider_code,
        clinic.name or '',
        'NI',
        clinic.nit or '',
        invoice,
        submission_date,
        start_date.strftime('%d/%m/%Y'),
        end_date.strftime('%d/%m/%Y'),
        '',                                       # codigo de la entidad
        '',                                       # nombre de la entidad
        '',                                       # numero de contrato
        '',                                       # plan de beneficios
        '',                                       # numero de poliza
        '0', '0', '0', '0',                       # copago, comision, descuento, neto
    ]]

    # --- CT: control ---------------------------------------------------------
    files['CT'] = [
        [provider_code, submission_date, 'AF000001', str(len(files['AF']))],
        [provider_code, submission_date, 'US000001', str(len(files['US']))],
        [provider_code, submission_date, 'AC000001', str(len(files['AC']))],
    ]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, rows in files.items():
            output = io.StringIO()
            writer = csv.writer(output, delimiter=',', lineterminator='\n')
            for row in rows:
                writer.writerow([_csv_cell(cell) for cell in row])
            archive.writestr(f'{name}000001.txt', output.getvalue())

        notes = [
            'PAQUETE RIPS — RuralHealth Connect',
            f'Prestador: {clinic.name} (habilitacion {provider_code})',
            f'Periodo: {start_date.strftime("%d/%m/%Y")} a {end_date.strftime("%d/%m/%Y")}',
            f'Generado: {submission_date}',
            f'Atenciones: {len(ac_rows)} | Usuarios: {len(us_rows)}',
            '',
            'Archivos incluidos: CT (control), AF (transacciones), US (usuarios), AC (consultas).',
            '',
            'PENDIENTE ANTES DE RADICAR:',
        ]
        if not invoice_number:
            notes.append(
                '  - Numero de factura: no se indico. El archivo lleva un marcador '
                'provisional que debe reemplazarse por la factura electronica real.'
            )
        notes.extend([
            '  - Codigo y nombre de la entidad responsable de pago (EPS/ADRES) en AF.',
            '  - Numero de contrato y plan de beneficios, si aplican.',
            '  - Validacion contra el MUV y radicacion en el canal vigente segun la',
            '    Resolucion 2275 de 2023, con la factura electronica validada por la DIAN.',
        ])
        if validation['issues']:
            notes.append('')
            notes.append(f'ADVERTENCIA: se exporto en modo no estricto con '
                         f'{len(validation["issues"])} inconsistencia(s) sin resolver.')
        archive.writestr('LEEME.txt', '\n'.join(notes))

    return buffer.getvalue()
