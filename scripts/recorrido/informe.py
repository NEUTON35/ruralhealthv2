# -*- coding: utf-8 -*-
"""Arma `capturas/informe.html`: el estado del sistema en una sola pagina.

Junta las tres cosas que se pueden comprobar de una aplicacion sin ponerla en
manos de un paciente:

  1. Como se ve      — 90 capturas, escritorio y movil, de las 45 pantallas.
  2. Si funciona     — 49 acciones reales ejecutadas rol por rol, cada una con
                       su comprobacion posterior en la base de datos.
  3. Que se arreglo  — los hallazgos de las auditorias, con su escenario de
                       fallo y el commit que los corrige.

Se ejecuta despues de `capturar.py` y de `simular.py`.
"""
import io
import json
import os
import subprocess

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESTINO = os.path.join(RAIZ, 'capturas')

ROLES = {
    'publico': ('Sin sesión', 'Lo que ve alguien que todavía no ha entrado'),
    'pac': ('Paciente', 'María Elena Pérez Gómez'),
    'doc': ('Profesional', 'Carlos Rodríguez Mesa'),
    'adm': ('Administración', 'Ana Torres Gil'),
    'sta': ('Personal', 'Sofía Ruiz León'),
    'aut': ('Profesional autónomo', 'Sandra Milena Ochoa'),
    'exp': ('Farmacia', 'Luis Mora Díaz'),
    'sup': ('Superadministración', 'usuario sup'),
}

ESPERADOS = {
    ('publico', '/ruta-que-no-existe'): 'página de error, a propósito',
}

