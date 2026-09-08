# -*- coding: utf-8 -*-
"""Recorre toda la interfaz, captura cada pantalla y detecta roturas.

Hace dos cosas a la vez:

1. Guarda una imagen por pantalla, en escritorio y en movil, para poder
   revisar todo el frontend sin crear cuentas ni navegar a mano.
2. Registra el codigo HTTP, los errores de consola del navegador y las
   trazas del servidor. Un rediseno se verifica mirando, pero tambien
   comprobando que nada dejo de funcionar.

    python scripts/recorrido/capturar.py
    python scripts/recorrido/indice.py

Deja el resultado en capturas/ (ignorada por git: se regenera).

Requiere Playwright con Chromium instalado:

    pip install playwright && playwright install chromium
"""
import io
import json
import os
import sys
import threading
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESTINO = os.path.join(RAIZ, 'capturas')
PUERTO = 5056
BASE = 'http://127.0.0.1:%d' % PUERTO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Cada entrada: (archivo, titulo legible, ruta)
PANTALLAS = {
    'publico': [
        ('01-acceso', 'Iniciar sesión', '/login'),
        ('02-registro', 'Registro de paciente', '/register'),
        ('03-recuperar-clave', 'Recuperar contraseña', '/recuperar'),
        ('04-socio', 'Ser socio', '/socio'),
        ('05-terminos', 'Términos y condiciones', '/terminos'),
        ('06-privacidad', 'Política de datos', '/privacidad'),
        ('07-aviso-privacidad', 'Aviso de privacidad', '/aviso-de-privacidad'),
        ('08-telemedicina', 'Consentimiento de telemedicina', '/telemedicina'),
        ('09-transparencia', 'Transparencia y derechos', '/transparencia'),
        ('10-sin-conexion', 'Pantalla sin conexión', '/offline'),
        # A proposito: es la unica forma de ver la pagina de error.
        ('11-error-404', 'Error 404', '/ruta-que-no-existe'),
    ],
    'pac': [
        ('20-panel-paciente', 'Panel del paciente', '/patient/dashboard'),
        ('21-consulta', 'Consulta con el profesional', '/patient/chat/1'),
        ('22-agendar', 'Agendar cita', '/patient/book_appointment/2'),
        ('23-mapa', 'Mapa de atención', '/patient/map'),
        ('24-mis-datos', 'Mis datos (habeas data)', '/privacidad-datos/'),
        ('25-pqrs', 'Peticiones, quejas y reclamos', '/pqrs/'),
        ('26-ajustes', 'Ajustes del paciente', '/settings/'),
        ('27-manual', 'Manual de uso', '/manual'),
        ('28-consentimiento-datos', 'Consentimiento de datos sensibles',
         '/patient/consentimiento-datos-sensibles'),
        ('29-consentimiento-telemedicina', 'Consentimiento de telemedicina',
         '/privacidad-datos/consentimiento-telemedicina'),
        # Da 404 cuando el profesional no cobra por su cuenta. Es correcto.
        ('2a-pago-medico', 'Pago al profesional', '/patient/doctor_payment/2'),
    ],
    'doc': [
        ('30-panel-medico', 'Panel del profesional', '/doctor/dashboard'),
        ('31-consulta-medico', 'Consulta clínica', '/doctor/chat/1'),
        ('32-historia', 'Historia clínica del paciente', '/doctor/patient_history/1'),
        ('33-prescripcion', 'Prescripción', '/doctor/prescription/1'),
        ('36-orden-impresa', 'Orden médica para imprimir', '/doctor/order/1'),
        ('34-agendar-medico', 'Agendar para un paciente', '/doctor/book_appointment/1'),
        ('35-ajustes-medico', 'Ajustes del profesional', '/settings/'),
    ],
    'adm': [
        ('40-panel-admin', 'Panel de administración', '/admin/dashboard'),
        ('41-analitica', 'Analítica', '/admin/analytics'),
        ('42-reporte-stock', 'Reporte de quiebre de stock', '/admin/reporte_stock'),
        # Da 403: el inventario es del personal, no de la administracion.
        ('43-inventario', 'Inventario', '/staff/inventory'),
        ('44-gestion-pqrs', 'Gestión de PQRS', '/pqrs/gestion'),
        ('45-solicitudes-datos', 'Solicitudes de habeas data',
         '/privacidad-datos/solicitudes'),
        ('46-ajustes-clinica', 'Ajustes de la clínica', '/settings/'),
    ],
    'sta': [
        ('50-panel-staff', 'Panel del personal', '/staff/dashboard'),
        ('51-pendientes', 'Pendientes de stock', '/staff/pendientes'),
        ('52-cargamentos', 'Cargamentos', '/staff/envios'),
        ('53-inventario-staff', 'Inventario', '/staff/inventory'),
        ('54-ticket', 'Ticket de recogida', '/staff/ticket/1'),
        ('55-mapa-orden', 'Mapa de la orden', '/staff/order/1/map'),
        ('56-consulta-staff', 'Chat de soporte', '/staff/chat/2'),
    ],
    'exp': [
        ('60-despacho', 'Despacho de medicamentos', '/expendedor/dashboard'),
    ],
    'sup': [
        ('70-superadmin', 'Panel de superadministración', '/superadmin/'),
    ],
}

