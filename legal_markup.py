"""Convierte el texto plano de los documentos legales a HTML.

Los textos de `legal_documents.py` se escriben en texto plano para que sigan
siendo legibles y revisables en el código fuente: un abogado puede leerlos sin
pelear con etiquetas HTML. Este módulo les da forma al mostrarlos.

Es un subconjunto muy pequeño y cerrado de marcado -negritas, listas, tablas y
párrafos- construido a partir del texto, nunca copiándolo tal cual. Todo el
contenido pasa por escape antes de insertarse, así que no hay forma de que una
etiqueta escrita en el documento llegue al navegador como marcado.
"""

import re

from markupsafe import Markup, escape

# Marcador que deja `render_document` cuando falta un dato del prestador.
PENDING = re.compile(r'«PENDIENTE: ([^»]+)»')


def _inline(text):
    """Negritas y marcadores pendientes. Todo lo demás se escapa."""
    salida = str(escape(text))
    # **negrita**
    salida = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', salida)
    # «PENDIENTE: ...» se resalta para que nadie lo pase por alto.
    salida = PENDING.sub(
        lambda m: f'<span class="pendiente">PENDIENTE: {m.group(1)}</span>',
        salida,
    )
    return salida


def _table(filas):
    """Tabla a partir de filas tipo `| a | b |`."""
    partes = ['<table>']
    for indice, fila in enumerate(filas):
        celdas = [c.strip() for c in fila.strip().strip('|').split('|')]
        # La segunda fila de una tabla markdown es el separador de alineación.
        if indice == 1 and all(set(c) <= set(':- ') for c in celdas):
            continue
        etiqueta = 'th' if indice == 0 else 'td'
        partes.append('<tr>')
        for celda in celdas:
            partes.append(f'<{etiqueta}>{_inline(celda)}</{etiqueta}>')
        partes.append('</tr>')
    partes.append('</table>')
    return ''.join(partes)


def render(text):
    """Devuelve el texto legal como HTML seguro."""
    if not text:
        return Markup('')

    lineas = str(text).strip().split('\n')
    salida = []
    parrafo = []
    lista = []
    tipo_lista = None
    tabla = []

    def cerrar_parrafo():
        if parrafo:
            salida.append(f'<p>{_inline(" ".join(parrafo))}</p>')
            parrafo.clear()

    def cerrar_lista():
        nonlocal tipo_lista
        if lista:
            etiqueta = tipo_lista or 'ul'
            elementos = ''.join(f'<li>{_inline(item)}</li>' for item in lista)
            salida.append(f'<{etiqueta}>{elementos}</{etiqueta}>')
            lista.clear()
            tipo_lista = None

    def cerrar_tabla():
        if tabla:
            salida.append(_table(tabla))
            tabla.clear()

    for linea in lineas:
        limpia = linea.strip()

        if not limpia:
            cerrar_parrafo()
            cerrar_lista()
            cerrar_tabla()
            continue

        if limpia.startswith('|') and limpia.endswith('|'):
            cerrar_parrafo()
            cerrar_lista()
            tabla.append(limpia)
            continue
        cerrar_tabla()

        vineta = re.match(r'^[-*]\s+(.*)$', limpia)
        if vineta:
            cerrar_parrafo()
            if tipo_lista == 'ol':
                cerrar_lista()
            tipo_lista = 'ul'
            lista.append(vineta.group(1))
            continue

        numerada = re.match(r'^\d+\.\s+(.*)$', limpia)
        if numerada:
            cerrar_parrafo()
            if tipo_lista == 'ul':
                cerrar_lista()
            tipo_lista = 'ol'
            lista.append(numerada.group(1))
            continue

        cerrar_lista()
        parrafo.append(limpia)

    cerrar_parrafo()
    cerrar_lista()
    cerrar_tabla()

    return Markup(''.join(salida))
