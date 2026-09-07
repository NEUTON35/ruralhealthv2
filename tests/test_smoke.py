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
    '/socio', '/offline', '/recuperar', '/health', '/ready',
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
        """Un campo sin nombre se anuncia como "campo de texto, en blanco"."""
        import glob
        import os
        import re

        sin_nombre = []
        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            etiquetados = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', src))
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
        """text-slate-400 sobre blanco da 2.56:1; AA exige 4.5:1."""
        import glob
        import re

        malos = []
        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            for etiqueta in re.findall(r'<[a-zA-Z][^>]*>', src):
                if etiqueta.startswith('<i ') or 'data-lucide' in etiqueta:
                    continue   # los iconos no transmiten texto
                if re.search(r'(?<!placeholder:)text-(slate|gray)-[34]00', etiqueta):
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
    """Todo recurso externo debe ir con version fijada y verificacion de integridad.

    Sin `integrity`, un CDN comprometido —o un intermediario— puede servir
    JavaScript arbitrario que se ejecuta en paginas con historia clinica abierta.
    `lucide@latest` era exactamente ese caso: version no fijada y sin verificar.
    """

    # Excepcion conocida y unica. El script Play de Tailwind compila el CSS en el
    # navegador en tiempo de ejecucion, asi que su contenido no es estable y no
    # admite SRI. Su propia documentacion desaconseja usarlo en produccion.
    # Remedio pendiente: generar el CSS en el build y servirlo desde /static.
    EXCEPCIONES = ('cdn.tailwindcss.com',)

    def test_external_resources_are_pinned_and_verified(self):
        import glob
        import re

        problemas = []
        patron = re.compile(r'<(?:link|script)\b[^>]*?(?:src|href)="(https?://[^"]+)"[^>]*>', re.I)

        for path in glob.glob('templates/*.html'):
            src = open(path, encoding='utf-8').read()
            for m in patron.finditer(src):
                url, etiqueta = m.group(1), m.group(0)

                # Las fuentes de Google se cargan como hoja de estilo sin JS.
                if 'fonts.googleapis.com' in url or 'fonts.gstatic.com' in url:
                    continue
                if any(exc in url for exc in self.EXCEPCIONES):
                    continue

                if '@latest' in url or re.search(r'@\^|@~', url):
                    problemas.append(f'{path}: version no fijada -> {url}')
                if 'integrity=' not in etiqueta:
                    problemas.append(f'{path}: sin SRI -> {url}')

        assert not problemas, '\n'.join(problemas)

    def test_no_new_unverifiable_cdn_is_added(self):
        """La lista de excepciones no debe crecer sin una decision explicita."""
        assert self.EXCEPCIONES == ('cdn.tailwindcss.com',), (
            'se anadio un recurso externo que no puede verificarse; '
            'documenta por que antes de ampliar la excepcion'
        )
