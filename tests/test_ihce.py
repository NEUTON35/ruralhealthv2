# -*- coding: utf-8 -*-
"""Interoperabilidad IHCE: Resumen Digital de Atencion (Resolucion 1888 de 2025).

Las pruebas se escriben contra las reglas del Manual de operaciones v1.4 y
contra el perfil CompositionAmbulatoryRDA. No hay red: el cliente recibe un
`opener` falso.

Lo que estas pruebas NO demuestran: que el Ministerio acepte el documento. Eso
exige el ambiente de pruebas con credenciales reales. Aqui se verifica que el
documento cumple las reglas que el propio manual dice que el servidor aplica, y
que la cola se comporta bien cuando la red falla.
"""

import json
import urllib.error
from datetime import date, datetime

import pytest

from ihce import terminology as T
from ihce.client import DuplicadoError, IHCEClient, IHCEError
from ihce.config import IHCEConfig
from ihce.mapping import construir_bundle_consulta
from ihce.validation import validar_bundle, validar_datos_minimos


# --- Dobles de prueba -------------------------------------------------------

class FakePaciente:
    def __init__(self, **kw):
        self.document_type = kw.get('document_type', 'CC')
        self.cedula = kw.get('cedula', '1098765432')
        self.first_name = kw.get('first_name', 'María')
        self.second_name = kw.get('second_name', 'Elena')
        self.first_surname = kw.get('first_surname', 'Pérez')
        self.second_surname = kw.get('second_surname', 'Gómez')
        self.name = 'María Elena Pérez Gómez'
        self.birth_date = kw.get('birth_date', date(1985, 3, 14))
        self.sex = kw.get('sex', 'F')
        self.phone = kw.get('phone', '3001234567')
        self.municipality_code = kw.get('municipality_code', '05001')
        self.department_code = kw.get('department_code', '05')
        self.zone = kw.get('zone', 'R')
        self.nationality_code = kw.get('nationality_code', '170')
        self.ethnic_group = kw.get('ethnic_group', '99')
        self.disability = kw.get('disability', '08')
        self.is_active_account = True


class FakeProfesional:
    def __init__(self, **kw):
        self.document_type = 'CC'
        self.cedula = kw.get('cedula', '79123456')
        self.first_name = 'Carlos'
        self.second_name = None
        self.first_surname = 'Rodríguez'
        self.second_surname = None
        self.name = 'Carlos Rodríguez'
        self.medical_registration = kw.get('medical_registration', 'RM-12345')


class FakeClinica:
    def __init__(self, **kw):
        self.habilitacion_code = kw.get('habilitacion_code', '0500112345')
        self.name = 'Puesto de Salud El Carmen'
        self.legal_name = 'ESE Hospital El Carmen'
        self.activa = True


class FakeHistoria:
    def __init__(self, **kw):
        self.id = kw.get('id', 42)
        self.created_at = kw.get('created_at', datetime(2026, 9, 7, 10, 30))
        self.cie10_code = kw.get('cie10_code', 'J00X')
        self.diagnosis = kw.get('diagnosis', 'Rinofaringitis aguda')
        self.summary = 'Paciente con cuadro gripal de 3 dias'
        self.treatment = 'Sintomatico'
        self.patient_id = 1
        self.doctor_id = 2
        self.clinic_id = 1


class FakeAlergia:
    def __init__(self, sustancia='Penicilina', severidad='grave', estado='confirmada'):
        self.substance = sustancia
        self.reaction = 'Urticaria generalizada'
        self.severity = severidad
        self.status = estado
        self.onset_date = date(2010, 1, 1)
        self.is_active = estado != 'descartada'


class FakeCronica:
    def __init__(self, cie10='E119'):
        self.cie10_code = cie10
        self.condition = 'Diabetes mellitus tipo 2'
        self.status = 'activa'
        self.diagnosed_on = date(2018, 6, 1)


def bundle_completo(**kw):
    return construir_bundle_consulta(
        kw.get('historia', FakeHistoria()),
        kw.get('paciente', FakePaciente()),
        kw.get('profesional', FakeProfesional()),
        kw.get('clinica', FakeClinica()),
        alergias=kw.get('alergias', []),
        condiciones=kw.get('condiciones', []),
        medicamentos=kw.get('medicamentos', []),
    )


