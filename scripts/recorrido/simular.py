# -*- coding: utf-8 -*-
"""Ejecuta la aplicacion entera, rol por rol y funcion por funcion.

El recorrido de capturas mira las pantallas. Esto hace lo otro: pulsa los
botones. Entra como cada uno de los siete roles y ejecuta las acciones reales
—agendar, prescribir, dispensar, exportar datos, radicar una queja— y despues
comprueba en la base de datos que lo que se pedia efectivamente ocurrio.

La diferencia importa. Un formulario que responde 200 y no guarda nada se ve
perfecto en una captura. Aqui se detecta, porque cada paso declara que tiene
que haber cambiado.

    python scripts/recorrido/simular.py

Deja `capturas/simulacion.json` con un asiento por paso: rol, accion, codigo
HTTP, si la comprobacion posterior paso, y el detalle del fallo si lo hubo.

Cada paso corre aislado: si uno falla, se registra y se sigue. Lo que interesa
es el inventario completo de lo que no funciona, no el primer tropiezo.
"""
import io
import json
import os
import sys
import tempfile
import traceback
from datetime import timedelta

from flask.testing import FlaskClient

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)

DESTINO = os.path.join(RAIZ, 'capturas')
CLAVE = 'Rural-Health-2026#Seg'

informe = []


class Paso:
    """Un paso de la simulacion: se ejecuta, se comprueba y se registra.

    El `assert` va dentro del `with`, no fuera: asi un fallo de comprobacion
    queda con el mismo formato que un error inesperado, y ninguno de los dos
    detiene el recorrido.
    """

    def __init__(self, rol, accion, detalle=''):
        self.rol = rol
        self.accion = accion
        self.detalle = detalle
        self.estado = None

    def __enter__(self):
        return self

    def __exit__(self, tipo, valor, tb):
        entrada = {'rol': self.rol, 'accion': self.accion,
                   'detalle': self.detalle, 'http': self.estado}
        if tipo is None:
            entrada['resultado'] = 'ok'
        else:
            entrada['resultado'] = 'fallo'
            entrada['error'] = '%s: %s' % (tipo.__name__, str(valor)[:400])
            entrada['traza'] = ''.join(
                traceback.format_exception(tipo, valor, tb))[-1200:]
        informe.append(entrada)
        # Se traga la excepcion: el recorrido continua.
        return True


class ClienteConCSRF(FlaskClient):
    """Aporta el token CSRF en cada envio, como lo hace un navegador.

    La proteccion no se desactiva para simular: hacerlo dejaria sin ejercitar
    el control que protege cada formulario, y la simulacion pasaria sobre un
    comportamiento distinto del real.
    """

    def open(self, *args, **kwargs):
        metodo = (kwargs.get('method')
                  or (args[1] if len(args) > 1 else 'GET')).upper()
        if metodo in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            with self.session_transaction() as sesion:
                token = sesion.get('_csrf_token')
                if not token:
                    token = 'token-de-simulacion-' + 'a' * 16
                    sesion['_csrf_token'] = token
            datos = kwargs.get('data')
            if isinstance(datos, dict) and '_csrf_token' not in datos:
                datos['_csrf_token'] = token
            cabeceras = dict(kwargs.get('headers') or {})
            cabeceras.setdefault('X-CSRFToken', token)
            kwargs['headers'] = cabeceras
        return super().open(*args, **kwargs)


def entrar(cliente, usuario, clave=CLAVE):
    """Inicia sesion por el formulario real."""
    return cliente.post('/login',
                        data={'username': usuario, 'password': clave},
                        follow_redirects=False)


def preparar():
    """Base desechable con los siete roles y contenido en cada pantalla."""
    import datos as sembrador
    app, clave = sembrador.preparar()
    app.test_client_class = ClienteConCSRF
    return app, clave


