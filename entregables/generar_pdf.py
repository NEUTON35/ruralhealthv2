# -*- coding: utf-8 -*-
"""Convierte cada entregable de Markdown a PDF.

Por que hacen falta las dos formas
----------------------------------
El Markdown es la fuente: GitHub lo renderiza, se edita sin herramientas y el
control de versiones muestra que cambio de una revision a otra.

Pero a un abogado o a un quimico farmaceutico no se le manda un `.md`. Se le
manda un PDF que pueda abrir, imprimir y anotar al margen, y que se vea como
un documento y no como un archivo de programador.

Los PDF no se versionan: se regeneran en segundos, y versionar binarios que
cambian con cada retoque de texto ensucia el historial sin aportar nada.

    python entregables/generar_pdf.py

Necesita `markdown` y `playwright`:

    pip install markdown playwright && playwright install chromium
"""
import glob
import io
import os
import re

RAIZ = os.path.dirname(os.path.abspath(__file__))

# Tipografia con serifa para el cuerpo. Es lo que se usa en un documento que se
# lee entero y se anota, no en una pantalla que se ojea.
ESTILO = """
@page { size: letter; margin: 20mm 18mm 22mm 18mm; }
* { box-sizing: border-box; }
body {
  font: 11.5pt/1.55 Georgia, 'Times New Roman', serif;
  color: #111; margin: 0;
}
h1 {
  font-size: 20pt; line-height: 1.2; margin: 0 0 4pt;
  border-bottom: 2px solid #111; padding-bottom: 8pt;
}
h2 {
  font-size: 14pt; margin: 22pt 0 6pt;
  border-bottom: 1px solid #999; padding-bottom: 3pt;
  page-break-after: avoid;
}
h3 { font-size: 12pt; margin: 16pt 0 4pt; page-break-after: avoid; }
p, li { margin: 0 0 7pt; }
ul, ol { margin: 0 0 10pt; padding-left: 20pt; }
li { margin-bottom: 4pt; }
strong { font-weight: bold; }
code {
  font: 10pt 'Courier New', Courier, monospace;
  background: #f2f2f2; padding: 1pt 3pt; border: 1px solid #ddd;
}
pre {
  font: 9.5pt/1.45 'Courier New', Courier, monospace;
  background: #f7f7f7; border: 1px solid #ccc; border-left: 3px solid #555;
  padding: 8pt 10pt; overflow-x: auto; page-break-inside: avoid;
}
pre code { background: none; border: 0; padding: 0; }
table {
  border-collapse: collapse; width: 100%; margin: 8pt 0 12pt;
  font-size: 10.5pt; page-break-inside: avoid;
}
th {
  text-align: left; background: #eee; border: 1px solid #999;
  padding: 5pt 7pt; font-weight: bold;
}
td { border: 1px solid #bbb; padding: 5pt 7pt; vertical-align: top; }
blockquote {
  margin: 8pt 0; padding: 6pt 12pt; border-left: 3px solid #555;
  background: #f7f7f7; font-style: italic;
}
/* Los campos de cada punto (Quien, Desbloquea, Detalle) van en lineas
   propias y sin vineta: son metadatos del punto, no puntos por si mismos.
   Juntos en un parrafo, la lista deja de poderse escanear, que es lo unico
   que se le pide a una lista para ir tachando. */
li ul { margin: 3pt 0 0; padding-left: 0; }
li ul li {
  list-style: none; margin: 0 0 1pt; font-size: 10.5pt; color: #333;
}
li.tarea > p, li.hecha > p { display: inline; }
hr { border: 0; border-top: 1px solid #bbb; margin: 18pt 0; }
a { color: #111; }
/* Las casillas de la lista de pendientes, para poder marcarlas a mano. */
li.tarea { list-style: none; margin-left: -14pt; }
li.tarea::before {
  content: ''; display: inline-block; width: 9pt; height: 9pt;
  border: 1px solid #333; margin-right: 7pt; vertical-align: -1pt;
}
li.hecha { list-style: none; margin-left: -14pt; color: #555; }
li.hecha::before {
  content: '\\2713'; display: inline-block; width: 9pt; margin-right: 7pt;
  font-weight: bold; color: #111;
}
"""


def a_html(texto_md, titulo):
    import markdown

    cuerpo = markdown.markdown(
        texto_md, extensions=['tables', 'fenced_code', 'sane_lists'])

    # Las casillas `- [ ]` las escupe Markdown como texto plano dentro del
    # `<li>`. Se convierten en casillas de verdad, que es lo que hace util un
    # documento pensado para ir tachando en papel.
    #
    # Hay que cubrir las dos formas. Un punto simple queda como `<li>[ ] ...`,
    # pero uno que lleva sublista debajo queda como `<li><p>[ ] ...`, porque
    # Markdown lo trata como contenido de bloque. Con un solo patron se
    # convertia la primera casilla del documento y las demas salian como
    # corchetes de texto.
    cuerpo = re.sub(r'<li>\s*(<p>)?\s*\[ \]\s*',
                    lambda m: '<li class="tarea">' + (m.group(1) or ''),
                    cuerpo)
    cuerpo = re.sub(r'<li>\s*(<p>)?\s*\[x\]\s*',
                    lambda m: '<li class="hecha">' + (m.group(1) or ''),
                    cuerpo, flags=re.I)

    return ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<title>%s</title><style>%s</style></head><body>%s</body></html>'
            % (titulo, ESTILO, cuerpo))


def main():
    from playwright.sync_api import sync_playwright

    fuentes = sorted(glob.glob(os.path.join(RAIZ, '**', '*.md'),
                               recursive=True))
    # El indice no se convierte: es para leer en GitHub, no para enviar.
    fuentes = [f for f in fuentes if os.path.basename(f) != 'README.md']

    if not fuentes:
        print('no hay entregables que convertir')
        return

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()
        for ruta in fuentes:
            texto = io.open(ruta, encoding='utf-8').read()
            titulo = texto.lstrip('# ').split('\n')[0].strip()
            destino = ruta[:-3] + '.pdf'

            pagina.set_content(a_html(texto, titulo), wait_until='load')
            pagina.pdf(path=destino, format='Letter',
                       print_background=True,
                       margin={'top': '0', 'bottom': '0',
                               'left': '0', 'right': '0'},
                       display_header_footer=True,
                       header_template='<span></span>',
                       footer_template=(
                           '<div style="font:8pt Georgia,serif;color:#666;'
                           'width:100%;padding:0 18mm;display:flex;'
                           'justify-content:space-between">'
                           '<span>RuralHealth Connect</span>'
                           '<span class="pageNumber"></span></div>'))
            print('  %s  (%.0f KB)'
                  % (os.path.relpath(destino, RAIZ),
                     os.path.getsize(destino) / 1024))
        navegador.close()

    print('\n%d documentos generados.' % len(fuentes))


if __name__ == '__main__':
    main()
