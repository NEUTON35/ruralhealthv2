# -*- coding: utf-8 -*-
"""RIPS en JSON, como soporte de la factura electronica de venta en salud.

Por que existe este modulo
--------------------------
`rips_service.py` genera los archivos planos CT, AF, US y AC de la Resolucion
3374 de 2000. Esa resolucion fue **derogada el 30 de junio de 2023** por la
Resolucion 1036 de 2022. El formato viejo no se quedo corto: ya no lo recibe
nadie.

Lo vigente
----------
- **Resolucion 948 de 2026** (14 de mayo de 2026), que derogo la Resolucion 2275
  de 2023 y las 558 y 1884 de 2024.
- El RIPS viaja en JSON, asociado a la factura electronica de venta en salud
  (FEV) validada por la DIAN.
- Ambos se envian al Mecanismo Unico de Validacion del Ministerio, que devuelve
  el **CUV** (Codigo Unico de Validacion). Sin CUV no hay radicacion.
- Desde el 1 de junio de 2026 las reglas de validacion pasaron de notificar a
  **rechazar**.

Que hace este modulo
--------------------
Arma el JSON y lo valida en local contra las tablas de referencia oficiales.
No radica: la radicacion exige la FEV validada por la DIAN, que depende del
proveedor tecnologico que contrate el prestador, y las credenciales del MUV.
Mientras eso no exista, generar el JSON sirve para revisar que los datos estan
completos, no para cumplir.

Igual que en `rips_service`, **no se inventa ningun dato**. Si falta, se rechaza
la generacion con el detalle de que falta y en que atencion. La regla RVC096 de
la Resolucion 948 bloquea los codigos de relleno, asi que rellenar no es una
opcion ni siquiera pragmatica.

Lo que falta por confirmar
--------------------------
La Resolucion 948 anadio tres campos obligatorios cuyos nombres exactos no
pudieron extraerse del texto publicado:

- **CIE-11** y **Codigo VIDA** en consultas y procedimientos (exigible desde el
  1 de julio de 2026).
- **SIRAS** (campo U12), obligatorio para usuarios tipo 10, victimas de
  accidente de transito cubiertas por SOAT (exigible desde el 1 de septiembre
  de 2026).

Estan declarados en `CAMPOS_PENDIENTES_RES948` y el generador avisa de ellos en
lugar de emitirlos con un nombre inventado. Un campo mal nombrado se rechaza
igual que uno ausente, pero ademas hace creer que esta resuelto.
"""

import json
from datetime import date, datetime

from models import (Clinic, MedicalHistory, RIPSReferenceCode, TABLA_CAUSA_EXTERNA,
                    TABLA_CONCEPTO_RECAUDO, TABLA_FINALIDAD, TABLA_MODALIDAD,
                    TABLA_TIPO_DIAGNOSTICO, TABLA_TIPO_USUARIO, TABLA_ZONA, User)

VERSION_NORMA = 'Resolucion 948 de 2026'

# Campos que la Resolucion 948 exige y que aun no se emiten porque no se pudo
# confirmar su nombre exacto en el anexo tecnico. Se listan para que el
# operador sepa que le falta antes de intentar radicar.
CAMPOS_PENDIENTES_RES948 = (
    ('CIE-11', 'Obligatorio en consultas desde el 1 de julio de 2026. Requiere '
               'ademas cargar el catalogo CIE-11, que la aplicacion no tiene.'),
    ('Codigo VIDA', 'Obligatorio en consultas desde el 1 de julio de 2026.'),
    ('SIRAS', 'Obligatorio para usuarios tipo 10 (victimas de accidente de '
              'transito, SOAT) desde el 1 de septiembre de 2026.'),
)

# Grupo de servicios y servicio para consulta externa. Vienen de las tablas
# del Ministerio; se dejan como constantes porque esta aplicacion solo presta
# consulta externa. Un prestador con urgencias u hospitalizacion tendria que
# resolverlos por servicio.
GRUPO_CONSULTA_EXTERNA = '01'
SERVICIO_CONSULTA_EXTERNA = 1


class RIPSJSONError(Exception):
    """No se puede generar el JSON porque faltan datos obligatorios."""

    def __init__(self, issues):
        self.issues = list(issues)
        super().__init__('%d problema(s) impiden generar el RIPS' % len(self.issues))


