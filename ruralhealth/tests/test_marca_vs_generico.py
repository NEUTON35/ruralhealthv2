"""La verificación de seguridad tiene que mirar el principio activo.

El defecto que estas pruebas previenen es el peor que ha tenido este sistema.

El formulario de prescripción guarda la marca en `medicamento` y el principio
activo en `nombre_med`. El motor de seguridad leía `medicamento` primero, así
que al recetar «Amoxal» normalizaba «amoxal», no lo encontraba en la tabla de
clases y devolvía cero hallazgos, en un paciente con alergia a la penicilina
registrada y confirmada. No saltaba nada: ni alergia, ni interacción, ni
duplicidad, ni embarazo, ni pediatría. Y la orden quedaba archivada con un
informe que decía «sin hallazgos», es decir, con constancia escrita de que se
había verificado.

Las marcas son infinitas y cambian por país y por laboratorio. Los principios
activos son los que están en las tablas. Se evalúa el genérico, siempre.

La segunda mitad: el campo obligatorio del formulario es el principio activo,
pero el parser recorría la lista del nombre comercial, que es opcional. Recetar
por genérico (lo que exige la Resolución 1403 de 2007) devolvía «agregue al
menos un medicamento». El único camino que funcionaba era escribir la marca, y
ése era justo el que apagaba la verificación.
"""

import types

import pytest


def alergia(sustancia, severidad='grave'):
    """Una alergia como la lee el motor: por atributo, no por clave."""
    return types.SimpleNamespace(substance=sustancia, severity=severidad,
                                 status='confirmada', reaction=None)


class TestElMotorMiraElPrincipioActivo:

    def test_la_alergia_salta_aunque_se_recete_por_la_marca(self):
        """El caso exacto: penicilina registrada, receta escrita como «Amoxal»."""
        from clinical_safety import check_allergies

        alergias = [alergia('Penicilina')]
        receta = [{'medicamento': 'Amoxal',        # marca
                   'nombre_med': 'Amoxicilina',    # principio activo
                   'denominacion_comun': 'Amoxicilina'}]

        hallazgos = check_allergies(receta, alergias)
        assert hallazgos, 'no saltó la alergia a la penicilina'

    def test_la_alerta_nombra_el_principio_activo(self):
        """Al profesional hay que decirle por qué salta, y eso es el genérico."""
        from clinical_safety import _med_display

        assert _med_display({'medicamento': 'Amoxal',
                             'nombre_med': 'Amoxicilina'}) == 'Amoxicilina'

    def test_sin_generico_se_usa_lo_que_haya(self):
        """Un dato viejo sin `nombre_med` no puede quedarse sin evaluar."""
        from clinical_safety import _med_display

        assert _med_display({'medicamento': 'Amoxicilina'}) == 'Amoxicilina'
        assert _med_display({'name': 'Amoxicilina'}) == 'Amoxicilina'

    def test_la_duplicidad_tambien_mira_el_generico(self):
        """Dos marcas distintas de la misma clase siguen siendo duplicidad.

        Escritas como marca, el motor no las reconocía y las dejaba pasar.
        """
        from clinical_safety import check_duplicate_therapy

        receta = [{'medicamento': 'Amoxal', 'nombre_med': 'Amoxicilina'}]
        activos = [{'medicamento': 'Ampi-Genfar', 'nombre_med': 'Ampicilina'}]

        hallazgos = check_duplicate_therapy(receta, activos)
        assert hallazgos, 'no detectó dos penicilinas escritas como marca'

    def test_el_embarazo_tambien_mira_el_generico(self):
        from clinical_safety import check_pregnancy

        receta = [{'medicamento': 'Cumadin', 'nombre_med': 'Warfarina'}]
        assert check_pregnancy(receta, True), 'no saltó la contraindicación'


class TestPrescribirPorGenerico:
    """La Resolución 1403 de 2007 exige prescribir por denominación común."""

    @pytest.fixture
    def receta(self, app, make_user):
        from models import Chat, db

        doctor = make_user(role='doctor', username='doc_dci',
                           medical_registration='RM-99999',
                           signature_path='clinic_1/firma.png')
        patient = make_user(role='patient', username='pac_dci')
        with app.app_context():
            chat = Chat(clinic_id=1, patient_id=patient.id, doctor_id=doctor.id,
                        status='open', reason='Control', mode='consultation')
            db.session.add(chat)
            db.session.commit()
        return {'doctor': doctor, 'patient': patient}

    def _formulario(self, **cambios):
        # El formulario mínimo válido ya está construido en las pruebas de
        # prescripción; aquí solo se cambia el par de nombres.
        from tests.test_prescription import prescription_form
        return prescription_form(**cambios)

    def test_solo_con_el_principio_activo_se_emite_la_orden(
            self, app, client, login, receta):
        """Sin nombre comercial, que es opcional, la orden debe salir."""
        from models import MedicalOrder, db

        login('doc_dci')
        respuesta = client.post(
            '/doctor/prescription/%d' % receta['patient'].id,
            data=self._formulario(generic_name='Paracetamol', med_name=''),
            follow_redirects=True)
        assert respuesta.status_code == 200

        with app.app_context():
            ordenes = MedicalOrder.query.filter_by(
                patient_id=receta['patient'].id).all()
        assert len(ordenes) == 1, 'no se emitió la orden prescrita por genérico'
        assert 'Paracetamol' in ordenes[0].meds_json

    def test_la_marca_sola_sigue_funcionando(self, app, client, login, receta):
        """Quien escriba solo la marca no se queda sin poder recetar."""
        from models import MedicalOrder, db

        login('doc_dci')
        client.post('/doctor/prescription/%d' % receta['patient'].id,
                    data=self._formulario(generic_name='', med_name='Paracetamol'),
                    follow_redirects=True)
        with app.app_context():
            ordenes = MedicalOrder.query.filter_by(
                patient_id=receta['patient'].id).all()
        assert len(ordenes) == 1

    def test_un_renglon_vacio_no_cuenta(self, app, client, login, receta):
        from routes_doctor import _medications_from_form_legal

        with app.test_request_context('/', method='POST', data={
                'generic_name': ['Acetaminofen', ''],
                'med_name': ['', ''],
                'concentration': ['500 mg', ''],
                'dosage_form': ['tableta', ''],
                'route': ['oral', ''],
                'dosage': ['1 tableta', ''],
                'frequency': ['cada 8 horas', ''],
                'duration_days': ['3', ''],
                'quantity': ['9', ''],
                'unit': ['tabletas', ''],
                'instructions': ['', ''],
        }):
            meds, errores = _medications_from_form_legal()
        assert len(meds) == 1
        assert not errores