# --- Reglas de estructura del manual v1.4 -----------------------------------

class TestReglasDelManual:
    """Reglas 1 a 6 de la seccion 5.4 del manual de operaciones."""

    def test_regla_1_el_bundle_es_de_tipo_document(self):
        assert bundle_completo()['type'] == 'document'

    def test_regla_2_la_primera_entrada_es_el_composition(self):
        b = bundle_completo()
        assert b['entry'][0]['resource']['resourceType'] == 'Composition'

    def test_regla_3a_solo_hay_un_composition(self):
        b = bundle_completo()
        cs = [e for e in b['entry']
              if e['resource']['resourceType'] == 'Composition']
        assert len(cs) == 1

    def test_regla_3b_todas_las_secciones_obligatorias_viajan(self):
        """Aunque no haya datos. La que va vacia lleva emptyReason."""
        b = bundle_completo()
        secciones = b['entry'][0]['resource']['section']
        codigos = {c['code']
                   for s in secciones for c in s['code']['coding']}
        for definicion in T.SECCIONES_CONSULTA:
            if definicion['obligatoria']:
                assert definicion['codigo'] in codigos, definicion['nombre']

    def test_regla_3b_la_seccion_vacia_lleva_el_codigo_exacto(self):
        b = bundle_completo(alergias=[])
        seccion = _seccion(b, '48765-2')
        assert 'entry' not in seccion
        coding = seccion['emptyReason']['coding'][0]
        assert coding['system'] == T.RAZON_VACIA_SYSTEM
        assert coding['code'] == 'nilknown'
        assert 'No existen elementos conocidos' in seccion['text']['div']

    def test_los_titulos_de_seccion_son_los_fijados_por_el_perfil(self):
        """Son valores fijos en el StructureDefinition: cambiarlos es un rechazo."""
        b = bundle_completo()
        secciones = b['entry'][0]['resource']['section']
        titulos = {s['title'] for s in secciones}
        for definicion in T.SECCIONES_CONSULTA:
            if definicion['obligatoria']:
                assert definicion['titulo'] in titulos, definicion['nombre']

    def test_regla_6_toda_referencia_apunta_a_una_entrada_existente(self):
        b = bundle_completo(alergias=[FakeAlergia()],
                            condiciones=[FakeCronica()],
                            medicamentos=[{'nombre_med': 'Acetaminofén',
                                           'cantidad': 20}])
        assert validar_bundle(b) == []


def _seccion(bundle, codigo_loinc):
    for s in bundle['entry'][0]['resource']['section']:
        for c in s['code']['coding']:
            if c['code'] == codigo_loinc:
                return s
    raise AssertionError('No existe la seccion %s' % codigo_loinc)


# --- Contenido clinico ------------------------------------------------------

