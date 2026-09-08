"""Recorrido de humo: que cada pantalla renderice para el rol que la usa.

Las pruebas unitarias verifican lógica; estas verifican que las plantillas no
estén rotas. Un error de Jinja en una vista clínica es una pantalla en blanco
delante de un paciente, y no lo detecta ninguna prueba de lógica.
"""

import pytest

from conftest import VALID_PASSWORD


@pytest.fixture
def populated(app, make_user, make_stock):
    """Un conjunto mínimo de datos para que las vistas tengan qué mostrar."""
    from models import (
        Appointment, Chat, MedicalOrder, MedicationPickupTicket, Message,
        PatientAllergy, Pharmacy, db,
    )
    from security import generate_pickup_hash, generate_signed_order_hash
    from time_utils import colombia_now
    from datetime import timedelta

    users = {
        'patient': make_user(role='patient', username='smoke_pac', name='Paciente Prueba'),
        'doctor': make_user(role='doctor', username='smoke_doc', name='Doctora Prueba',
                            medical_registration='RM-SMOKE',
                            signature_path='clinic_1/firma.png'),
        'staff': make_user(role='staff', username='smoke_staff'),
        'expendedor': make_user(role='expendedor', username='smoke_exp'),
        'admin': make_user(role='admin', username='smoke_admin'),
        'super': make_user(role='super', username='smoke_super'),
    }

    make_stock('Amoxicilina', 100)
    make_stock('Paracetamol', 50)

    with app.app_context():
        pharmacy = Pharmacy.query.filter_by(clinic_id=1).first()
        now = colombia_now()

        chat = Chat(clinic_id=1, patient_id=users['patient'].id,
                    doctor_id=users['doctor'].id, status='open', reason='Dolor de cabeza')
        db.session.add(chat)
        db.session.flush()
        db.session.add(Message(clinic_id=1, chat_id=chat.id,
                               sender_id=users['patient'].id, content='Buenos dias'))

        db.session.add(Appointment(
            clinic_id=1, patient_id=users['patient'].id, doctor_id=users['doctor'].id,
            date=now.strftime('%Y-%m-%d'), time='10:00', status='pending',
        ))

        db.session.add(PatientAllergy(
            clinic_id=1, patient_id=users['patient'].id, substance='Penicilina',
            substance_normalized='penicilina', severity='grave', status='confirmada',
            recorded_by_id=users['doctor'].id,
        ))

        meds_json = '[{"nombre_med": "Paracetamol", "cantidad": 20, "unidad": "tableta"}]'
        expires = now + timedelta(days=30)
        order_hash = generate_signed_order_hash(
            users['doctor'].id, users['patient'].id, meds_json, 1,
            issued_at=now.isoformat(), expires_at=expires.isoformat(),
        )
        order = MedicalOrder(
            clinic_id=1, order_number='OM-1-00001',
            doctor_id=users['doctor'].id, patient_id=users['patient'].id,
            meds_json=meds_json, diagnosis_json='[{"code": "Z000", "description": "General"}]',
            verification_hash=order_hash, hash_seguridad=order_hash,
            doctor_registration='RM-SMOKE', signed_at=now,
            status='pendiente', created_at=now, expires_at=expires, fecha_expiracion=expires,
        )
        db.session.add(order)
        db.session.flush()

        ticket = MedicationPickupTicket(
            clinic_id=1, order_id=order.id, pharmacy_id=pharmacy.id,
            patient_id=users['patient'].id, staff_id=users['staff'].id,
            pickup_code='SMOKE1234567', pickup_hash='pendiente',
            meds_json=meds_json, pickup_date=now.strftime('%Y-%m-%d'),
            pickup_time='11:00', status='autorizado',
        )
        db.session.add(ticket)
        db.session.flush()
        ticket.pickup_hash = generate_pickup_hash(
            ticket.id, ticket.order_id, ticket.patient_id, ticket.clinic_id, ticket.pickup_code
        )
        db.session.commit()

        users['chat_id'] = chat.id
        users['order_id'] = order.id
        users['ticket_id'] = ticket.id

    return users


# Rutas públicas: deben responder sin sesión.
PUBLIC_ROUTES = [
    '/login', '/register', '/terminos', '/privacidad', '/transparencia',
    '/socio', '/offline', '/recuperar', '/health', '/ready', '/telemedicina',
    '/manifest.json', '/service-worker.js',
]


@pytest.mark.parametrize('path', PUBLIC_ROUTES)
def test_public_routes_render(client, path):
    response = client.get(path)
    assert response.status_code == 200, f'{path} devolvio {response.status_code}'


