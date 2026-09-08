# -*- coding: utf-8 -*-
"""Arma capturas/index.html: el recorrido completo en una sola pagina.

Se ejecuta despues de capturar.py. Agrupa por rol, conserva el orden de
captura y enlaza cada pantalla con su version movil.
"""
import io
import json
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESTINO = os.path.join(RAIZ, 'capturas')

ROLES = {
    'publico': ('Sin sesión', 'Lo que ve alguien que todavía no ha entrado'),
    'pac': ('Paciente', 'María Elena Pérez Gómez · usuario pac'),
    'doc': ('Profesional', 'Carlos Rodríguez Mesa · usuario doc'),
    'adm': ('Administración', 'Ana Torres Gil · usuario adm'),
    'sta': ('Personal', 'Sofía Ruiz León · usuario sta'),
    'aut': ('Profesional autónomo', 'Sandra Milena Ochoa · usuario aut'),
    'exp': ('Farmacia', 'Luis Mora Díaz · usuario exp'),
    'sup': ('Superadministración', 'usuario sup'),
}

# Los codigos que no son 200 y aun asi son correctos: se explican en la ficha
# para que nadie los persiga como si fueran un fallo.
# El unico codigo que no es 200 y aun asi es correcto: la pagina de error solo
# se puede fotografiar pidiendo una ruta que no existe.
ESPERADOS = {
    ('publico', '/ruta-que-no-existe'): 'página de error, a propósito',
}

PLANTILLA = u'''<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RuralHealth Connect - Recorrido de la interfaz</title>
<style>
:root{--tinta:#0f172a;--tenue:#475569;--borde:#e2e8f0;--fondo:#f8fafc;--acento:#0369A1}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--tinta);
     font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{background:#fff;border-bottom:1px solid var(--borde);padding:28px 32px}
h1{margin:0 0 6px;font-size:24px;letter-spacing:-.01em}
header p{margin:0;color:var(--tenue);font-size:14px}
main{padding:24px 32px 64px;max-width:1600px;margin:0 auto}
section{margin-bottom:44px}
h2{font-size:18px;margin:0 0 2px}
.sub{margin:0 0 16px;color:var(--tenue);font-size:13px}
.rejilla{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px}
figure{margin:0;background:#fff;border:1px solid var(--borde);border-radius:10px;overflow:hidden}
figure img{display:block;width:100%;height:190px;object-fit:cover;object-position:top;
           border-bottom:1px solid var(--borde);background:#fff}
figcaption{padding:10px 12px;font-size:13px}
figcaption code{color:var(--tenue);font-size:11.5px}
.estado{background:#f1f5f9;color:#334155;border:1px solid var(--borde);
        border-radius:99px;padding:1px 7px;font-size:11px;margin-left:6px}
.movil{color:var(--acento);font-size:12px;text-decoration:none}
.movil:hover{text-decoration:underline}
a{color:inherit}
</style></head><body>
<header>
<h1>RuralHealth Connect &middot; recorrido de la interfaz</h1>
<p>{n} pantallas, en escritorio y en móvil. Clic para ver la captura completa.</p>
</header>
<main>{cuerpo}</main></body></html>'''


def main():
    informe = json.load(
        io.open(os.path.join(DESTINO, 'informe.json'), encoding='utf-8'))

    por_rol = {}
    for i in informe:
        if i.get('archivo') and i['vista'] == 'escritorio':
            por_rol.setdefault(i['rol'], []).append(i)

    movil = {(i['rol'], i['ruta']): i['archivo']
             for i in informe if i.get('archivo') and i['vista'] == 'movil'}

    filas = []
    for rol, (titulo, detalle) in ROLES.items():
        pantallas = por_rol.get(rol, [])
        if not pantallas:
            continue
        filas.append(u'<section><h2>%s</h2><p class="sub">%s</p>'
                     u'<div class="rejilla">' % (titulo, detalle))
        for i in pantallas:
            if i['estado'] == 200:
                estado = u''
            else:
                nota = ESPERADOS.get((i['rol'], i['ruta']))
                estado = u'<span class="estado">HTTP %s%s</span>' % (
                    i['estado'], (u' &middot; ' + nota) if nota else u'')
            m = movil.get((rol, i['ruta']))
            enlace_movil = (
                u'<br><a class="movil" href="movil/%s" target="_blank">'
                u'ver en móvil</a>' % m) if m else u''
            filas.append(
                u'<figure>'
                u'<a href="escritorio/%s" target="_blank">'
                u'<img src="escritorio/%s" loading="lazy" alt="%s"></a>'
                u'<figcaption><b>%s</b>%s<br><code>%s</code>%s</figcaption>'
                u'</figure>'
                % (i['archivo'], i['archivo'], i['titulo'], i['titulo'], estado,
                   i['ruta'], enlace_movil))
        filas.append(u'</div></section>')

    html = PLANTILLA.replace('{n}', str(len(informe) // 2)) \
                    .replace('{cuerpo}', u'\n'.join(filas))
    io.open(os.path.join(DESTINO, 'index.html'), 'w', encoding='utf-8').write(html)
    print('indice generado con %d pantallas' % (len(informe) // 2))


if __name__ == '__main__':
    main()