class TestContenidoClinico:

    def test_el_diagnostico_de_la_atencion_va_en_la_seccion_de_problemas(self):
        b = bundle_completo()
        seccion = _seccion(b, '11450-4')
        assert len(seccion['entry']) == 1
        condicion = _recurso(b, 'Condition')
        assert condicion['code']['coding'][0]['code'] == 'J00X'
        assert condicion['code']['coding'][0]['system'] == T.CIE10_SYSTEM

    def test_las_cronicas_se_suman_al_diagnostico_de_la_atencion(self):
        b = bundle_completo(condiciones=[FakeCronica('E119')])
        seccion = _seccion(b, '11450-4')
        assert len(seccion['entry']) == 2

    def test_una_cronica_sin_cie10_no_se_envia(self):
        """Sin codigo no hay recurso valido; se omite en vez de enviar basura."""
        b = bundle_completo(condiciones=[FakeCronica(None)])
        assert len(_seccion(b, '11450-4')['entry']) == 1

    def test_la_alergia_conserva_severidad_y_reaccion(self):
        b = bundle_completo(alergias=[FakeAlergia(severidad='anafilaxia')])
        alergia = _recurso(b, 'AllergyIntolerance')
        assert alergia['reaction'][0]['severity'] == 'severe'
        assert 'Urticaria' in alergia['reaction'][0]['manifestation'][0]['text']

    def test_la_alergia_descartada_viaja_como_refutada(self):
        """Una alergia descartada es informacion clinica util, no ruido:
        evita que otro prestador la vuelva a asumir como cierta."""
        b = bundle_completo(alergias=[FakeAlergia(estado='descartada')])
        alergia = _recurso(b, 'AllergyIntolerance')
        assert alergia['verificationStatus']['coding'][0]['code'] == 'refuted'

    def test_el_medicamento_va_con_su_posologia(self):
        b = bundle_completo(medicamentos=[
            {'nombre_med': 'Amoxicilina', 'cantidad': 21,
             'indicaciones': '500 mg cada 8 horas por 7 dias'}])
        med = _recurso(b, 'MedicationRequest')
        assert med['medicationCodeableConcept']['text'] == 'Amoxicilina'
        assert '500 mg' in med['dosageInstruction'][0]['text']
        assert med['dosageInstruction'][0]['doseAndRate'][0]['doseQuantity']['value'] == 21.0

    def test_el_paciente_se_identifica_con_el_patron_del_manual(self):
        """Seccion 5.3 literal a: TipoIdentificacion-NumIdentificacion."""
        b = bundle_completo()
        paciente = _recurso(b, 'Patient')
        assert paciente['id'] == 'CC-1098765432'

    def test_la_ips_se_identifica_por_su_codigo_de_habilitacion(self):
        """Seccion 5.3 literal c."""
        b = bundle_completo()
        assert _recurso(b, 'Organization')['id'] == '0500112345'

    def test_el_registro_medico_viaja_como_identificador_secundario(self):
        b = bundle_completo()
        profesional = _recurso(b, 'Practitioner')
        valores = [i['value'] for i in profesional['identifier']]
        assert 'RM-12345' in valores

    def test_el_composition_lo_atesta_el_profesional_tratante(self):
        b = bundle_completo()
        composition = b['entry'][0]['resource']
        assert composition['attester'][0]['mode'] == 'legal'
        assert composition['attester'][0]['party']['reference'] == 'Practitioner/CC-79123456'

    def test_la_confidencialidad_es_la_que_fija_el_perfil(self):
        assert bundle_completo()['entry'][0]['resource']['confidentiality'] == 'N'


def _recurso(bundle, tipo):
    for e in bundle['entry']:
        if e['resource']['resourceType'] == tipo:
            return e['resource']
    raise AssertionError('No hay recurso %s' % tipo)


# --- Validacion previa ------------------------------------------------------

