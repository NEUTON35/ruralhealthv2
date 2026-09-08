# -*- coding: utf-8 -*-
"""Extrae el contrato de cada plantilla, para poder rediseñarlas sin romperlas.

El riesgo de reescribir cuarenta y cuatro plantillas no es que queden feas: es
que desaparezca en silencio el `name` de un campo, un `url_for`, un token CSRF
o una rama de Jinja. Nada de eso da error. El formulario se envía, el servidor
recibe un campo menos, y el dato del paciente se pierde sin que nadie se entere
hasta que alguien lo busca.

Este script fotografía lo que cada plantilla promete:

  - los campos de cada formulario, con su `name` y su método
  - si el formulario lleva token CSRF
  - cada destino `url_for`
  - cada variable que la plantilla lee del contexto
  - los `id` a los que apunta el JavaScript
  - la herencia y los bloques

    python scripts/contrato_plantillas.py guardar   # antes de tocar nada
    python scripts/contrato_plantillas.py comparar  # después, y falla si falta algo

Lo que se añade no es un problema. Lo que se pierde, sí.
"""
import glob
import io
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANTILLAS = os.path.join(RAIZ, 'templates')
FOTO = os.path.join(RAIZ, 'scripts', 'contrato_plantillas.json')

CAMPO = re.compile(r'<(?:input|select|textarea|button)\b[^>]*\bname="([^"]+)"',
                   re.I | re.S)
FORMULARIO = re.compile(r'<form\b([^>]*)>(.*?)</form>', re.I | re.S)
ACCION = re.compile(r'\baction="([^"]*)"', re.I)
METODO = re.compile(r'\bmethod="([^"]*)"', re.I)
URL_FOR = re.compile(r"url_for\(\s*'([^']+)'")
RUTA_LITERAL = re.compile(r'(?:action|href)="(/[^"{}]*)"')
EXTIENDE = re.compile(r"\{%-?\s*extends\s+'([^']+)'")
BLOQUE = re.compile(r'\{%-?\s*block\s+([a-zA-Z_][\w]*)')
INCLUYE = re.compile(r"\{%-?\s*include\s+'([^']+)'")
IDENT = re.compile(r'\bid="([^"{}]+)"')
# Raiz de cada variable de contexto: `order.doctor.name` -> `order`.
EXPRESION = re.compile(r'\{\{(.*?)\}\}|\{%(.*?)%\}', re.S)
RAIZ_VAR = re.compile(r'\b([a-z_][a-z0-9_]*)\b')

# Palabras de Jinja y filtros propios: no son variables del contexto.
RESERVADAS = {
    'if', 'else', 'elif', 'endif', 'for', 'endfor', 'in', 'not', 'and', 'or',
    'is', 'none', 'true', 'false', 'set', 'block', 'endblock', 'extends',
    'include', 'with', 'without', 'context', 'macro', 'endmacro', 'call',
    'filter', 'endfilter', 'do', 'break', 'continue', 'as', 'import', 'from',
    'length', 'default', 'join', 'lower', 'upper', 'title', 'trim', 'safe',
    'escape', 'e', 'int', 'float', 'string', 'list', 'first', 'last', 'sort',
    'reverse', 'round', 'abs', 'replace', 'truncate', 'striptags', 'urlencode',
    'tojson', 'selectattr', 'rejectattr', 'map', 'sum', 'min', 'max', 'batch',
    'slice', 'groupby', 'attr', 'format', 'indent', 'wordwrap', 'capitalize',
    'url_for', 'csrf_token', 'loop', 'range', 'dict', 'defined', 'undefined',
    'equalto', 'eq', 'ne', 'lt', 'gt', 'le', 'ge', 'sameas', 'divisibleby',
    'even', 'odd', 'number', 'mapping', 'sequence', 'iterable', 'callable',
    'endwith', 'elif', 'raw', 'endraw', 'self', 'super', 'varargs', 'kwargs',
    'app_env', 'get_flashed_messages', 'category_filter', 'with_categories',
    # Filtros propios de la aplicacion.
    'colombia_iso', 'colombia_time', 'colombia_date', 'from_json',
    'legal_markup', 'estado',
}


