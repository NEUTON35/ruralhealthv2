# -*- coding: utf-8 -*-
"""Siembra una base con datos de todos los roles, para revisar la interfaz.

No es una fixture de pruebas: es un escenario completo, con contenido en cada
pantalla, para poder mirarlas sin tener que crear cuentas a mano.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUTA_BD = os.path.join(tempfile.gettempdir(), 'revision_ui.db')

# Clave unica para los seis roles. Es una base desechable en el temporal
# del sistema, no una instalacion real: la clave esta aqui a proposito
# para que quien revise la interfaz pueda entrar sin ir a buscarla.
CLAVE = 'Rural-Health-2026#Seg'


def preparar():
    os.environ['FLASK_ENV'] = 'development'
    if os.path.exists(RUTA_BD):
        os.remove(RUTA_BD)
    os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + RUTA_BD.replace(os.sep, '/')
    sys.path.insert(0, RAIZ)
    os.chdir(RAIZ)

    from app import create_app
    from models import (Appointment, Chat, Clinic, DoctorSchedule, DoctorTariff,
                        InventoryItem, MedicalHistory, MedicalOrder,
                        MedicationPickupTicket, Message, Notification, Pharmacy,
                        ServiceComplaint, SivigilaNotification, Stock, User, db)
    from pharmacy_utils import alerta_por_punto_de_reposicion
    from security import (bootstrap_schema, generate_pickup_hash,
                          generate_signed_order_hash, hash_password, pii_hash)
    from service_quality import radicar_pqrs, registrar_evento_adverso
    from surveillance import detectar
    from time_utils import colombia_now

    clave = CLAVE
    app = create_app()
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True

    with app.app_context():
        db.create_all()
        bootstrap_schema(db, create_tables=True)

        cl = Clinic(name='Puesto de Salud El Carmen', legal_name='ESE El Carmen',
                    nit='900123456-7', habilitacion_code='0500112345',
                    department_code='05', municipality_code='001',
                    location='El Carmen de Viboral, Antioquia',
                    latitude=6.08, longitude=-75.33,
                    status='active', activa=True, access_type='public')
        db.session.add(cl)
        db.session.flush()

        ph = Pharmacy(clinic_id=cl.id, name='Farmacia Central',
                      address='Calle 12 #4-30', latitude=6.08, longitude=-75.33)
        db.session.add(ph)
        db.session.flush()

        def usuario(rol, nombre, usr, **kw):
            u = User(clinic_id=cl.id, username=usr, password=hash_password(clave),
                     role=rol, name=nombre, cedula='10' + usr,
                     cedula_hash=pii_hash('10' + usr), created_at=colombia_now(),
                     accepted_terms_at=colombia_now(),
                     accepted_privacy_at=colombia_now(),
                     accepted_transparency_at=colombia_now(),
                     sensitive_data_consent_at=colombia_now(), **kw)
            db.session.add(u)
            return u

        pac = usuario('patient', 'María Elena Pérez Gómez', 'pac',
                      birth_date=date(1985, 3, 14), sex='F', zone='R',
                      document_type='CC', first_name='María', second_name='Elena',
                      first_surname='Pérez', second_surname='Gómez',
                      department_code='05', municipality_code='001',
                      affiliation_regime='subsidiado', affiliation_type='cabeza',
                      insurer_name='Savia Salud', insurer_code='EPS042',
                      phone='3001234567', address='Vereda La Chapa',
                      nationality_code='170', ethnic_group='99', disability='08',
                      latitude=6.09, longitude=-75.34)
        doc = usuario('doctor', 'Carlos Rodríguez Mesa', 'doc',
                      medical_registration='RM-12345', specialty='Medicina general',
                      document_type='CC', first_name='Carlos', first_surname='Rodríguez',
                      is_available=True, atencion_inicio='07:00', atencion_fin='17:00',
                      office_address='Consultorio 3', office_latitude=6.08,
                      office_longitude=-75.33, show_office_on_map=True)
        adm = usuario('admin', 'Ana Torres Gil', 'adm')
        exp = usuario('expendedor', 'Luis Mora Díaz', 'exp', pharmacy_id=ph.id)
        sta = usuario('staff', 'Sofía Ruiz León', 'sta')
        sup = usuario('super', 'Superadministrador', 'sup')
        db.session.flush()

        db.session.add(DoctorTariff(clinic_id=cl.id, doctor_id=doc.id,
                                    price_per_consultation=35000,
                                    price_monthly=90000, price_annual=850000))
        for dia in range(5):
            db.session.add(DoctorSchedule(clinic_id=cl.id, doctor_id=doc.id,
                                          day_of_week=dia, start_time='07:00',
                                          end_time='17:00', is_available=True))

        db.session.add(InventoryItem(clinic_id=cl.id, name='Losartán 50mg',
                                     sku='LOS-50', unit='tabletas',
                                     stock=120, min_stock=30))
        # Las tres situaciones del punto de reposicion, para poder verlas en el
        # recorrido: por encima, en el punto, y sin punto definido.
        for nombre, cant, minimo in (('Losartán 50mg', 120, 30),
                                     ('Amoxicilina 500mg', 8, 50),
                                     ('Acetaminofén 500mg', 45, 0)):
            fila = Stock(clinic_id=cl.id, pharmacy_id=ph.id,
                         nombre_med=nombre, medicamento=nombre,
                         cantidad=cant, unidad='tabletas',
                         cantidad_minima=minimo)
            db.session.add(fila)
            db.session.flush()
            # Igual que al guardar desde la pantalla: si ya esta en el punto de
            # reposicion, el administrador tiene que verlo en sus alertas.
            alerta_por_punto_de_reposicion(fila)

        chat = Chat(clinic_id=cl.id, patient_id=pac.id, doctor_id=doc.id,
                    status='open', reason='Dolor de cabeza persistente',
                    mode='consultation')
        db.session.add(chat)
        db.session.flush()
        db.session.add(Message(clinic_id=cl.id, chat_id=chat.id, sender_id=pac.id,
                               content='Buenos días doctor, llevo tres días con dolor de cabeza.'))
        db.session.add(Message(clinic_id=cl.id, chat_id=chat.id, sender_id=doc.id,
                               content='Buenos días. ¿El dolor es en toda la cabeza o de un solo lado?'))

        soporte = Chat(clinic_id=cl.id, patient_id=pac.id, doctor_id=sta.id,
                       status='open', reason='Disponibilidad de medicamentos',
                       mode='triage')
        db.session.add(soporte)

        for dias, estado in ((1, 'pending'), (-3, 'attended')):
            db.session.add(Appointment(
                clinic_id=cl.id, patient_id=pac.id, doctor_id=doc.id,
                date=(colombia_now() + timedelta(days=dias)).strftime('%Y-%m-%d'),
                time='08:30' if dias > 0 else '10:00',
                appointment_type='Consulta', status=estado,
                description='Control de presión arterial'))

        meds = '[{"nombre_med":"Losartán 50mg","cantidad":30,"unidad":"tabletas","indicaciones":"1 tableta cada 24 horas"}]'
        sello = generate_signed_order_hash(doc.id, pac.id, meds, cl.id)
        orden = MedicalOrder(
            clinic_id=cl.id, order_number='OM-000001', doctor_id=doc.id,
            patient_id=pac.id, meds_json=meds, status='activa',
            verification_hash=sello, hash_seguridad=sello,
            doctor_registration='RM-12345', signed_at=colombia_now(),
            patient_document='10pac', patient_document_type='CC',
            insurance_name='Savia Salud', insurance_regime='subsidiado',
            care_modality='presencial', created_at=colombia_now(),
            expires_at=colombia_now() + timedelta(days=30))
        db.session.add(orden)
        db.session.flush()

        ticket = MedicationPickupTicket(
            clinic_id=cl.id, order_id=orden.id, patient_id=pac.id,
            pharmacy_id=ph.id, staff_id=sta.id, pickup_code='A1B2C3',
            pickup_hash=generate_pickup_hash(1, orden.id, pac.id, cl.id, 'A1B2C3'),
            status='autorizado', meds_json=meds,
            pickup_date=colombia_now().strftime('%Y-%m-%d'), pickup_time='09:00')
        db.session.add(ticket)

        for cie10, resumen in (('Z000', 'Examen médico general de control'),
                               ('A90', 'Fiebre de tres días, sospecha de dengue')):
            h = MedicalHistory(
                clinic_id=cl.id, patient_id=pac.id, doctor_id=doc.id,
                record_type='consultation', summary=resumen,
                diagnosis=resumen, cie10_code=cie10, cups_code='890201',
                external_cause='26', consultation_purpose='15',
                care_modality='01', created_at=colombia_now())
            db.session.add(h)
            db.session.flush()
            detectar(db, h)

        db.session.add(Notification(
            user_id=pac.id, clinic_id=cl.id, title='Medicamentos listos',
            message='Su ticket A1B2C3 está listo en Farmacia Central.',
            type='ticket_ready'))
        db.session.commit()

        radicar_pqrs(db, pac, 'queja', 'No me entregaron el medicamento',
                     'Fui dos veces a la farmacia y me dijeron que no había existencias.')
        registrar_evento_adverso(db, pac, 'Amoxicilina 500mg',
                                 'Exantema generalizado a las seis horas de la primera dosis',
                                 reportado_por_id=doc.id, clinic_id=cl.id)
        db.session.commit()

    return app, clave