class TestValidacionPrevia:
    """Se valida en local para no gastar red en documentos que seran rechazados.

    En una zona rural la conectividad es el recurso escaso.
    """

    def test_un_bundle_bien_armado_no_tiene_errores(self):
        assert validar_bundle(bundle_completo()) == []

    def test_se_detecta_el_tipo_de_bundle_equivocado(self):
        b = bundle_completo()
        b['type'] = 'transaction'
        assert any('document' in e for e in validar_bundle(b))

    def test_se_detecta_que_el_composition_no_va_primero(self):
        b = bundle_completo()
        b['entry'].append(b['entry'].pop(0))
        assert any('primera entrada' in e for e in validar_bundle(b))

    def test_se_detecta_una_referencia_rota(self):
        b = bundle_completo()
        b['entry'][0]['resource']['subject']['reference'] = 'Patient/CC-000'
        assert any('regla 6' in e for e in validar_bundle(b))

    def test_se_detecta_una_seccion_obligatoria_ausente(self):
        b = bundle_completo()
        b['entry'][0]['resource']['section'] = [
            s for s in b['entry'][0]['resource']['section']
            if s['code']['coding'][0]['code'] != '48765-2']
        assert any('48765-2' in e for e in validar_bundle(b))

    def test_una_atencion_sin_diagnostico_no_se_transmite(self):
        """sectionProblems tiene entry 1..*: es la unica seccion que lo exige."""
        b = bundle_completo(historia=FakeHistoria(cie10_code=None))
        errores = validar_bundle(b)
        assert any('diagnostico' in e for e in errores)

    def test_se_avisa_del_profesional_sin_registro_medico(self):
        """Regla 8: el Practitioner debe poder validarse en RETHUS."""
        errores = validar_datos_minimos(
            FakePaciente(), FakeProfesional(medical_registration=None),
            FakeClinica(), FakeHistoria())
        assert any('RETHUS' in e for e in errores)

    def test_se_avisa_de_la_ips_sin_codigo_de_habilitacion(self):
        errores = validar_datos_minimos(
            FakePaciente(), FakeProfesional(),
            FakeClinica(habilitacion_code=None), FakeHistoria())
        assert any('habilitacion' in e for e in errores)

    def test_se_avisa_del_paciente_sin_fecha_de_nacimiento(self):
        errores = validar_datos_minimos(
            FakePaciente(birth_date=None), FakeProfesional(),
            FakeClinica(), FakeHistoria())
        assert any('nacimiento' in e for e in errores)

    def test_se_rechaza_un_tipo_de_documento_fuera_del_valueset(self):
        errores = validar_datos_minimos(
            FakePaciente(document_type='XX'), FakeProfesional(),
            FakeClinica(), FakeHistoria())
        assert any('ValueSet' in e for e in errores)

    def test_el_registro_civil_es_un_tipo_valido(self):
        """Un puesto rural atiende menores: RC tiene que pasar."""
        errores = validar_datos_minimos(
            FakePaciente(document_type='RC'), FakeProfesional(),
            FakeClinica(), FakeHistoria())
        assert not any('ValueSet' in e for e in errores)

    def test_el_permiso_de_proteccion_temporal_es_valido(self):
        """Poblacion migrante en frontera. PPT debe estar en la tabla."""
        assert T.tipo_documento_valido('PPT')


    def test_se_avisa_del_paciente_sin_pertenencia_etnica(self):
        """El perfil la marca 1..1. No se puede suponer ni dejar en blanco."""
        errores = validar_datos_minimos(
            FakePaciente(ethnic_group=None), FakeProfesional(),
            FakeClinica(), FakeHistoria())
        assert any('etnica' in e for e in errores)

    def test_se_avisa_del_paciente_sin_zona_de_residencia(self):
        errores = validar_datos_minimos(
            FakePaciente(zone=None), FakeProfesional(),
            FakeClinica(), FakeHistoria())
        assert any('zona de residencia' in e for e in errores)

    def test_se_avisa_del_paciente_sin_dato_de_discapacidad(self):
        """"Sin discapacidad" es un valor del catalogo, no la ausencia de dato."""
        errores = validar_datos_minimos(
            FakePaciente(disability=None), FakeProfesional(),
            FakeClinica(), FakeHistoria())
        assert any('discapacidad' in e for e in errores)

    def test_se_rechaza_un_grupo_etnico_fuera_del_catalogo(self):
        errores = validar_datos_minimos(
            FakePaciente(ethnic_group='77'), FakeProfesional(),
            FakeClinica(), FakeHistoria())
        assert any('catalogo' in e for e in errores)


class TestDatosDemograficos:
    """Extensiones que el perfil PatientRDA marca obligatorias."""

    def test_la_zona_rural_viaja_con_el_codigo_del_ministerio(self):
        b = bundle_completo(paciente=FakePaciente(zone='R'))
        direccion = _recurso(b, 'Patient')['address'][0]
        coding = direccion['extension'][0]['valueCoding']
        assert coding['code'] == '02'
        assert coding['display'] == 'Rural'

    def test_la_zona_urbana_se_traduce_igual(self):
        b = bundle_completo(paciente=FakePaciente(zone='U'))
        coding = _recurso(b, 'Patient')['address'][0]['extension'][0]['valueCoding']
        assert coding['code'] == '01'

    def test_la_etnia_y_la_discapacidad_van_como_extensiones(self):
        b = bundle_completo(paciente=FakePaciente(ethnic_group='1', disability='02'))
        extensiones = {e['url']: e['valueCoding'] for e in _recurso(b, 'Patient')['extension']}
        assert extensiones[T.EXT_ETNIA]['code'] == '1'
        assert extensiones[T.EXT_ETNIA]['display'] == 'Indigena'
        assert extensiones[T.EXT_DISCAPACIDAD]['code'] == '02'

    def test_sin_etnia_no_se_inventa_un_valor(self):
        """Un dato etnico falso en un registro nacional es peor que uno ausente."""
        b = bundle_completo(paciente=FakePaciente(ethnic_group=None))
        urls = {e['url'] for e in _recurso(b, 'Patient').get('extension', [])}
        assert T.EXT_ETNIA not in urls

    def test_la_nacionalidad_usa_el_codigo_iso_numerico(self):
        b = bundle_completo(paciente=FakePaciente(nationality_code='862'))
        extensiones = {e['url']: e['valueCoding'] for e in _recurso(b, 'Patient')['extension']}
        assert extensiones[T.EXT_NACIONALIDAD]['code'] == '862'
        assert extensiones[T.EXT_NACIONALIDAD]['system'] == T.CS_NACIONALIDAD