# Los hallazgos que se corrigieron en esta ronda. El texto es el que importa:
# un hallazgo sin su escenario de fallo no se puede evaluar ni priorizar.
HALLAZGOS = [
    ('P0', 'El motor de seguridad evaluaba la marca, no el principio activo',
     'clinical_safety.py',
     'El formulario guarda la marca en <code>medicamento</code> y el principio '
     'activo en <code>nombre_med</code>. La función leía <code>medicamento</code> '
     'primero. Recetando «Amoxal» a un paciente con alergia a la penicilina '
     '<b>registrada y confirmada</b>, el motor normalizaba «amoxal», no lo '
     'encontraba en la tabla de clases y devolvía <b>cero hallazgos</b>. No '
     'saltaba nada: ni alergia, ni interacción, ni duplicidad, ni embarazo, ni '
     'pediatría. Y la orden quedaba archivada con un informe que decía «sin '
     'hallazgos», o sea con constancia escrita de que se había verificado.'),

    ('P0', 'Prescribir solo por denominación común era imposible',
     'routes_doctor.py',
     'El campo obligatorio del formulario es el principio activo. El parser '
     'recorría la lista del nombre comercial, que es opcional. Recetar por '
     'genérico —lo que exige la Resolución 1403 de 2007— devolvía «agregue al '
     'menos un medicamento». El único camino que funcionaba era escribir la '
     'marca, que era justo el que apagaba la verificación anterior.'),

    ('P0', 'El paciente no podía recoger lo que tenía apartado',
     'dispatch_engine.py',
     '<code>cantidad_comprometida</code> era un número sin dueño. Al emitir el '
     'ticket se apartaban las unidades del paciente ahí; en el mostrador, la '
     'evaluación calculaba disponible = cantidad − comprometida, o sea le '
     'restaba al paciente <b>su propia reserva</b>. Con diez unidades en '
     'estante y un ticket por diez, disponible daba cero: el ticket caía a «sin '
     'stock» con el frasco delante y no había forma de reactivarlo. En un '
     'puesto con existencias justas —el caso normal— pasaba siempre.'),

    ('P0', 'La reserva de un paciente se la llevaba otro',
     'ledger.py',
     'Al dispensar se restaba de <code>cantidad_comprometida</code> sin mirar '
     'de quién era. Una entrega sin reserva propia se comía la de un paciente '
     'crónico que todavía no había pasado a recoger.'),

    ('P0', 'El mismo ticket se entregaba dos veces',
     'dispatch_engine.py',
     'El estado del ticket se comprobaba en la ruta, fuera de toda transacción. '
     'Se bloqueaba la fila de existencias pero nunca la del ticket. Dos '
     'peticiones simultáneas —un doble clic, un reenvío del formulario— leían '
     '«autorizado» a la vez y ambas dispensaban. Reproducido: 20 unidades '
     'entregadas contra una prescripción de 10.'),

    ('P0', 'Se dispensaba contra órdenes anuladas y vencidas',
     'dispatch_engine.py',
     'La validez de la orden se comprobaba al emitir el ticket y nunca más. '
     '<code>MedicalOrder.is_dispensable</code> existía y no se llamaba desde '
     'ningún sitio. Entre la emisión y el mostrador el profesional puede haber '
     'anulado la orden por una dosis equivocada o una alergia, y el ticket '
     'seguía entregando lo que el médico acababa de retirar.'),

    ('P0', 'Cambiar la contraseña no invalidaba nada',
     'security.py · app.py',
     'El comentario del código afirmaba que «las sesiones y tokens anteriores '
     'dejan de valer». Lo único que se ejecutaba debajo era borrar el contador '
     'de intentos fallidos. Le roban la cookie a una médica en el equipo '
     'compartido del puesto de salud, la administradora emite un código, la '
     'médica fija una clave nueva, y la sesión del atacante sigue abriendo la '
     'historia clínica de todos sus pacientes. Con el token de refresco era '
     'peor: se renovaba solo, siete días cada vez, indefinidamente.'),

    ('P0', 'Un administrador de clínica reescribía los documentos legales de todas',
     'routes_settings.py',
     '<code>LegalConfiguration</code> es una tabla global: sin '
     '<code>clinic_id</code>, fuera del aislamiento. El admin de la clínica A '
     'escribía su NIT y su razón social y quedaban como responsable del '
     'tratamiento en la política de datos de <b>todas</b> las clínicas y en la '
     'página pública, sin autenticar. Ley 1581 de 2012.'),

    ('P1', 'El aviso de stock bajo usaba un umbral fijo de cinco unidades',
     'pharmacy_utils.py',
     'El stock por farmacia no tenía punto de reposición propio. Amoxicilina '
     'con 8 unidades salía en verde. Ahora el umbral es de la sede, y el cero '
     'significa «sin definir», no «cero es suficiente»: sin umbral el sistema '
     'no afirma que el stock esté bien.'),

    ('P1', 'La mercancía que llegaba a una sede secuestraba tickets de otra',
     'dispatch_engine.py',
     'Se reasignaba <code>pharmacy_id</code> dejando '
     '<code>pickup_location</code> —lo impreso en el papel que el paciente '
     'lleva en la mano— apuntando a la sede anterior. En una vereda, «la otra '
     'sede» puede ser media jornada de camino.'),

    ('P1', 'Reactivar un pendiente multiplicaba la reserva',
     'routes_staff.py',
     'Cada pulsación apartaba otro tanto: tres clics dejaban cuarenta unidades '
     'comprometidas para una orden de diez, y nadie las soltaba nunca.'),

    ('P2', 'Un mensaje del profesional sin acción se perdía en silencio',
     'routes_doctor.py',
     'La ruta del paciente trata la ausencia de <code>action</code> como '
     'mensaje; la del profesional no tenía esa rama. Respondía 200, no '
     'guardaba nada y no avisaba.'),

    ('P2', 'La pantalla de cargamentos reventaba con un 500',
     'routes_staff.py',
     'La plantilla pedía <code>today_str</code> para marcar los cargamentos con '
     'retraso y la ruta no lo entregaba. Lo encontró la simulación, no las '
     'pruebas.'),

    ('P2', 'Estados de la base en inglés en la cara del profesional',
     'estados.py',
     'El historial mostraba «OPEN», «pending», «attended» y «no_show» — el '
     'valor crudo de la columna. Diecinueve sitios en catorce plantillas.'),

    ('P2', 'Textos por debajo del contraste mínimo AA',
     'tests/test_smoke.py',
     'La prueba solo miraba las familias slate y gray. Por ahí se colaron '
     'cuatro textos, dos de ellos las etiquetas de «Críticos ≥7d» del personal: '
     'la cifra que cuenta pacientes esperando una semana su medicamento, en '
     'rojo pálido sobre fondo rojo pálido.'),
]