# Ruido del entorno del navegador, no de la aplicacion: la extension de Jitsi
# buscando su propio icono y el registrador de Amplitude que trae la libreria
# de video. Ninguno de los dos existe en el proyecto. Se compara contra el
# texto y contra la URL, porque el mensaje util suele estar en la URL:
# el texto se queda en "Failed to load resource".
RUIDO = ('chrome-extension://', 'Amplitude Logger')

# El navegador repite en consola el codigo HTTP de la propia pagina. No es un
# error aparte del que ya registra `estado`.
ECO_DEL_ESTADO = 'the server responded with a status'


def util(mensaje, url_pagina):
    """Descarta el ruido del navegador y el eco del codigo HTTP."""
    texto, origen = mensaje
    if any(r in texto or r in origen for r in RUIDO):
        return False
    return not (ECO_DEL_ESTADO in texto and origen.startswith(url_pagina))


def arrancar_servidor():
    """Levanta la aplicacion sobre una base sembrada, en un hilo aparte."""
    import datos
    app, clave = datos.preparar()

    hilo = threading.Thread(
        target=lambda: app.run(host='127.0.0.1', port=PUERTO, debug=False,
                               use_reloader=False, threaded=True),
        daemon=True)
    hilo.start()
    time.sleep(4)
    return app, clave


def main():
    for sub in ('escritorio', 'movil'):
        os.makedirs(os.path.join(DESTINO, sub), exist_ok=True)

    _, clave = arrancar_servidor()

    from playwright.sync_api import sync_playwright

    informe = []
    with sync_playwright() as p:
        # Sin camara ni microfono, la videoconsulta no llega a pintarse y la
        # captura del chat sale a medias. Con el dispositivo simulado se ve la
        # pantalla como la ve el paciente.
        navegador = p.chromium.launch(args=[
            '--use-fake-device-for-media-stream',
            '--use-fake-ui-for-media-stream'])

        for rol, pantallas in PANTALLAS.items():
            for etiqueta, ancho, alto in (('escritorio', 1440, 900),
                                          ('movil', 390, 844)):
                ctx = navegador.new_context(
                    viewport={'width': ancho, 'height': alto},
                    device_scale_factor=1, locale='es-CO',
                    permissions=['camera', 'microphone'])
                pagina = ctx.new_page()
                consola = []
                pagina.on('console', lambda m: consola.append(
                    (m.text[:160], (m.location or {}).get('url', '')))
                    if m.type == 'error' else None)

                if rol != 'publico':
                    pagina.goto(BASE + '/login', wait_until='networkidle')
                    pagina.fill('input[name=username]', rol)
                    pagina.fill('input[name=password]', clave)
                    pagina.click('button[type=submit]')
                    pagina.wait_for_load_state('networkidle')

                for archivo, titulo, ruta in pantallas:
                    consola.clear()
                    try:
                        r = pagina.goto(BASE + ruta, wait_until='networkidle',
                                        timeout=25000)
                        estado = r.status if r else 0
                    except Exception as e:
                        informe.append({'rol': rol, 'vista': etiqueta, 'ruta': ruta,
                                        'titulo': titulo, 'estado': -1,
                                        'error': str(e)[:120]})
                        continue

                    pagina.wait_for_timeout(700)
                    destino = os.path.join(DESTINO, etiqueta,
                                           '%s-%s.png' % (archivo, rol))
                    pagina.screenshot(path=destino, full_page=True)

                    html = pagina.content()
                    informe.append({
                        'rol': rol, 'vista': etiqueta, 'ruta': ruta,
                        'titulo': titulo, 'estado': estado,
                        'archivo': os.path.basename(destino),
                        'consola': ['%s (%s)' % m for m in consola
                                    if util(m, BASE + ruta)],
                        # Una traza de Werkzeug en la pagina es un 500 disfrazado.
                        'traza': 'Traceback' in html or 'Werkzeug' in html,
                        # Una llave sin sustituir es una plantilla que no se
                        # renderizo: se ve bien en la captura y esta rota.
                        'jinja_sin_render': '{{' in html or '{%' in html,
                    })
                ctx.close()
        navegador.close()

    io.open(os.path.join(DESTINO, 'informe.json'), 'w', encoding='utf-8').write(
        json.dumps(informe, ensure_ascii=False, indent=2))

    # Los 404 y 403 esperados no cuentan como rotura: estan documentados arriba.
    esperados = {('publico', '/ruta-que-no-existe'), ('adm', '/staff/inventory'),
                 ('pac', '/patient/doctor_payment/2')}
    malos = [i for i in informe
             if (i['estado'] != 200 and (i['rol'], i['ruta']) not in esperados)
             or i.get('traza') or i.get('jinja_sin_render') or i.get('consola')]

    print('capturas: %d' % len([i for i in informe if i.get('archivo')]))
    print('con problema: %d' % len(malos))
    for i in malos:
        print('  %-4s %-11s %-34s estado=%s traza=%s jinja=%s consola=%s'
              % (i['rol'], i['vista'], i['ruta'], i['estado'], i.get('traza'),
                 i.get('jinja_sin_render'), (i.get('consola') or [''])[0][:60]))
    return informe


if __name__ == '__main__':
    main()