# Rutas por rol. El valor es una plantilla de ruta con marcadores que se
# sustituyen con los identificadores creados en el fixture.
ROLE_ROUTES = {
    'patient': [
        '/patient/dashboard',
        '/patient/map',
        '/patient/api/map_points',
        '/patient/api/offline_agenda',
        '/patient/chat/{chat_id}',
        '/settings/',
        '/privacidad-datos/',
        '/manual',
    ],
    'doctor': [
        '/doctor/dashboard',
        '/doctor/patient_history/{patient_id}',
        '/doctor/prescription/{patient_id}',
        '/doctor/chat/{chat_id}',
        '/doctor/order/{order_id}',
        '/settings/',
        '/privacidad-datos/',
    ],
    'staff': [
        '/staff/dashboard',
        '/staff/inventory',
        '/staff/pendientes',
        '/staff/envios',
        '/staff/ticket/{ticket_id}',
        '/settings/',
    ],
    'expendedor': [
        '/expendedor/dashboard',
        '/settings/',
    ],
    'admin': [
        '/admin/dashboard',
        '/admin/analytics',
        '/admin/api/analytics_data',
        '/admin/reporte_stock',
        '/admin/auditoria',
        '/admin/rips/validar',
        '/privacidad-datos/solicitudes',
        '/settings/',
    ],
    'super': [
        '/superadmin/',
        '/settings/',
    ],
}


@pytest.mark.parametrize(
    'role,path',
    [(role, path) for role, paths in ROLE_ROUTES.items() for path in paths],
)
def test_authenticated_routes_render(app, client, populated, role, path):
    """Cada pantalla debe renderizar sin error de plantilla ni excepción."""
    suffixes = {
        'patient': 'pac', 'doctor': 'doc', 'staff': 'staff',
        'expendedor': 'exp', 'admin': 'admin', 'super': 'super',
    }
    username = f'smoke_{suffixes[role]}'

    login = client.post('/login', data={'username': username, 'password': VALID_PASSWORD})
    assert login.status_code == 302, f'no se pudo iniciar sesion como {role}'

    resolved = path.format(
        chat_id=populated['chat_id'],
        order_id=populated['order_id'],
        ticket_id=populated['ticket_id'],
        patient_id=populated['patient'].id,
        doctor_id=populated['doctor'].id,
    )

    response = client.get(resolved)
    assert response.status_code == 200, (
        f'{role} -> {resolved} devolvio {response.status_code}'
    )


def test_no_server_error_page_leaks_internals(app, client, populated):
    """La página de error no debe exponer trazas ni estructura interna.

    Se buscan marcadores propios de una traza de Python, no palabras sueltas:
    cadenas como "line " aparecen legítimamente en las clases de CSS.
    """
    client.post('/login', data={'username': 'smoke_pac', 'password': VALID_PASSWORD})

    for path in ('/patient/chat/999999', '/doctor/order/999999', '/no-existe-esta-ruta'):
        body = client.get(path).get_data(as_text=True).lower()
        for leaked in ('traceback (most recent call last)', 'sqlalchemy.',
                       'werkzeug.exceptions', 'file "', '.py", line'):
            assert leaked not in body, f'{path} expone "{leaked}"'


def test_internal_error_shows_incident_code_not_trace(app, client, populated):
    """Ante un fallo inesperado, al usuario le llega un código, no la traza.

    El código permite a soporte localizar la traza en el registro sin que la
    estructura interna —ni los datos que hubiera en memoria— salgan a pantalla.
    """
    @app.route('/_fallo_deliberado')
    def deliberate_failure():
        raise RuntimeError('fallo de prueba con dato sensible: cedula 1098765432')

    client.post('/login', data={'username': 'smoke_pac', 'password': VALID_PASSWORD})
    response = client.get('/_fallo_deliberado')

    assert response.status_code == 500
    body = response.get_data(as_text=True)
    assert '1098765432' not in body, 'no debe filtrarse el contenido de la excepcion'
    assert 'RuntimeError' not in body
    assert 'codigo' in body.lower(), 'debe ofrecerse un identificador de incidente'


# =============================================================================
# Robustez del HTML servido
# =============================================================================

class TestFormsWorkWithoutJavaScript:
    """Los formularios no deben depender de JavaScript para el token CSRF.

    La aplicacion lo inyectaba con un script al cargar la pagina. Eso deja toda
    operacion de escritura supeditada a que ese script se ejecute: si el JS no
    carga —conexion intermitente, navegador antiguo—, cada envio devuelve un 400
    de CSRF y el usuario ve un formulario que aparentemente no hace nada.
    """

    def test_every_post_form_carries_its_token(self):
        import glob
        import os
        import re

        sin_token = []
        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            for m in re.finditer(r'<form\b[^>]*method\s*=\s*["\']post["\'][^>]*>', src, re.I):
                cierre = src.lower().find('</form>', m.end())
                cuerpo = src[m.end():cierre if cierre != -1 else len(src)]
                if '_csrf_token' not in cuerpo:
                    linea = src[:m.start()].count('\n') + 1
                    sin_token.append(f'{os.path.basename(path)}:{linea}')

        assert not sin_token, (
            'formularios POST que dependerian del JavaScript para funcionar: '
            f'{sin_token}'
        )

    def test_rendered_login_form_has_token(self, client):
        cuerpo = client.get('/login').get_data(as_text=True)
        assert 'name="_csrf_token"' in cuerpo