# --- Configuracion ----------------------------------------------------------

class TestConfiguracion:

    def test_sin_credenciales_la_transmision_esta_inactiva(self):
        config = IHCEConfig.from_env({})
        assert not config.is_enabled
        assert 'IHCE_CLIENT_ID' in config.faltantes()

    def test_con_credenciales_completas_se_activa(self):
        config = IHCEConfig.from_env(_entorno())
        assert config.is_configured
        assert config.is_enabled
        assert config.faltantes() == []

    def test_se_puede_apagar_sin_borrar_credenciales(self):
        entorno = dict(_entorno(), IHCE_ENABLED='0')
        config = IHCEConfig.from_env(entorno)
        assert config.is_configured
        assert not config.is_enabled

    def test_la_url_del_token_usa_el_tenant(self):
        config = IHCEConfig.from_env(_entorno())
        assert config.token_url == (
            'https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token')

    def test_describe_no_expone_secretos(self):
        texto = json.dumps(IHCEConfig.from_env(_entorno()).describe())
        assert 'secreto-muy-secreto' not in texto
        assert 'clave-suscripcion' not in texto


def _entorno():
    return {
        'IHCE_TENANT_ID': 'tenant-123',
        'IHCE_CLIENT_ID': 'cliente-abc',
        'IHCE_CLIENT_SECRET': 'secreto-muy-secreto',
        'IHCE_SUBSCRIPTION_KEY': 'clave-suscripcion',
        'IHCE_BASE_URL': 'https://api.ejemplo.gov.co/ihce',
        'IHCE_SCOPE': 'api://ihce/.default',
        'IHCE_HABILITACION': '0500112345',
    }



class TestTransporte:
    """Articulo 6.4 del manual: TLS 1.3 o superior, obligatorio."""

    def test_el_contexto_tls_rechaza_versiones_anteriores_a_1_3(self):
        import ssl
        from ihce.client import _contexto_tls

        contexto = _contexto_tls()
        assert contexto.minimum_version == ssl.TLSVersion.TLSv1_3
        assert contexto.check_hostname is True
        assert contexto.verify_mode == ssl.CERT_REQUIRED

# --- Cliente ----------------------------------------------------------------

class RespuestaFalsa:
    def __init__(self, cuerpo, codigo=200):
        self._cuerpo = json.dumps(cuerpo).encode('utf-8')
        self.code = codigo

    def read(self):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _error_http(codigo, cuerpo=None):
    import io as _io
    datos = json.dumps(cuerpo or {}).encode('utf-8')
    return urllib.error.HTTPError('http://x', codigo, 'error', {},
                                  _io.BytesIO(datos))