def _problema(scope, entity, field, message, entity_id=None):
    return {'scope': scope, 'entity': entity, 'entity_id': entity_id,
            'field': field, 'message': message}


def _fecha_hora(valor):
    """`fechaInicioAtencion` va como 'AAAA-MM-DD HH:MM' (16 caracteres)."""
    if isinstance(valor, datetime):
        return valor.strftime('%Y-%m-%d %H:%M')
    return str(valor)[:16]


def _fecha(valor):
    if isinstance(valor, (datetime, date)):
        return valor.strftime('%Y-%m-%d')
    return str(valor)[:10]


def _codigo_valido(tabla, codigo):
    return RIPSReferenceCode.es_valido(tabla, codigo)


def _tipo_usuario(paciente):
    """Deriva el tipo de usuario del regimen de afiliacion.

    Solo cubre los casos que la aplicacion registra. Si el regimen no permite
    determinarlo, se devuelve None y el generador lo reporta como faltante en
    lugar de suponer.
    """
    regimen = (getattr(paciente, 'affiliation_regime', None) or '').strip().lower()
    tipo = (getattr(paciente, 'affiliation_type', None) or '').strip().lower()
    if regimen == 'subsidiado':
        return '04'
    if regimen == 'contributivo':
        if tipo in ('beneficiario', 'beneficiaria'):
            return '02'
        if tipo == 'adicional':
            return '03'
        return '01'
    if regimen in ('no afiliado', 'no_afiliado', 'vinculado'):
        return '05'
    return None


def _zona(paciente):
    """Zona territorial segun ZonaVersion2.

    CUIDADO: en esta tabla 01 es Rural y 02 es Urbano, al reves que en el
    catalogo del IHCE. La aplicacion guarda 'R' o 'U'.
    """
    zona = (getattr(paciente, 'zone', None) or '').strip().upper()
    return {'R': '01', 'U': '02'}.get(zona)


def validar_usuario(paciente):
    issues = []
    etiqueta = 'Paciente %s' % (getattr(paciente, 'id', '?'))

    if not (getattr(paciente, 'document_type', None) or '').strip():
        issues.append(_problema('usuario', etiqueta, 'document_type',
                                'El paciente no tiene tipo de documento.',
                                paciente.id))
    if not (getattr(paciente, 'cedula', None) or '').strip():
        issues.append(_problema('usuario', etiqueta, 'cedula',
                                'El paciente no tiene numero de documento.',
                                paciente.id))
    if not getattr(paciente, 'birth_date', None):
        issues.append(_problema('usuario', etiqueta, 'birth_date',
                                'El paciente no tiene fecha de nacimiento.',
                                paciente.id))
    if not (getattr(paciente, 'sex', None) or '').strip():
        issues.append(_problema('usuario', etiqueta, 'sex',
                                'El paciente no tiene sexo registrado.',
                                paciente.id))
    if not (getattr(paciente, 'municipality_code', None) or '').strip():
        issues.append(_problema('usuario', etiqueta, 'municipality_code',
                                'El paciente no tiene municipio de residencia.',
                                paciente.id))

    tipo = _tipo_usuario(paciente)
    if not tipo:
        issues.append(_problema(
            'usuario', etiqueta, 'affiliation_regime',
            'No se puede determinar el tipo de usuario: falta el regimen de '
            'afiliacion. No se supone, porque de el depende quien paga.',
            paciente.id))
    elif not _codigo_valido(TABLA_TIPO_USUARIO, tipo):
        issues.append(_problema('usuario', etiqueta, 'affiliation_regime',
                                'El tipo de usuario "%s" no esta en el catalogo '
                                'del Ministerio.' % tipo, paciente.id))

    if not _zona(paciente):
        issues.append(_problema('usuario', etiqueta, 'zone',
                                'El paciente no tiene zona de residencia.',
                                paciente.id))
    return issues