class TestAccessibleMarkup:

    def test_form_fields_have_accessible_names(self):
        """Un campo sin nombre se anuncia como "campo de texto, en blanco".

        Se admiten las tres formas válidas de nombrarlo: `<label for>` explícita,
        `aria-label`, y la etiqueta implícita —el campo dentro de un `<label>`—,
        que es igual de válida para un lector de pantalla.
        """
        import glob
        import os
        import re

        sin_nombre = []
        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            etiquetados = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', src))

            # Rangos de texto que quedan dentro de un <label>...</label>.
            rangos_label = [
                (m.start(), m.end())
                for m in re.finditer(r'<label\b[^>]*>.*?</label>', src, re.I | re.S)
            ]

            for m in re.finditer(r'<(input|select|textarea)\b((?:[^<>"]|"[^"]*")*?)/?>',
                                 src, re.I):
                attrs = m.group(2)
                if re.search(r'type="(hidden|submit|button)"', attrs, re.I):
                    continue
                ident = re.search(r'\bid="([^"]+)"', attrs)
                if ident and ident.group(1) in etiquetados:
                    continue
                if 'aria-label' in attrs or 'aria-labelledby' in attrs:
                    continue
                # Etiqueta implícita.
                if any(inicio < m.start() < fin for inicio, fin in rangos_label):
                    continue
                nombre = re.search(r'name="([^"]+)"', attrs)
                sin_nombre.append(
                    f'{os.path.basename(path)}:{nombre.group(1) if nombre else "?"}'
                )

        assert not sin_nombre, f'campos sin nombre accesible: {sin_nombre}'

    def test_no_text_below_minimum_size(self):
        """Nada por debajo de 12px: se usa al sol, en pantallas baratas."""
        import glob
        import re

        diminutos = []
        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            for hallazgo in re.findall(r'text-\[(\d+)px\]', src):
                if int(hallazgo) < 12:
                    diminutos.append(f'{path}: {hallazgo}px')
        assert not diminutos, f'texto por debajo del minimo legible: {diminutos}'

    def test_no_insufficient_contrast_on_text(self):
        """text-slate-400 sobre blanco da 2.56:1; AA exige 4.5:1.

        Vale para cualquier familia: el tono 300 o 400 de cualquier color de
        Tailwind se queda por debajo de AA sobre fondo claro. Antes solo se
        miraba slate y gray, y por ahi se colo un `text-indigo-300` en la
        pista del pad de firma que no se leia.
        """
        import glob
        import re

        FAMILIAS = ('slate|gray|zinc|neutral|stone|red|orange|amber|yellow|'
                    'lime|green|emerald|teal|cyan|sky|blue|indigo|violet|'
                    'purple|fuchsia|pink|rose')
        # Sobre fondo oscuro un tono claro es lo correcto, no un fallo.
        OSCURO = re.compile(r'bg-(?:accent|critical|caution|positive|primary|'
                            r'medical|black|slate-[6-9]00|[a-z]+-[6-9]00)')

        malos = []
        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            for etiqueta in re.findall(r'<[a-zA-Z][^>]*>', src):
                if etiqueta.startswith('<i ') or 'data-lucide' in etiqueta:
                    continue   # los iconos no transmiten texto
                if OSCURO.search(etiqueta):
                    continue
                if re.search(r'(?<!placeholder:)text-(?:%s)-[34]00' % FAMILIAS,
                             etiqueta):
                    malos.append(f'{path}: {etiqueta[:70]}')
        assert not malos, f'contraste por debajo de AA: {malos[:10]}'

    def test_external_links_are_isolated(self):
        """target=_blank sin rel deja window.opener a la pagina destino."""
        import glob
        import re

        expuestos = []
        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            for enlace in re.findall(r'<a\b[^>]*target="_blank"[^>]*>', src):
                if 'noopener' not in enlace:
                    expuestos.append(f'{path}: {enlace[:60]}')
        assert not expuestos, f'enlaces sin noopener: {expuestos}'

    def test_images_have_alt(self):
        import glob
        import re

        sin_alt = []
        for path in glob.glob('templates/*.html'):
            for img in re.findall(r'<img\b[^>]*>', open(path, encoding='utf-8').read()):
                if 'alt=' not in img:
                    sin_alt.append(f'{path}: {img[:60]}')
        assert not sin_alt, f'imagenes sin alt: {sin_alt}'