def _git(*args):
    try:
        return subprocess.run(['git'] + list(args), cwd=RAIZ, capture_output=True,
                              text=True, encoding='utf-8', timeout=30).stdout.strip()
    except Exception:
        return ''


def escapar(texto):
    return (str(texto).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))


def main():
    capturas = json.load(io.open(os.path.join(DESTINO, 'informe.json'),
                                 encoding='utf-8'))
    ruta_sim = os.path.join(DESTINO, 'simulacion.json')
    simulacion = (json.load(io.open(ruta_sim, encoding='utf-8'))
                  if os.path.exists(ruta_sim) else [])

    por_rol = {}
    for i in capturas:
        if i.get('archivo') and i['vista'] == 'escritorio':
            por_rol.setdefault(i['rol'], []).append(i)
    movil = {(i['rol'], i['ruta']): i['archivo']
             for i in capturas if i.get('archivo') and i['vista'] == 'movil'}

    # --- Pestaña 1: las pantallas ---
    pantallas = []
    for rol, (titulo, detalle) in ROLES.items():
        filas = por_rol.get(rol, [])
        if not filas:
            continue
        pantallas.append(
            '<section><h3>%s <span class="tenue">%s</span></h3><div class="rejilla">'
            % (escapar(titulo), escapar(detalle)))
        for i in filas:
            nota = ESPERADOS.get((i['rol'], i['ruta']))
            estado = '' if i['estado'] == 200 else (
                '<span class="pastilla">HTTP %s%s</span>'
                % (i['estado'], (' · ' + nota) if nota else ''))
            m = movil.get((rol, i['ruta']))
            pantallas.append(
                '<figure><a href="escritorio/%s" target="_blank">'
                '<img src="escritorio/%s" loading="lazy" alt="%s"></a>'
                '<figcaption><b>%s</b>%s<br><code>%s</code>%s</figcaption></figure>'
                % (i['archivo'], i['archivo'], escapar(i['titulo']),
                   escapar(i['titulo']), estado, escapar(i['ruta']),
                   ('<br><a class="enlace" href="movil/%s" target="_blank">'
                    'ver en móvil</a>' % m) if m else ''))
        pantallas.append('</div></section>')

    # --- Pestaña 2: la simulación ---
    sim_por_rol = {}
    for paso in simulacion:
        sim_por_rol.setdefault(paso['rol'], []).append(paso)

    pasos = []
    for rol, lista in sim_por_rol.items():
        fallos = sum(1 for x in lista if x['resultado'] != 'ok')
        pasos.append('<section><h3>%s <span class="tenue">%d acciones, %s</span></h3>'
                     % (escapar(rol.title()), len(lista),
                        'todas correctas' if not fallos else '%d con fallo' % fallos))
        pasos.append('<table><thead><tr><th>Acción</th><th>HTTP</th>'
                     '<th>Comprobación</th></tr></thead><tbody>')
        for x in lista:
            ok = x['resultado'] == 'ok'
            pasos.append(
                '<tr><td>%s</td><td><code>%s</code></td>'
                '<td class="%s">%s</td></tr>'
                % (escapar(x['accion']), x['http'] if x['http'] else '—',
                   'ok' if ok else 'mal',
                   'pasó' if ok else escapar(x.get('error', 'falló'))[:220]))
        pasos.append('</tbody></table></section>')

    # --- Pestaña 3: los hallazgos ---
    tarjetas = []
    for nivel, titulo, donde, texto in HALLAZGOS:
        tarjetas.append(
            '<article class="hallazgo n%s"><div class="cab">'
            '<span class="nivel n%s">%s</span><h3>%s</h3></div>'
            '<p class="donde"><code>%s</code></p><p>%s</p></article>'
            % (nivel, nivel, nivel, escapar(titulo), escapar(donde), texto))

    total_pasos = len(simulacion)
    fallos_sim = sum(1 for x in simulacion if x['resultado'] != 'ok')
    n_pantallas = len(capturas) // 2
    commits = _git('log', '--oneline', '-14')
    pruebas = _git('rev-parse', '--short', 'HEAD')

    html = PLANTILLA
    for clave, valor in {
        '@PANTALLAS@': '\n'.join(pantallas),
        '@PASOS@': '\n'.join(pasos),
        '@HALLAZGOS@': '\n'.join(tarjetas),
        '@N_PANTALLAS@': str(n_pantallas),
        '@N_CAPTURAS@': str(len([i for i in capturas if i.get('archivo')])),
        '@N_PASOS@': str(total_pasos),
        '@N_FALLOS@': str(fallos_sim),
        '@N_HALLAZGOS@': str(len(HALLAZGOS)),
        '@N_P0@': str(sum(1 for h in HALLAZGOS if h[0] == 'P0')),
        '@COMMITS@': escapar(commits),
        '@REVISION@': escapar(pruebas),
    }.items():
        html = html.replace(clave, valor)

    io.open(os.path.join(DESTINO, 'informe.html'), 'w', encoding='utf-8').write(html)
    print('informe generado: capturas/informe.html')
    print('  %d pantallas, %d pasos de simulación (%d fallos), %d hallazgos'
          % (n_pantallas, total_pasos, fallos_sim, len(HALLAZGOS)))