def validar_consulta(historia):
    issues = []
    etiqueta = 'Atencion %s' % historia.id

    if not (historia.cups_code or '').strip():
        issues.append(_problema('atencion', etiqueta, 'cups_code',
                                'La atencion no tiene procedimiento CUPS.',
                                historia.id))
    if not (historia.cie10_code or '').strip():
        issues.append(_problema('atencion', etiqueta, 'cie10_code',
                                'La atencion no tiene diagnostico principal.',
                                historia.id))

    for campo, tabla, nombre in (
            ('external_cause', TABLA_CAUSA_EXTERNA, 'causa externa'),
            ('consultation_purpose', TABLA_FINALIDAD, 'finalidad de la consulta'),
            ('care_modality', TABLA_MODALIDAD, 'modalidad de atencion')):
        valor = (getattr(historia, campo, None) or '').strip()
        if not valor:
            issues.append(_problema('atencion', etiqueta, campo,
                                    'La atencion no tiene %s.' % nombre,
                                    historia.id))
        elif not _codigo_valido(tabla, valor):
            issues.append(_problema(
                'atencion', etiqueta, campo,
                'El codigo de %s "%s" no esta en el catalogo del Ministerio. '
                'La regla RVC096 rechaza los codigos que no existan en las '
                'tablas de referencia.' % (nombre, valor), historia.id))

    profesional = historia.doctor
    if profesional is None:
        issues.append(_problema('atencion', etiqueta, 'doctor_id',
                                'La atencion no tiene profesional asociado.',
                                historia.id))
    elif not (getattr(profesional, 'cedula', None) or '').strip():
        issues.append(_problema('atencion', etiqueta, 'doctor_document',
                                'El profesional no tiene documento registrado.',
                                historia.id))
    return issues


def _usuario_json(paciente, consecutivo):
    return {
        'tipoDocumentoIdentificacion': (paciente.document_type or '').strip().upper(),
        'numDocumentoIdentificacion': (paciente.cedula or '').strip(),
        'tipoUsuario': _tipo_usuario(paciente),
        'fechaNacimiento': _fecha(paciente.birth_date),
        'codSexo': (paciente.sex or '').strip().upper(),
        'codPaisResidencia': (getattr(paciente, 'nationality_code', None) or '170'),
        'codMunicipioResidencia': '%s%s' % (
            (paciente.department_code or '').strip(),
            (paciente.municipality_code or '').strip()),
        'codZonaTerritorialResidencia': _zona(paciente),
        # No se emiten incapacidades desde esta aplicacion.
        'incapacidad': 'NO',
        'consecutivo': consecutivo,
        'codPaisOrigen': (getattr(paciente, 'nationality_code', None) or '170'),
    }


def _consulta_json(historia, clinica, consecutivo, valor=0):
    profesional = historia.doctor
    return {
        'codPrestador': (clinica.habilitacion_code or '').strip(),
        'fechaInicioAtencion': _fecha_hora(historia.created_at),
        # Esta aplicacion no gestiona autorizaciones. El anexo dice que cuando
        # el servicio no la requiere se informa null, no una cadena vacia.
        'numAutorizacion': None,
        'codConsulta': (historia.cups_code or '').strip(),
        'modalidadGrupoServicioTecSal': (historia.care_modality or '').strip(),
        'grupoServicios': GRUPO_CONSULTA_EXTERNA,
        'codServicio': SERVICIO_CONSULTA_EXTERNA,
        'finalidadTecnologiaSalud': (historia.consultation_purpose or '').strip(),
        'causaMotivoAtencion': (historia.external_cause or '').strip(),
        'codDiagnosticoPrincipal': (historia.cie10_code or '').strip().upper(),
        'codDiagnosticoRelacionado1': None,
        'codDiagnosticoRelacionado2': None,
        'codDiagnosticoRelacionado3': None,
        # 1 = impresion diagnostica. Es lo que corresponde a una consulta
        # externa de primer nivel: el diagnostico se confirma despues.
        'tipoDiagnosticoPrincipal': '1',
        'tipoDocumentoIdentificacion': (
            getattr(profesional, 'document_type', None) or 'CC').strip().upper(),
        'numDocumentoIdentificacion': (getattr(profesional, 'cedula', None) or '').strip(),
        'vrServicio': valor,
        # 05 = no aplica pago moderador.
        'conceptoRecaudo': '05',
        'valorPagoModerador': 0,
        'numFEVPagoModerador': None,
        'consecutivo': consecutivo,
    }


