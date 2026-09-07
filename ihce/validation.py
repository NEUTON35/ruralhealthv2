# -*- coding: utf-8 -*-
"""Validacion local del Bundle antes de transmitirlo.

El propio Manual de operaciones v1.4 lo pide: "El cliente debe validarlas
localmente antes de enviar para reducir rechazos" (seccion 5.4).

Aqui hay una razon adicional que pesa mas en este proyecto. La conectividad de
un puesto de salud rural es cara y escasa. Gastar un intento de red en un
documento que va a devolver 400 no solo pierde la llamada: mete el envio en la
cola de reintentos y retrasa a los que si estaban bien.

Se implementan las reglas 1 a 6 del manual que pueden comprobarse sin consultar
registros nacionales. Las que dependen de EVOL, REPS y RETHUS (reglas 4 y 8)
solo puede resolverlas el servidor.
"""

from . import terminology as T


class ValidationError(Exception):
    """El Bundle no cumple las reglas y no debe transmitirse."""

    def __init__(self, errores):
        self.errores = list(errores)
        super().__init__('; '.join(self.errores))


def validar_bundle(bundle):
    """Devuelve la lista de errores. Vacia significa que se puede transmitir."""
    errores = []

    if bundle.get('resourceType') != 'Bundle':
        errores.append('El recurso raiz debe ser un Bundle.')
    # Regla 1
    if bundle.get('type') != 'document':
        errores.append('Bundle.type debe ser "document" (regla 1).')

    entradas = bundle.get('entry') or []
    if not entradas:
        errores.append('El Bundle no tiene entradas.')
        return errores

    # Regla 2
    primero = (entradas[0].get('resource') or {}).get('resourceType')
    if primero != 'Composition':
        errores.append('La primera entrada debe ser el Composition (regla 2); '
                       'se encontro %s.' % (primero or 'nada'))

    # Regla 3a
    composiciones = [e for e in entradas
                     if (e.get('resource') or {}).get('resourceType') == 'Composition']
    if len(composiciones) > 1:
        errores.append('Solo se permite un Composition por Bundle (regla 3a); '
                       'hay %d.' % len(composiciones))
    if not composiciones:
        return errores

    composition = composiciones[0]['resource']

    for campo in ('status', 'type', 'subject', 'encounter', 'date', 'author', 'title'):
        if not composition.get(campo):
            errores.append('Composition.%s es obligatorio.' % campo)

    if composition.get('title') != 'RDA Consulta':
        errores.append('Composition.title debe ser exactamente "RDA Consulta"; '
                       'es "%s".' % composition.get('title'))

    # Regla 3b: todas las secciones del perfil, con entradas o con emptyReason.
    secciones = composition.get('section') or []
    por_codigo = {}
    for s in secciones:
        for coding in (s.get('code') or {}).get('coding', []):
            if coding.get('code'):
                por_codigo[coding['code']] = s

    for definicion in T.SECCIONES_CONSULTA:
        if not definicion.get('obligatoria'):
            continue
        seccion = por_codigo.get(definicion['codigo'])
        if seccion is None:
            errores.append('Falta la seccion obligatoria %s (LOINC %s).'
                           % (definicion['nombre'], definicion['codigo']))
            continue
        if seccion.get('title') != definicion['titulo']:
            errores.append('El titulo de %s debe ser exactamente "%s".'
                           % (definicion['nombre'], definicion['titulo']))
        tiene_entradas = bool(seccion.get('entry'))
        if not tiene_entradas and not seccion.get('emptyReason'):
            errores.append('La seccion %s va sin entradas y sin emptyReason '
                           '(regla 3b).' % definicion['nombre'])
        if definicion.get('exige_entrada') and not tiene_entradas:
            errores.append('La seccion %s exige al menos una entrada; un RDA sin '
                           'diagnostico se rechaza.' % definicion['nombre'])

    # Regla 6: toda referencia interna debe existir como entrada del Bundle.
    ids = set()
    for e in entradas:
        r = e.get('resource') or {}
        if r.get('id'):
            ids.add('%s/%s' % (r.get('resourceType'), r['id']))

    for referencia in _referencias(bundle):
        if referencia.startswith('#'):
            referencia = referencia[1:]
        if '://' in referencia or referencia.startswith('urn:'):
            continue
        if referencia not in ids:
            errores.append('La referencia %s no corresponde a ninguna entrada '
                           'del Bundle (regla 6).' % referencia)

    return errores


def _referencias(nodo, encontradas=None):
    """Recorre el Bundle recogiendo todos los `reference`."""
    if encontradas is None:
        encontradas = []
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            if clave == 'reference' and isinstance(valor, str):
                encontradas.append(valor)
            else:
                _referencias(valor, encontradas)
    elif isinstance(nodo, list):
        for item in nodo:
            _referencias(item, encontradas)
    return encontradas


def validar_datos_minimos(paciente, profesional, clinica, historia):
    """Comprueba los datos del prestador antes incluso de armar el Bundle.

    Separado de `validar_bundle` porque estos fallos se corrigen en la interfaz
    (falta completar un perfil), no en el mapeo. El mensaje dice donde.
    """
    errores = []

    if not (getattr(paciente, 'cedula', None) or '').strip():
        errores.append('El paciente no tiene numero de documento registrado.')
    tipo = (getattr(paciente, 'document_type', None) or 'CC').strip().upper()
    if not T.tipo_documento_valido(tipo):
        errores.append('El tipo de documento "%s" del paciente no esta en el '
                       'ValueSet del Ministerio.' % tipo)
    if not getattr(paciente, 'birth_date', None):
        errores.append('El paciente no tiene fecha de nacimiento; el perfil '
                       'PatientRDA la exige.')
    if not (getattr(paciente, 'sex', None) or '').strip():
        errores.append('El paciente no tiene sexo registrado; el perfil '
                       'PatientRDA lo exige.')

    if not (getattr(profesional, 'cedula', None) or '').strip():
        errores.append('El profesional no tiene numero de documento registrado.')
    if not (getattr(profesional, 'medical_registration', None) or '').strip():
        errores.append('El profesional no tiene registro medico; sin el no puede '
                       'validarse en RETHUS (regla 8).')

    if not (getattr(clinica, 'habilitacion_code', None) or '').strip():
        errores.append('La institucion no tiene codigo de habilitacion REPS; '
                       'se configura en Ajustes.')

    if not (getattr(historia, 'cie10_code', None) or '').strip():
        errores.append('La atencion no tiene diagnostico CIE-10; la seccion de '
                       'problemas del RDA exige al menos uno.')

    return errores