class TestThirdPartyResources:
    """Ningún recurso de la interfaz debe depender de un servidor externo.

    La aplicación cargaba Tailwind desde `cdn.tailwindcss.com`. Cuando ese CDN
    no era alcanzable —red rural, cortafuegos, bloqueo regional— la interfaz
    aparecía **sin ningún estilo**: no se degradaba, se rompía. En una aplicación
    para zonas de baja conectividad eso no es un caso raro, es el esperado.

    Ahora el CSS, los iconos, los mapas, las gráficas y la tipografía se sirven
    desde el propio servidor. Esta prueba impide que vuelva a colarse un CDN.
    """

    # Únicos externos admitidos, y ambos necesitan conexión por naturaleza:
    # la videollamada y las teselas del mapa.
    ORIGENES_PERMITIDOS = ('meet.jit.si', 'jitsi.net', '8x8.vc',
                           'tile.openstreetmap.org')

    def test_no_external_stylesheets_or_scripts(self):
        import glob
        import os
        import re

        externos = []
        patron = re.compile(
            r'<(?:link|script)[^>]*?(?:src|href)="(https?://[^"]+)"', re.I)

        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            for m in patron.finditer(src):
                url = m.group(1)
                if any(ok in url for ok in self.ORIGENES_PERMITIDOS):
                    continue
                externos.append(f'{os.path.basename(path)}: {url}')

        assert not externos, (
            'la interfaz volveria a romperse sin acceso a estos servidores: '
            f'{externos}'
        )

    def test_built_stylesheet_is_versioned(self):
        """El CSS generado debe estar en el repositorio.

        Sin él, ejecutar la aplicación exigiría Node y conexión — justo lo que
        se quería evitar.
        """
        import os
        assert os.path.isfile('static/css/app.css'), (
            'falta static/css/app.css; generalo con: npm run build:css'
        )
        assert os.path.getsize('static/css/app.css') > 10000, (
            'el CSS generado parece vacio o incompleto'
        )

    def test_design_tokens_are_in_the_stylesheet(self):
        """Las clases del sistema visual deben existir en el CSS generado.

        Tailwind solo emite las clases que encuentra usadas: si el build no
        escanea las plantillas correctas, faltarían sin previo aviso.
        """
        css = open('static/css/app.css', encoding='utf-8').read()
        for clase in ('bg-accent', 'text-critical', 'bg-caution-subtle',
                      'text-positive', 'border-critical-border'):
            assert clase in css, f'falta la clase {clase} en el CSS generado'

    def test_vendored_libraries_exist(self):
        import os
        for archivo in ('static/vendor/lucide.min.js',
                        'static/vendor/leaflet.js',
                        'static/vendor/leaflet.css',
                        'static/vendor/chart.umd.min.js',
                        'static/css/fonts.css'):
            assert os.path.isfile(archivo), f'falta {archivo}'

    def test_service_worker_precaches_the_interface(self):
        """Sin conexión la interfaz debe seguir teniendo estilos."""
        sw = open('static/service-worker.js', encoding='utf-8').read()
        for recurso in ('/static/css/app.css', '/static/vendor/lucide.min.js'):
            assert recurso in sw, f'{recurso} no se precachea'


class TestEstadosEnEspanol:
    """La base guarda los estados en inglés; la pantalla no debe mostrarlos así.

    El profesional veía «OPEN» sobre una consulta abierta y «no_show» cuando el
    paciente no llegó. Se traduce al mostrar, sin tocar el valor almacenado:
    los estados también viajan al RIPS y al IHCE, y cambiarlos en la base
    obligaría a migrar datos.
    """

    def test_traduce_los_estados_conocidos(self):
        from estados import traducir

        assert traducir('open') == 'Abierta'
        assert traducir('no_show') == 'No asistió'
        assert traducir('sin_stock') == 'Sin existencias'
        assert traducir('in_progress') == 'En curso'

    def test_no_distingue_mayusculas_ni_espacios(self):
        from estados import traducir

        assert traducir(' Attended ') == 'Atendida'

    def test_un_estado_desconocido_se_muestra_tal_cual(self):
        """Un estado invisible es peor que uno sin traducir."""
        from estados import traducir

        assert traducir('estado_nuevo_sin_traducir') == 'estado_nuevo_sin_traducir'
        assert traducir(None) == ''

    def test_ninguna_plantilla_imprime_un_estado_crudo(self):
        import glob
        import re

        crudos = []
        for path in glob.glob('templates/**/*.html', recursive=True):
            src = open(path, encoding='utf-8').read()
            for hallazgo in re.findall(r'\{\{\s*[a-z_]+(?:\.[a-z_]+)*\.status\s*'
                                       r'(?:\|[^}]*)?\}\}', src):
                if '|estado' not in hallazgo:
                    crudos.append(f'{path}: {hallazgo}')
        assert not crudos, f'estados sin traducir en pantalla: {crudos}'