class TestCliente:

    def _cliente(self, respuestas):
        """`respuestas` es una lista; cada llamada consume una."""
        pendientes = list(respuestas)
        llamadas = []

        def opener(peticion, timeout=None):
            llamadas.append(peticion)
            siguiente = pendientes.pop(0)
            if isinstance(siguiente, Exception):
                raise siguiente
            return siguiente

        cliente = IHCEClient(IHCEConfig.from_env(_entorno()), opener=opener)
        cliente.llamadas = llamadas
        return cliente

    def test_obtiene_y_reutiliza_el_token(self):
        cliente = self._cliente([
            RespuestaFalsa({'access_token': 'tok-1', 'expires_in': 3600}),
        ])
        assert cliente.obtener_token() == 'tok-1'
        # La segunda vez no vuelve a pedirlo: solo hubo una llamada.
        assert cliente.obtener_token() == 'tok-1'
        assert len(cliente.llamadas) == 1

    def test_renueva_el_token_cuando_esta_por_expirar(self):
        cliente = self._cliente([
            RespuestaFalsa({'access_token': 'tok-1', 'expires_in': 30}),
            RespuestaFalsa({'access_token': 'tok-2', 'expires_in': 3600}),
        ])
        assert cliente.obtener_token() == 'tok-1'
        assert cliente.obtener_token() == 'tok-2'

    def test_el_envio_usa_los_headers_que_exige_el_manual(self):
        cliente = self._cliente([
            RespuestaFalsa({'access_token': 'tok', 'expires_in': 3600}),
            RespuestaFalsa({'id': 'RDA-999'}),
        ])
        cliente.enviar_rda(bundle_completo())
        envio = cliente.llamadas[-1]
        assert envio.get_header('Authorization') == 'Bearer tok'
        assert envio.get_header('Ocp-apim-subscription-key') == 'clave-suscripcion'
        assert envio.get_header('Content-type') == 'application/fhir+json'
        assert envio.full_url.endswith('/Composition/$enviar-rda-consulta')

    def test_el_409_no_es_un_fallo_sino_un_duplicado(self):
        cliente = self._cliente([
            RespuestaFalsa({'access_token': 'tok', 'expires_in': 3600}),
            _error_http(409, {'resourceType': 'OperationOutcome',
                              'issue': [{'diagnostics': 'RDA repetido'}]}),
        ])
        with pytest.raises(DuplicadoError) as exc:
            cliente.enviar_rda(bundle_completo())
        assert 'repetido' in (exc.value.detalle or '')

    def test_el_400_es_permanente_y_no_se_reintenta(self):
        cliente = self._cliente([
            RespuestaFalsa({'access_token': 'tok', 'expires_in': 3600}),
            _error_http(400, {'resourceType': 'OperationOutcome',
                              'issue': [{'diagnostics': 'Falta el campo X',
                                         'expression': ['Composition.section[2]']}]}),
        ])
        with pytest.raises(IHCEError) as exc:
            cliente.enviar_rda(bundle_completo())
        assert exc.value.permanente
        assert 'Falta el campo X' in exc.value.detalle
        assert 'Composition.section[2]' in exc.value.detalle

    def test_el_500_es_transitorio_y_se_reintenta(self):
        cliente = self._cliente([
            RespuestaFalsa({'access_token': 'tok', 'expires_in': 3600}),
            _error_http(503),
        ])
        with pytest.raises(IHCEError) as exc:
            cliente.enviar_rda(bundle_completo())
        assert not exc.value.permanente

    def test_un_401_reintenta_una_vez_con_token_nuevo(self):
        cliente = self._cliente([
            RespuestaFalsa({'access_token': 'viejo', 'expires_in': 3600}),
            _error_http(401),
            RespuestaFalsa({'access_token': 'nuevo', 'expires_in': 3600}),
            RespuestaFalsa({'id': 'RDA-1'}),
        ])
        assert cliente.enviar_rda(bundle_completo())['id'] == 'RDA-1'

    def test_un_401_persistente_no_entra_en_bucle(self):
        cliente = self._cliente([
            RespuestaFalsa({'access_token': 'a', 'expires_in': 3600}),
            _error_http(401),
            RespuestaFalsa({'access_token': 'b', 'expires_in': 3600}),
            _error_http(401),
        ])
        with pytest.raises(IHCEError) as exc:
            cliente.enviar_rda(bundle_completo())
        assert exc.value.permanente

    def test_sin_credenciales_falla_antes_de_tocar_la_red(self):
        cliente = IHCEClient(IHCEConfig.from_env({}),
                             opener=lambda *a, **k: pytest.fail('no debe llamar'))
        with pytest.raises(IHCEError) as exc:
            cliente.obtener_token()
        assert exc.value.permanente

    def test_el_mensaje_de_error_no_filtra_el_secreto(self):
        cliente = self._cliente([_error_http(401, {'error': 'invalid_client'})])
        with pytest.raises(IHCEError) as exc:
            cliente.obtener_token()
        assert 'secreto-muy-secreto' not in str(exc.value)