PLANTILLA = '''<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RuralHealth Connect · estado del sistema</title>
<style>
:root{--tinta:#0f172a;--tenue:#64748b;--linea:#cbd5e1;--suave:#e2e8f0;
      --fondo:#f1f5f9;--banda:#f5f5f7;--accion:#0369A1;--riesgo:#B91C1C;
      --revisar:#B45309;--completado:#047857}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--tinta);
     font:15px/1.6 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{background:#fff;border-bottom:1px solid var(--linea);padding:28px 32px}
h1{margin:0 0 4px;font-size:24px;letter-spacing:-.01em}
header p{margin:0;color:var(--tenue);font-size:14px}
main{padding:24px 32px 72px;max-width:1500px;margin:0 auto}
.cifras{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
        gap:0;border:1px solid var(--linea);border-radius:8px;background:#fff;
        margin:20px 0 28px;overflow:hidden}
.cifra{padding:14px 18px;border-right:1px solid var(--suave)}
.cifra:last-child{border-right:0}
.cifra span{display:block;font-size:11px;font-weight:700;text-transform:uppercase;
            letter-spacing:.06em;color:var(--tenue)}
.cifra b{display:block;font-size:26px;font-weight:600;margin-top:2px}
nav.tabs{display:flex;gap:0;border-bottom:1px solid var(--linea);margin-bottom:24px;
         flex-wrap:wrap}
nav.tabs button{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
   padding:10px 18px;font:inherit;font-weight:600;color:var(--tenue);cursor:pointer;
   min-height:44px}
nav.tabs button[aria-selected="true"]{color:var(--tinta);border-bottom-color:var(--accion)}
section{margin-bottom:36px}
h3{font-size:16px;margin:0 0 12px;font-weight:700}
.tenue{color:var(--tenue);font-weight:400;font-size:13px}
.rejilla{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px}
figure{margin:0;background:#fff;border:1px solid var(--linea);border-radius:8px;
       overflow:hidden}
figure img{display:block;width:100%;height:180px;object-fit:cover;object-position:top;
           border-bottom:1px solid var(--suave);background:#fff}
figcaption{padding:10px 12px;font-size:13px}
figcaption code{color:var(--tenue);font-size:11.5px}
.pastilla{display:inline-block;background:var(--banda);color:#334155;
          border:1px solid var(--linea);border-radius:6px;padding:1px 7px;
          font-size:11px;margin-left:6px}
.enlace{color:var(--accion);font-size:12px;text-decoration:none}
.enlace:hover{text-decoration:underline}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--linea);
      border-radius:8px;overflow:hidden;font-size:14px}
thead{background:var(--banda)}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
   padding:9px 12px;border-bottom:1px solid var(--linea)}
td{padding:9px 12px;border-bottom:1px solid var(--suave);vertical-align:top}
tr:last-child td{border-bottom:0}
td.ok{color:var(--completado);font-weight:600}
td.mal{color:var(--riesgo);font-weight:600}
.hallazgo{background:#fff;border:1px solid var(--linea);border-left-width:3px;
          border-radius:8px;padding:16px 18px;margin-bottom:14px}
.hallazgo.nP0{border-left-color:var(--riesgo)}
.hallazgo.nP1{border-left-color:var(--revisar)}
.hallazgo.nP2{border-left-color:var(--linea)}
.hallazgo .cab{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.hallazgo h3{margin:0;font-size:15px}
.nivel{font-size:11px;font-weight:700;border:1px solid;border-radius:6px;
       padding:1px 7px;white-space:nowrap}
.nivel.nP0{color:var(--riesgo);border-color:#fecaca;background:#fef2f2}
.nivel.nP1{color:var(--revisar);border-color:#fde68a;background:#fffbeb}
.nivel.nP2{color:var(--tenue);border-color:var(--linea);background:var(--banda)}
.donde{margin:6px 0 8px}
.donde code{font-size:12px;color:var(--tenue)}
.hallazgo p{margin:0;font-size:14px;line-height:1.62}
pre{background:#fff;border:1px solid var(--linea);border-radius:8px;padding:14px 16px;
    overflow-x:auto;font-size:12.5px;line-height:1.7}
.oculto{display:none}
footer{color:var(--tenue);font-size:12.5px;padding:0 32px 40px;max-width:1500px;
       margin:0 auto}
</style></head><body>
<header>
  <h1>RuralHealth Connect · estado del sistema</h1>
  <p>Cómo se ve, si funciona, y qué se arregló. Revisión <code>@REVISION@</code>.</p>
</header>
<main>
  <div class="cifras">
    <div class="cifra"><span>Pantallas</span><b>@N_PANTALLAS@</b></div>
    <div class="cifra"><span>Capturas</span><b>@N_CAPTURAS@</b></div>
    <div class="cifra"><span>Acciones probadas</span><b>@N_PASOS@</b></div>
    <div class="cifra"><span>Acciones con fallo</span><b>@N_FALLOS@</b></div>
    <div class="cifra"><span>Defectos corregidos</span><b>@N_HALLAZGOS@</b></div>
    <div class="cifra"><span>De ellos graves</span><b>@N_P0@</b></div>
  </div>

  <nav class="tabs" role="tablist">
    <button role="tab" aria-selected="true" data-panel="p1">Las pantallas</button>
    <button role="tab" aria-selected="false" data-panel="p2">Qué se probó</button>
    <button role="tab" aria-selected="false" data-panel="p3">Qué se arregló</button>
    <button role="tab" aria-selected="false" data-panel="p4">Historial</button>
  </nav>

  <div id="p1">@PANTALLAS@</div>

  <div id="p2" class="oculto">
    <p class="tenue" style="margin-top:0">
      Cada paso ejecuta la acción real por la ruta de la aplicación —con su token
      CSRF, su sesión y su rol— y después comprueba en la base de datos que lo que
      se pedía efectivamente ocurrió. Un formulario que responde 200 y no guarda
      nada se ve perfecto en una captura; aquí no pasa.
    </p>
    @PASOS@
  </div>

  <div id="p3" class="oculto">
    <p class="tenue" style="margin-top:0">
      Ordenados por gravedad. Cada uno con el escenario concreto que lo produce:
      un defecto sin su escenario de fallo no se puede evaluar ni priorizar.
    </p>
    @HALLAZGOS@
  </div>

  <div id="p4" class="oculto">
    <h3>Últimos commits</h3>
    <pre>@COMMITS@</pre>
  </div>
</main>
<footer>
  Generado por <code>scripts/recorrido/informe.py</code>, a partir de
  <code>capturar.py</code> (las imágenes) y <code>simular.py</code> (las acciones).
  Se regenera entero en unos minutos.
</footer>
<script>
// Pestanas sin dependencias: la pagina tiene que abrir desde el disco, sin
// servidor y sin conexion, que es como se va a mirar.
const botones = document.querySelectorAll('nav.tabs button');
botones.forEach(function (boton) {
  boton.addEventListener('click', function () {
    botones.forEach(function (otro) {
      otro.setAttribute('aria-selected', String(otro === boton));
      document.getElementById(otro.dataset.panel).classList.toggle(
        'oculto', otro !== boton);
    });
  });
});
</script>
</body></html>'''


if __name__ == '__main__':
    main()