def simular():
    app, clave = preparar()

    from models import (Appointment, Chat, Clinic, MedicalHistory, MedicalOrder,
                        MedicationPickupTicket, Message, Notification, Pharmacy,
                        ServiceComplaint, Stock, StockReservation, User, db)

    def cliente_de(usuario):
        c = app.test_client()
        entrar(c, usuario)
        return c

    with app.app_context():
        paciente = User.query.filter_by(username='pac').first()
        ids = {
            # La clinica del escenario no es necesariamente la 1: el arranque
            # crea una clinica base antes de la sembrada. Fijar el 1 a mano
            # creaba los datos en otra clinica y el filtro de aislamiento,
            # correctamente, no los dejaba ver.
            'clinica': paciente.clinic_id,
            'paciente': paciente.id,
            'doctor': User.query.filter_by(username='doc').first().id,
            'staff': User.query.filter_by(username='sta').first().id,
            'chat': Chat.query.filter_by(mode='consultation').first().id,
            'orden': MedicalOrder.query.first().id,
            'ticket': MedicationPickupTicket.query.first().id,
            'farmacia': Pharmacy.query.first().id,
        }

    # ================= PACIENTE =================
    pac = cliente_de('pac')

    with Paso('paciente', 'Fuera del horario no se puede escribir') as p:
        # El profesional del escenario atiende de 07:00 a 17:00. Que la
        # aplicacion rechace el mensaje fuera de esa franja es lo correcto: el
        # paciente tiene que saber que nadie va a leerlo ahora.
        with app.app_context():
            # El horario que decide si se puede escribir vive en
            # `DoctorSchedule`, no en `atencion_inicio` del usuario.
            from models import DoctorSchedule
            for franja in DoctorSchedule.query.filter_by(
                    doctor_id=ids['doctor']).all():
                franja.start_time, franja.end_time = '23:58', '23:59'
            db.session.commit()
            antes = Message.query.filter_by(chat_id=ids['chat']).count()
        r = pac.post('/patient/chat/%d' % ids['chat'],
                     data={'content': 'Escribo de madrugada.'},
                     follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = Message.query.filter_by(chat_id=ids['chat']).count()
        assert ahora == antes, 'se acepto un mensaje fuera del horario'

    with Paso('paciente', 'Enviar un mensaje en la consulta') as p:
        with app.app_context():
            from models import DoctorSchedule
            from time_utils import colombia_now
            profesional = db.session.get(User, ids['doctor'])
            profesional.is_available = True
            hoy = colombia_now().weekday()
            franja = DoctorSchedule.query.filter_by(
                doctor_id=ids['doctor'], day_of_week=hoy).first()
            if franja is None:
                franja = DoctorSchedule(clinic_id=ids['clinica'],
                                        doctor_id=ids['doctor'],
                                        day_of_week=hoy, is_available=True)
                db.session.add(franja)
            franja.start_time, franja.end_time = '00:00', '23:59'
            franja.is_available = True
            db.session.commit()
            antes = Message.query.filter_by(chat_id=ids['chat']).count()
        r = pac.post('/patient/chat/%d' % ids['chat'],
                     data={'content': 'Doctor, sigo con el dolor de cabeza.'},
                     follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = Message.query.filter_by(chat_id=ids['chat']).count()
        assert ahora > antes, 'el mensaje no se guardo'

    with Paso('paciente', 'Agendar una cita') as p:
        from time_utils import colombia_now
        fecha = (colombia_now() + timedelta(days=3)).strftime('%Y-%m-%d')
        r = pac.post('/patient/book_appointment/%d' % ids['doctor'],
                     data={'date': fecha, 'time': '09:00',
                           'appointment_type': 'Consulta',
                           'description': 'Control de rutina'},
                     follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            existe = Appointment.query.filter_by(date=fecha, time='09:00').first()
        assert existe is not None, 'la cita no quedo registrada'

    with Paso('paciente', 'Radicar una PQRS') as p:
        r = pac.post('/pqrs/radicar', data={
            'complaint_type': 'queja', 'subject': 'Demora en la entrega',
            'detail': 'Fui dos veces a la farmacia y no habia existencias.',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            total = ServiceComplaint.query.count()
        assert total >= 2, 'la PQRS no se radico (hay %d)' % total

    with Paso('paciente', 'Exportar mis datos (habeas data)') as p:
        r = pac.post('/privacidad-datos/exportar', follow_redirects=False)
        p.estado = r.status_code
        assert r.status_code in (200, 302), 'la exportacion no respondio'
        if r.status_code == 200:
            assert len(r.data) > 200, 'el archivo exportado esta vacio'

    with Paso('paciente', 'Cambiar la contrasena propia') as p:
        nueva = 'Rural-Health-2027#Otra'
        r = pac.post('/settings/', data={
            'action': 'change_password', 'current_password': clave,
            'new_password': nueva, 'confirm_password': nueva,
        }, follow_redirects=True)
        p.estado = r.status_code
        # La sesion propia tiene que sobrevivir al cambio.
        r2 = pac.get('/patient/dashboard')
        assert r2.status_code == 200, 'cambiar la clave cerro la propia sesion'
        # Y se deja como estaba, para no romper los pasos siguientes.
        pac.post('/settings/', data={
            'action': 'change_password', 'current_password': nueva,
            'new_password': clave, 'confirm_password': clave,
        }, follow_redirects=True)

    # ================= PROFESIONAL =================
    doc = cliente_de('doc')

    with Paso('profesional', 'Sin firma registrada no se puede prescribir') as p:
        # La firma es requisito de la Resolucion 1403 de 2007: una orden sin
        # firma del profesional no tiene validez.
        with app.app_context():
            antes = MedicalOrder.query.count()
        from time_utils import colombia_now
        r = doc.post('/doctor/prescription/%d' % ids['paciente'], data={
            'generic_name': 'Acetaminofen', 'concentration': '500 mg',
            'dosage_form': 'tableta', 'route': 'oral', 'dosage': '1 tableta',
            'frequency': 'cada 8 horas', 'duration_days': '3',
            'quantity': '9', 'unit': 'tabletas',
            'dx_code': 'Z000', 'dx_description': 'Examen medico general',
            'expires_date': (colombia_now() + timedelta(days=30)).strftime('%Y-%m-%d'),
            'expires_time': '23:59', 'patient_level': '1',
            'care_modality': 'presencial',
        }, follow_redirects=False)
        p.estado = r.status_code
        with app.app_context():
            assert MedicalOrder.query.count() == antes, (
                'se emitio una orden sin firma del profesional')
            # Se registra la firma para los pasos siguientes.
            db.session.get(User, ids['doctor']).signature_path = 'clinic_1/firma.png'
            db.session.commit()

    with Paso('profesional', 'Responder en la consulta') as p:
        # El chat del profesional exige `action`; el del paciente trata la
        # ausencia como mensaje. La asimetria esta documentada abajo.
        r = doc.post('/doctor/chat/%d' % ids['chat'],
                     data={'action': 'send_message',
                           'content': 'Cuenteme si el dolor es pulsatil.'},
                     follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            del_doctor = Message.query.filter_by(
                chat_id=ids['chat'], sender_id=ids['doctor']).count()
        assert del_doctor >= 2, 'la respuesta del profesional no se guardo'

    with Paso('profesional', 'Registrar la historia clinica') as p:
        with app.app_context():
            antes = MedicalHistory.query.count()
        r = doc.post('/doctor/chat/%d' % ids['chat'], data={
            'action': 'save_history',
            'summary': 'Cefalea tensional. Se indica analgesia y control.',
            'diagnosis': 'Cefalea tensional',
            'cie10_code': 'Z000', 'cups_code': '890201',
            'external_cause': '26', 'consultation_purpose': '15',
            'care_modality': '01',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = MedicalHistory.query.count()
        assert ahora > antes, 'la historia clinica no se guardo'

    with Paso('profesional', 'Prescribir por denominacion comun') as p:
        with app.app_context():
            antes = MedicalOrder.query.count()
        from time_utils import colombia_now
        r = doc.post('/doctor/prescription/%d' % ids['paciente'], data={
            'generic_name': 'Acetaminofen', 'med_name': '',
            'concentration': '500 mg', 'dosage_form': 'tableta',
            'route': 'oral', 'dosage': '1 tableta',
            'frequency': 'cada 8 horas', 'duration_days': '3',
            'quantity': '9', 'unit': 'tabletas',
            'instructions': 'Con alimentos',
            'dx_code': 'Z000', 'dx_description': 'Examen medico general',
            'expires_date': (colombia_now() + timedelta(days=30)).strftime('%Y-%m-%d'),
            'expires_time': '23:59', 'patient_level': '1',
            'care_modality': 'presencial',
        }, follow_redirects=False)
        p.estado = r.status_code
        with app.app_context():
            ahora = MedicalOrder.query.count()
        assert ahora > antes, (
            'la orden no se emitio prescribiendo solo por principio activo')

    with Paso('profesional', 'La alergia bloquea la prescripcion') as p:
        with app.app_context():
            from models import PatientAllergy
            db.session.add(PatientAllergy(
                clinic_id=ids['clinica'], patient_id=ids['paciente'],
                substance='Penicilina', substance_normalized='penicilina',
                reaction='Anafilaxia', severity='anafilaxia',
                status='confirmada'))
            db.session.commit()
            antes = MedicalOrder.query.count()
        from time_utils import colombia_now
        r = doc.post('/doctor/prescription/%d' % ids['paciente'], data={
            'generic_name': 'Amoxicilina', 'med_name': 'Amoxal',
            'concentration': '500 mg', 'dosage_form': 'capsula',
            'route': 'oral', 'dosage': '1 capsula',
            'frequency': 'cada 8 horas', 'duration_days': '7',
            'quantity': '21', 'unit': 'capsulas', 'instructions': '',
            'dx_code': 'Z000', 'dx_description': 'Examen medico general',
            'expires_date': (colombia_now() + timedelta(days=30)).strftime('%Y-%m-%d'),
            'expires_time': '23:59', 'patient_level': '1',
            'care_modality': 'presencial',
        }, follow_redirects=False)
        p.estado = r.status_code
        with app.app_context():
            ahora = MedicalOrder.query.count()
        assert ahora == antes, (
            'se emitio una orden de un betalactamico a un paciente con '
            'alergia a la penicilina confirmada, escrita con nombre comercial')

    with Paso('profesional', 'Anular una orden') as p:
        r = doc.post('/doctor/order/%d/anular' % ids['orden'],
                     data={'reason': 'Dosis incorrecta, se reemite corregida.',
                           'annul_reason': 'Dosis incorrecta, se reemite corregida.'},
                     follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            orden = db.session.get(MedicalOrder, ids['orden'])
            estado = orden.status
        assert estado == 'anulada', 'la orden no quedo anulada (%s)' % estado

    # ================= PERSONAL / RECEPCION =================
    sta = cliente_de('sta')

    with Paso('personal', 'Agregar un medicamento al inventario') as p:
        r = sta.post('/staff/inventory', data={
            'pharmacy_id': ids['farmacia'], 'nombre_med': 'Ibuprofeno 400mg',
            'cantidad': '80', 'unidad': 'tabletas', 'cantidad_minima': '20',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            fila = Stock.query.filter_by(nombre_med='Ibuprofeno 400mg').first()
        assert fila is not None, 'el medicamento no entro al inventario'
        assert fila.cantidad_minima == 20, 'no se guardo el punto de reposicion'

    with Paso('personal', 'Emitir un ticket de entrega desde una orden') as p:
        with app.app_context():
            # Una orden vigente, distinta de la que se acaba de anular.
            orden = MedicalOrder.query.filter(
                MedicalOrder.status != 'anulada').first()
            orden_id = orden.id if orden else None
            antes = MedicationPickupTicket.query.count()
        if orden_id:
            from time_utils import colombia_now
            r = sta.post('/staff/dispense/%d' % orden_id, data={
                'pharmacy_id': ids['farmacia'],
                'pickup_date': colombia_now().strftime('%Y-%m-%d'),
                'pickup_time': '10:00',
            }, follow_redirects=True)
            p.estado = r.status_code
            with app.app_context():
                ahora = MedicationPickupTicket.query.count()
            assert ahora > antes, 'no se genero el ticket de entrega'

    # ================= FARMACIA =================
    exp = cliente_de('exp')

    with Paso('farmacia', 'Buscar un ticket por su codigo') as p:
        with app.app_context():
            ticket = db.session.get(MedicationPickupTicket, ids['ticket'])
            codigo = ticket.pickup_code
        r = exp.get('/expendedor/dashboard?q=%s' % codigo)
        p.estado = r.status_code
        assert codigo.encode() in r.data, 'el ticket no aparecio al buscarlo'

    with Paso('farmacia', 'No se entrega contra una orden anulada') as p:
        # La orden del ticket original quedo anulada en el paso anterior.
        with app.app_context():
            ticket = db.session.get(MedicationPickupTicket, ids['ticket'])
            antes = ticket.status
            stock = Stock.query.filter_by(nombre_med='Losartán 50mg').first()
            cantidad_antes = stock.cantidad if stock else 0
        r = exp.post('/expendedor/confirm/%d' % ids['ticket'], data={
            'identity_verified': 'on', 'receiver_kind': 'paciente',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            stock = Stock.query.filter_by(nombre_med='Losartán 50mg').first()
            cantidad_ahora = stock.cantidad if stock else 0
        assert cantidad_ahora == cantidad_antes, (
            'se descontaron existencias contra una orden anulada')

    with Paso('farmacia', 'Fijar el punto de reposicion de la sede') as p:
        with app.app_context():
            fila = Stock.query.filter_by(
                nombre_med='Acetaminofén 500mg').first()
            fila_id = fila.id if fila else None
        if fila_id:
            r = exp.post('/expendedor/dashboard', data={
                'action': 'set_punto_reposicion', 'stock_id': fila_id,
                'cantidad_minima': '25',
            }, follow_redirects=True)
            p.estado = r.status_code
            with app.app_context():
                assert db.session.get(Stock, fila_id).cantidad_minima == 25, \
                    'no se guardo el punto de reposicion'

    # ================= ADMINISTRACION =================
    adm = cliente_de('adm')

    with Paso('administracion', 'Registrar un profesional') as p:
        with app.app_context():
            antes = User.query.filter_by(role='doctor').count()
        r = adm.post('/admin/dashboard', data={
            'action': 'add_doctor', 'name': 'Laura Gomez Ruiz',
            'cedula': '1098765432', 'specialty': 'Pediatria',
            'username': 'doc2', 'password': CLAVE,
            'medical_registration': 'RM-54321',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = User.query.filter_by(role='doctor').count()
        assert ahora > antes, 'el profesional no quedo registrado'

    with Paso('administracion', 'Crear un codigo de acceso a la clinica') as p:
        from models import ClinicAccessCode
        with app.app_context():
            antes = ClinicAccessCode.query.count()
        r = adm.post('/admin/dashboard', data={
            'action': 'create_clinic_access_code', 'code': 'VEREDA2026',
            'max_uses': '10', 'duration_days': '90',
            'description': 'Jornada de salud',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = ClinicAccessCode.query.count()
        assert ahora > antes, 'el codigo de acceso no se creo'

    with Paso('administracion', 'Fijar el punto de reposicion de otra sede') as p:
        with app.app_context():
            fila = Stock.query.filter_by(nombre_med='Losartán 50mg').first()
            fila_id = fila.id
        r = adm.post('/admin/dashboard', data={
            'action': 'set_punto_reposicion', 'stock_id': fila_id,
            'cantidad_minima': '45',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            assert db.session.get(Stock, fila_id).cantidad_minima == 45

    with Paso('administracion', 'Exportar el RIPS del periodo') as p:
        from time_utils import colombia_now
        desde = (colombia_now() - timedelta(days=30)).strftime('%Y-%m-%d')
        hasta = colombia_now().strftime('%Y-%m-%d')
        r = adm.get('/admin/export_rips?start_date=%s&end_date=%s' % (desde, hasta))
        p.estado = r.status_code
        assert r.status_code in (200, 302), 'la exportacion del RIPS fallo'

    with Paso('administracion', 'No puede tocar los documentos legales') as p:
        from models import LegalConfiguration
        r = adm.post('/settings/', data={
            'action': 'legal_configuration',
            'legal_NIT_OPERADOR': '999-SECUESTRADO',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            fila = LegalConfiguration.query.filter_by(key='NIT_OPERADOR').first()
        assert fila is None or fila.value != '999-SECUESTRADO', (
            'el admin de una clinica reescribio el responsable del tratamiento '
            'de todas las demas')

    with Paso('administracion', 'No alcanza el inventario del personal') as p:
        r = adm.get('/staff/inventory')
        p.estado = r.status_code
        assert r.status_code == 403, (
            'la administracion alcanzo una pantalla que no le corresponde')

    # ================= SUPERADMINISTRACION =================
    sup = cliente_de('sup')

    with Paso('superadministracion', 'Ver el panel') as p:
        r = sup.get('/superadmin/')
        p.estado = r.status_code
        assert r.status_code == 200

    with Paso('superadministracion', 'Crear una clinica') as p:
        with app.app_context():
            antes = Clinic.query.count()
        r = sup.post('/superadmin/', data={
            'action': 'create_clinic',
            'clinic_name': 'Puesto de Salud La Union',
            'location': 'La Union, Antioquia',
            'access_type': 'public', 'plan_pago': 'starter',
            'admin_name': 'Marta Ospina Diaz', 'admin_username': 'adm2',
            'admin_password': CLAVE, 'admin_cedula': '1055667788',
            'admin_email': 'adm2@ejemplo.co',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = Clinic.query.count()
        assert ahora > antes, 'la clinica no se creo'

    with Paso('superadministracion', 'Si puede fijar los datos legales') as p:
        from models import LegalConfiguration
        r = sup.post('/settings/', data={
            'action': 'legal_configuration',
            'legal_NIT_OPERADOR': '900123456-7',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            fila = LegalConfiguration.query.filter_by(key='NIT_OPERADOR').first()
        assert fila is not None and fila.value == '900123456-7', (
            'la superadministracion no pudo fijar los datos legales')

    # ================= SIN SESION =================
    anon = app.test_client()

    for ruta in ('/patient/dashboard', '/doctor/dashboard', '/admin/dashboard',
                 '/superadmin/', '/staff/inventory', '/expendedor/dashboard'):
        with Paso('sin sesion', 'No alcanza %s' % ruta) as p:
            r = anon.get(ruta, follow_redirects=False)
            p.estado = r.status_code
            assert r.status_code in (302, 401, 403), (
                'una pantalla privada respondio %s sin sesion' % r.status_code)

    with Paso('sin sesion', 'Registrarse como paciente') as p:
        with app.app_context():
            antes = User.query.filter_by(role='patient').count()
        r = anon.post('/register', data={
            'name': 'Pedro Ramirez Soto', 'username': 'pac2',
            'password': CLAVE, 'cedula': '1122334455',
            'document_type': 'CC', 'birth_date': '1990-05-20', 'sex': 'M',
            'accept_terms': 'on', 'accept_privacy': 'on',
            'accept_transparency': 'on',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = User.query.filter_by(role='patient').count()
        assert ahora > antes, 'el registro de paciente no creo la cuenta'

    with Paso('sin sesion', 'La cuenta se bloquea tras varios fallos') as p:
        # Dos defensas distintas y ambas valen: el limitador por IP
        # responde 429, y el bloqueo por cuenta rechaza el acceso aunque la
        # clave sea correcta. Lo que no puede pasar es que ocho intentos
        # fallidos dejen la puerta como estaba.
        frenado = False
        for _ in range(8):
            r = anon.post('/login', data={'username': 'doc',
                                          'password': 'clave-incorrecta'},
                          follow_redirects=True)
            if r.status_code == 429:
                frenado = True
                break
            if b'bloque' in r.data.lower() or b'intente' in r.data.lower():
                frenado = True
        p.estado = r.status_code
        assert frenado, 'ocho intentos fallidos no frenaron nada'


    # ================= SEGUNDA TANDA =================
    # Lo que la primera no tocaba: la adenda, los cargamentos, la
    # transferencia entre sedes, la calificacion, la analitica y la auditoria.

    with Paso('profesional', 'Registrar una adenda sobre la historia') as p:
        with app.app_context():
            historia = MedicalHistory.query.filter_by(
                record_type='consultation').first()
            historia_id = historia.id if historia else None
            antes = MedicalHistory.query.filter_by(record_type='adenda').count()
        assert historia_id, 'no habia historia sobre la que enmendar'
        r = doc.post('/doctor/historia/%d/adenda' % historia_id, data={
            'amendment_reason': ('El diagnostico se registro por error de '
                                 'digitacion; corresponde a otro codigo.'),
            'cie10_code': 'A00', 'diagnosis': 'Colera',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = MedicalHistory.query.filter_by(record_type='adenda').count()
        assert ahora > antes, 'la adenda no quedo registrada'

    with Paso('profesional', 'La historia original no se puede borrar') as p:
        # Resolucion 839 de 2017: quince anos de retencion. Una correccion se
        # registra como adenda junto al original, nunca sobre el.
        with app.app_context():
            original = db.session.get(MedicalHistory, historia_id)
        assert original is not None, 'el registro original desaparecio'

    with Paso('profesional', 'Cambiar la disponibilidad de atencion') as p:
        r = doc.post('/doctor/dashboard',
                     data={'action': 'toggle_availability'},
                     follow_redirects=True)
        p.estado = r.status_code
        assert r.status_code == 200

    with Paso('personal', 'Registrar un cargamento en camino') as p:
        from models import IncomingShipment
        from time_utils import colombia_now
        with app.app_context():
            antes = IncomingShipment.query.count()
        r = sta.post('/staff/envios', data={
            'action': 'create_shipment', 'pharmacy_id': ids['farmacia'],
            'supplier_name': 'Distribuidora del Oriente',
            'expected_date': (colombia_now() + timedelta(days=2)).strftime('%Y-%m-%d'),
            'med_name[]': 'Ibuprofeno 400mg', 'quantity[]': '200',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = IncomingShipment.query.count()
        assert ahora > antes, 'el cargamento no quedo registrado'

    with Paso('farmacia', 'Solicitar existencias a otra sede') as p:
        from models import StockTransferRequest
        with app.app_context():
            otra = Pharmacy.query.filter(
                Pharmacy.id != ids['farmacia']).first()
            if otra is None:
                otra = Pharmacy(clinic_id=ids['clinica'],
                                name='Farmacia La Vereda', is_active=True)
                db.session.add(otra)
                db.session.commit()
            otra_id = otra.id
            antes = StockTransferRequest.query.count()
        r = exp.post('/expendedor/dashboard', data={
            'action': 'request_transfer', 'source_pharmacy_id': otra_id,
            'med_name': 'Losartán 50mg', 'quantity': '20',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = StockTransferRequest.query.count()
        assert ahora > antes, 'la solicitud de transferencia no se registro'

    with Paso('administracion', 'Ver la analitica de la clinica') as p:
        r = adm.get('/admin/analytics')
        p.estado = r.status_code
        assert r.status_code in (200, 302), 'la analitica no respondio'

    with Paso('administracion', 'Ver el registro de auditoria') as p:
        r = adm.get('/admin/auditoria')
        p.estado = r.status_code
        assert r.status_code == 200, 'el registro de auditoria no abrio'
        assert b'login_success' in r.data or b'audit' in r.data.lower(), \
            'el registro de auditoria salio vacio'

    with Paso('administracion', 'Resolver una alerta de reposicion') as p:
        from models import ReplenishmentAlert
        with app.app_context():
            alerta = ReplenishmentAlert.query.filter_by(status='abierta').first()
            alerta_id = alerta.id if alerta else None
        if alerta_id:
            r = adm.post('/admin/dashboard', data={
                'action': 'resolve_replenishment_alert', 'alert_id': alerta_id,
            }, follow_redirects=True)
            p.estado = r.status_code
            with app.app_context():
                estado = db.session.get(ReplenishmentAlert, alerta_id).status
            assert estado == 'resuelta', 'la alerta no quedo resuelta'

    with Paso('administracion', 'Gestionar una PQRS') as p:
        with app.app_context():
            queja = ServiceComplaint.query.first()
            queja_id = queja.id if queja else None
        assert queja_id, 'no habia ninguna PQRS que gestionar'
        r = adm.post('/pqrs/gestion/%d/responder' % queja_id, data={
            'response': ('Se repuso el inventario de la sede y se contacto a '
                         'la paciente para coordinar la entrega.'),
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            estado = db.session.get(ServiceComplaint, queja_id).status
        assert estado != 'radicada', 'la PQRS no cambio de estado'

    with Paso('superadministracion', 'Registrar un profesional autonomo') as p:
        with app.app_context():
            antes = User.query.filter_by(role='doctor').count()
        r = sup.post('/superadmin/', data={
            'action': 'create_autonomous_doctor',
            'name': 'Jorge Alberto Rios', 'username': 'doc_auto',
            'password': CLAVE, 'cedula': '1077889900',
            'medical_registration': 'RM-77889', 'specialty': 'Medicina interna',
            'email': 'rios@ejemplo.co',
        }, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            ahora = User.query.filter_by(role='doctor').count()
        assert ahora > antes, 'el profesional autonomo no se registro'

    with Paso('sin sesion', 'La pagina de socios abre') as p:
        r = anon.get('/socio')
        p.estado = r.status_code
        assert r.status_code == 200, 'la pagina de socios no abrio'

    with Paso('sin sesion', 'Los documentos legales abren sin sesion') as p:
        for ruta in ('/terminos', '/privacidad', '/aviso-de-privacidad',
                     '/transparencia', '/telemedicina'):
            r = anon.get(ruta)
            assert r.status_code == 200, '%s respondio %s' % (ruta, r.status_code)
            assert b'[[' not in r.data, (
                '%s muestra marcadores sin reemplazar: no es un documento '
                'legal publicable' % ruta)
        p.estado = 200

    with Paso('paciente', 'Calificar al profesional') as p:
        with app.app_context():
            cerrado = Chat.query.filter_by(patient_id=ids['paciente']).first()
            chat_cal = cerrado.id if cerrado else None
        if chat_cal:
            r = pac.post('/patient/rate_chat/%d' % chat_cal,
                         data={'stars': '5', 'comment': 'Muy buena atencion.'},
                         follow_redirects=True)
            p.estado = r.status_code
            assert r.status_code in (200, 302)

    with Paso('paciente', 'Una calificacion fuera de rango no pasa') as p:
        # `stars` llegaba del formulario sin acotar: un valor absurdo movia el
        # promedio del profesional, que es lo que el paciente mira para elegir
        # a quien consultar.
        #
        # Hace falta una consulta SIN calificar: la ruta rechaza la segunda
        # sobre el mismo chat, y esa guarda hacia que la comprobacion pasara
        # sin llegar nunca al limite. Una prueba que pasa por el motivo
        # equivocado es peor que no tenerla.
        from models import Rating
        with app.app_context():
            otro_chat = Chat(clinic_id=ids['clinica'],
                             patient_id=ids['paciente'],
                             doctor_id=ids['doctor'], status='closed',
                             reason='Consulta para calificar', mode='read-only')
            db.session.add(otro_chat)
            db.session.commit()
            chat_sin_calificar = otro_chat.id

        r = pac.post('/patient/rate_chat/%d' % chat_sin_calificar,
                     data={'stars': '100000'}, follow_redirects=True)
        p.estado = r.status_code
        with app.app_context():
            guardada = Rating.query.filter_by(
                chat_id=chat_sin_calificar).first()
            promedio = db.session.get(User, ids['doctor']).rating or 0
        assert guardada is None or guardada.stars <= 5, (
            'se guardo una calificacion de %s estrellas' % guardada.stars)
        assert promedio <= 5, (
            'el promedio del profesional quedo en %s' % promedio)

    with Paso('paciente', 'Una calificacion no numerica no revienta') as p:
        with app.app_context():
            tercero = Chat(clinic_id=ids['clinica'],
                           patient_id=ids['paciente'], doctor_id=ids['doctor'],
                           status='closed', reason='Otra', mode='read-only')
            db.session.add(tercero)
            db.session.commit()
            tercero_id = tercero.id
        r = pac.post('/patient/rate_chat/%d' % tercero_id,
                     data={'stars': 'muchas'}, follow_redirects=True)
        p.estado = r.status_code
        assert r.status_code < 500, (
            'una calificacion no numerica devolvio %s' % r.status_code)

    with Paso('paciente', 'No alcanza la historia de otro paciente') as p:
        with app.app_context():
            from security import pii_hash
            otro = User(clinic_id=ids['clinica'], username='pac_ajeno',
                        password='x', role='patient', name='Otro Paciente',
                        cedula='1000000009',
                        cedula_hash=pii_hash('1000000009'))
            db.session.add(otro)
            db.session.commit()
            otro_id = otro.id
        r = pac.get('/doctor/patient_history/%d' % otro_id,
                    follow_redirects=False)
        p.estado = r.status_code
        assert r.status_code in (302, 403, 404), (
            'un paciente alcanzo la historia clinica de otro')

    return informe


def main():
    os.makedirs(DESTINO, exist_ok=True)
    resultados = simular()

    io.open(os.path.join(DESTINO, 'simulacion.json'), 'w',
            encoding='utf-8').write(
        json.dumps(resultados, ensure_ascii=False, indent=1))

    fallos = [i for i in resultados if i['resultado'] != 'ok']
    print('pasos ejecutados: %d' % len(resultados))
    print('fallos: %d' % len(fallos))
    print()
    for i in fallos:
        print('  [%s] %s' % (i['rol'], i['accion']))
        print('      http=%s  %s' % (i['http'], i.get('error', '')[:200]))
    return resultados


if __name__ == '__main__':
    main()