def contrato(ruta):
    fuente = io.open(ruta, encoding='utf-8').read()

    formularios = []
    for atributos, cuerpo in FORMULARIO.findall(fuente):
        accion = ACCION.search(atributos)
        metodo = METODO.search(atributos)
        formularios.append({
            'accion': (accion.group(1).strip() if accion else ''),
            'metodo': (metodo.group(1).lower() if metodo else 'get'),
            'campos': sorted(set(CAMPO.findall(cuerpo))),
            'csrf': 'csrf_token()' in cuerpo,
        })

    variables = set()
    for llaves, etiqueta in EXPRESION.findall(fuente):
        texto = llaves or etiqueta
        # Una cadena literal dentro de la expresión no aporta variables. Sin
        # esto, `{{ 'bg-white shadow-sm' if activo else '' }}` registraba
        # `bg`, `white`, `shadow` y `sm` como si fueran del contexto, y
        # cualquier retoque de estilo aparecía luego como una pérdida.
        texto = re.sub(r"'[^']*'|\"[^\"]*\"", ' ', texto)
        # Solo la raiz de cada acceso: `a.b.c` aporta `a`.
        for trozo in re.split(r'[^\w.]+', texto):
            if not trozo:
                continue
            raiz = trozo.split('.')[0].lower()
            if raiz and raiz not in RESERVADAS and not raiz.isdigit():
                variables.add(raiz)

    return {
        'extiende': (EXTIENDE.search(fuente).group(1)
                     if EXTIENDE.search(fuente) else None),
        'bloques': sorted(set(BLOQUE.findall(fuente))),
        'incluye': sorted(set(INCLUYE.findall(fuente))),
        'formularios': formularios,
        'url_for': sorted(set(URL_FOR.findall(fuente))),
        'rutas': sorted(set(RUTA_LITERAL.findall(fuente))),
        'ids': sorted(set(IDENT.findall(fuente))),
        'variables': sorted(variables),
        'campos': sorted({c for f in formularios for c in f['campos']}),
    }


def leer_todas():
    return {
        os.path.relpath(ruta, RAIZ).replace(os.sep, '/'): contrato(ruta)
        for ruta in sorted(glob.glob(os.path.join(PLANTILLAS, '**', '*.html'),
                                     recursive=True))
    }


def guardar():
    datos = leer_todas()
    io.open(FOTO, 'w', encoding='utf-8').write(
        json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True))
    campos = sum(len(v['campos']) for v in datos.values())
    formularios = sum(len(v['formularios']) for v in datos.values())
    print('contrato guardado: %d plantillas, %d formularios, %d campos'
          % (len(datos), formularios, campos))


def comparar():
    if not os.path.exists(FOTO):
        print('no hay foto previa; ejecuta primero `guardar`')
        return 1
    antes = json.load(io.open(FOTO, encoding='utf-8'))
    ahora = leer_todas()

    perdidas = []
    for nombre, viejo in sorted(antes.items()):
        nuevo = ahora.get(nombre)
        if nuevo is None:
            perdidas.append('%s: la plantilla ya no existe' % nombre)
            continue

        for clave in ('campos', 'url_for', 'rutas', 'bloques', 'incluye', 'ids',
                      'variables'):
            faltan = sorted(set(viejo[clave]) - set(nuevo[clave]))
            if faltan:
                perdidas.append('%s: falta %s -> %s'
                                % (nombre, clave, ', '.join(faltan)[:220]))

        if viejo['extiende'] != nuevo['extiende']:
            perdidas.append('%s: cambio la herencia (%s -> %s)'
                            % (nombre, viejo['extiende'], nuevo['extiende']))

        # Un formulario que pierde su CSRF pasa a ser rechazado por el
        # servidor, o peor: queda abierto si alguien quita la proteccion.
        csrf_antes = sum(1 for f in viejo['formularios'] if f['csrf'])
        csrf_ahora = sum(1 for f in nuevo['formularios'] if f['csrf'])
        if csrf_ahora < csrf_antes:
            perdidas.append('%s: %d formularios con CSRF -> %d'
                            % (nombre, csrf_antes, csrf_ahora))

        post_antes = sum(1 for f in viejo['formularios'] if f['metodo'] == 'post')
        post_ahora = sum(1 for f in nuevo['formularios'] if f['metodo'] == 'post')
        if post_ahora < post_antes:
            perdidas.append('%s: %d formularios POST -> %d'
                            % (nombre, post_antes, post_ahora))

    nuevas = sorted(set(ahora) - set(antes))
    if nuevas:
        print('plantillas nuevas (no es un problema): %s' % ', '.join(nuevas))

    if not perdidas:
        print('el contrato se conserva en las %d plantillas' % len(antes))
        return 0
    print('SE PERDIO ALGO en %d puntos:' % len(perdidas))
    for p in perdidas:
        print('  ', p)
    return 1


if __name__ == '__main__':
    orden = sys.argv[1] if len(sys.argv) > 1 else 'comparar'
    sys.exit(guardar() if orden == 'guardar' else comparar())