def generar(clinic_id, start, end, num_factura, nit_obligado=None,
            valores=None, strict=True):
    """Construye el JSON del RIPS para un periodo.

    `valores` permite pasar el valor facturado por atencion ({history_id: valor});
    lo que no se indique va en 0, que es lo que corresponde a un servicio sin
    cobro. Cuando haya facturacion electronica real, estos valores tienen que
    cuadrar con la FEV o el MUV rechaza el conjunto.

    Devuelve `(documento, avisos)`. Lanza `RIPSJSONError` si faltan datos.
    """
    clinica = Clinic.query.get(clinic_id)
    if clinica is None:
        raise RIPSJSONError([_problema('prestador', 'Clinica', 'clinic_id',
                                       'No existe la clinica %s.' % clinic_id)])

    issues = []
    if not (clinica.habilitacion_code or '').strip():
        issues.append(_problema('prestador', clinica.name, 'habilitacion_code',
                                'La institucion no tiene codigo de habilitacion '
                                'en REPS.'))
    obligado = (nit_obligado or getattr(clinica, 'nit', None) or '').strip()
    if not obligado:
        issues.append(_problema('prestador', clinica.name, 'nit',
                                'La institucion no tiene NIT registrado.'))
    if not (num_factura or '').strip():
        issues.append(_problema('factura', 'Factura', 'numFactura',
                                'Debe indicarse el numero de la factura '
                                'electronica de venta. El RIPS es su soporte: '
                                'sin factura no hay nada que soportar.'))

    historias = (MedicalHistory.query
                 .filter(MedicalHistory.clinic_id == clinic_id,
                         MedicalHistory.created_at >= start,
                         MedicalHistory.created_at <= end)
                 .order_by(MedicalHistory.created_at.asc()).all())
    if not historias:
        issues.append(_problema('periodo', 'Periodo', 'rango',
                                'No hay atenciones en el periodo indicado.'))

    pacientes = {}
    for historia in historias:
        issues.extend(validar_consulta(historia))
        paciente = historia.patient
        if paciente is None:
            issues.append(_problema('atencion', 'Atencion %s' % historia.id,
                                    'patient_id',
                                    'La atencion no tiene paciente asociado.',
                                    historia.id))
            continue
        if paciente.id not in pacientes:
            pacientes[paciente.id] = paciente
            issues.extend(validar_usuario(paciente))

    if issues and strict:
        raise RIPSJSONError(issues)

    valores = valores or {}
    consecutivo_usuario = {}
    usuarios = []
    for n, (pid, paciente) in enumerate(sorted(pacientes.items()), start=1):
        consecutivo_usuario[pid] = n
        usuarios.append(_usuario_json(paciente, n))

    consultas = []
    for n, historia in enumerate(historias, start=1):
        if historia.patient_id not in consecutivo_usuario:
            continue
        consultas.append(_consulta_json(
            historia, clinica, n, valor=valores.get(historia.id, 0)))

    documento = {
        'numDocumentoIdObligado': obligado,
        'numFactura': (num_factura or '').strip(),
        'tipoNota': None,
        'numNota': None,
        'usuarios': usuarios,
        'servicios': {
            'consultas': consultas,
        },
    }

    avisos = [
        'Generado conforme a %s.' % VERSION_NORMA,
        'Este archivo NO esta radicado. La radicacion exige la factura '
        'electronica validada por la DIAN y las credenciales del Mecanismo '
        'Unico de Validacion, que devuelve el CUV.',
        'Sin CUV la factura no puede radicarse ante el pagador.',
    ]
    for nombre, detalle in CAMPOS_PENDIENTES_RES948:
        avisos.append('PENDIENTE %s: %s' % (nombre, detalle))

    return documento, avisos


def serializar(documento):
    """JSON con el separador decimal que exige la norma (punto) y sin ASCII."""
    return json.dumps(documento, ensure_ascii=False, indent=2)


def preview(clinic_id, start, end, num_factura=None):
    """Comprueba si el periodo puede generarse, sin construir el archivo."""
    try:
        documento, avisos = generar(clinic_id, start, end,
                                    num_factura or 'PENDIENTE', strict=True)
    except RIPSJSONError as e:
        return {'ok': False, 'issues': e.issues, 'avisos': []}
    return {
        'ok': True,
        'issues': [],
        'avisos': avisos,
        'usuarios': len(documento['usuarios']),
        'consultas': len(documento['servicios']['consultas']),
    }
