# -*- coding: utf-8 -*-
"""RIPS en JSON (Resolucion 948 de 2026).

El formato plano de la Resolucion 3374 de 2000 esta derogado desde el 30 de
junio de 2023. Estas pruebas cubren el formato vigente y, sobre todo, que el
generador siga sin inventar datos: la regla RVC096 rechaza los codigos de
relleno, asi que rellenar no es una opcion ni siquiera pragmatica.
"""

import json
from datetime import date, timedelta

import pytest

import rips_json
from time_utils import colombia_now


@pytest.fixture
def periodo():
    return colombia_now() - timedelta(days=1), colombia_now() + timedelta(days=1)


@pytest.fixture
def atencion_completa(app, make_user):
    from models import Clinic, MedicalHistory, db as _db

    paciente = make_user(
        role='patient', birth_date=date(1990, 5, 15), sex='F',
        document_type='CC', first_name='Ana', first_surname='Perez',
        department_code='05', municipality_code='001', zone='R',
        affiliation_regime='subsidiado', insurer_code='EPS123',
        nationality_code='170')
    doctor = make_user(role='doctor', medical_registration='RM-1',
                       document_type='CC')

    with app.app_context():
        clinica = _db.session.get(Clinic, 1)
        clinica.habilitacion_code = '05001234501'
        clinica.nit = '900123456'
        historia = MedicalHistory(
            clinic_id=1, patient_id=paciente.id, doctor_id=doctor.id,
            record_type='consulta', cie10_code='Z000', cups_code='890201',
            summary='Consulta de control', created_at=colombia_now(),
            external_cause='26', consultation_purpose='15', care_modality='01')
        _db.session.add(historia)
        _db.session.commit()
        return {'patient': paciente, 'doctor': doctor, 'history_id': historia.id}


class TestEstructura:

    def test_el_documento_tiene_la_cabecera_de_la_norma(self, app, atencion_completa, periodo):
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        for campo in ('numDocumentoIdObligado', 'numFactura', 'tipoNota',
                      'numNota', 'usuarios', 'servicios'):
            assert campo in doc, campo

    def test_los_servicios_agrupan_las_consultas(self, app, atencion_completa, periodo):
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        assert 'consultas' in doc['servicios']
        assert len(doc['servicios']['consultas']) == 1

    def test_la_consulta_lleva_los_campos_del_anexo(self, app, atencion_completa, periodo):
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        consulta = doc['servicios']['consultas'][0]
        esperados = (
            'codPrestador', 'fechaInicioAtencion', 'numAutorizacion',
            'codConsulta', 'modalidadGrupoServicioTecSal', 'grupoServicios',
            'codServicio', 'finalidadTecnologiaSalud', 'causaMotivoAtencion',
            'codDiagnosticoPrincipal', 'codDiagnosticoRelacionado1',
            'tipoDiagnosticoPrincipal', 'tipoDocumentoIdentificacion',
            'numDocumentoIdentificacion', 'vrServicio', 'conceptoRecaudo',
            'valorPagoModerador', 'numFEVPagoModerador', 'consecutivo')
        for campo in esperados:
            assert campo in consulta, campo

    def test_la_fecha_va_con_hora_y_sin_segundos(self, app, atencion_completa, periodo):
        """`fechaInicioAtencion` es de 16 caracteres: AAAA-MM-DD HH:MM."""
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        fecha = doc['servicios']['consultas'][0]['fechaInicioAtencion']
        assert len(fecha) == 16
        assert fecha[10] == ' '

    def test_la_autorizacion_ausente_va_como_null_no_como_vacio(
            self, app, atencion_completa, periodo):
        """El anexo dice null cuando el servicio no requiere autorizacion."""
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        assert doc['servicios']['consultas'][0]['numAutorizacion'] is None

    def test_los_consecutivos_empiezan_en_uno(self, app, atencion_completa, periodo):
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        assert doc['usuarios'][0]['consecutivo'] == 1
        assert doc['servicios']['consultas'][0]['consecutivo'] == 1

    def test_el_documento_serializa_a_json_valido(self, app, atencion_completa, periodo):
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        recargado = json.loads(rips_json.serializar(doc))
        assert recargado['numFactura'] == 'FE-1'

    def test_el_usuario_no_se_repite_entre_atenciones(self, app, atencion_completa, periodo):
        """Dos consultas del mismo paciente van con un solo usuario."""
        from models import MedicalHistory, db as _db
        with app.app_context():
            _db.session.add(MedicalHistory(
                clinic_id=1, patient_id=atencion_completa['patient'].id,
                doctor_id=atencion_completa['doctor'].id, record_type='consulta',
                cie10_code='Z000', cups_code='890201', summary='Segunda',
                created_at=colombia_now(), external_cause='26',
                consultation_purpose='15', care_modality='01'))
            _db.session.commit()
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        assert len(doc['usuarios']) == 1
        assert len(doc['servicios']['consultas']) == 2


class TestZonaInvertida:
    """La zona del RIPS va al reves que la del IHCE. Es facil de confundir."""

    def test_rural_es_01_en_el_rips(self, app, atencion_completa, periodo):
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        assert doc['usuarios'][0]['codZonaTerritorialResidencia'] == '01'

    def test_urbano_es_02_en_el_rips(self, app, atencion_completa, periodo):
        from models import User, db as _db
        with app.app_context():
            _db.session.get(User, atencion_completa['patient'].id).zone = 'U'
            _db.session.commit()
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        assert doc['usuarios'][0]['codZonaTerritorialResidencia'] == '02'

    def test_no_coincide_con_la_del_ihce(self):
        """Prueba de regresion: si alguien las unifica, esto falla."""
        from ihce import terminology as T
        assert T.ZONA_A_CODIGO['R'] == '02'   # IHCE: rural es 02
        assert rips_json._zona(type('P', (), {'zone': 'R'})()) == '01'


class TestNoInventaDatos:

    def test_sin_causa_externa_se_niega(self, app, atencion_completa, periodo):
        from models import MedicalHistory, db as _db
        with app.app_context():
            _db.session.get(MedicalHistory,
                            atencion_completa['history_id']).external_cause = None
            _db.session.commit()
            with pytest.raises(rips_json.RIPSJSONError) as exc:
                rips_json.generar(1, *periodo, num_factura='FE-1')
            assert any('causa externa' in i['message'] for i in exc.value.issues)

    def test_un_codigo_fuera_del_catalogo_se_rechaza(self, app, atencion_completa, periodo):
        """RVC096 bloquea los codigos de relleno."""
        from models import MedicalHistory, db as _db
        with app.app_context():
            _db.session.get(MedicalHistory,
                            atencion_completa['history_id']).external_cause = '99'
            _db.session.commit()
            with pytest.raises(rips_json.RIPSJSONError) as exc:
                rips_json.generar(1, *periodo, num_factura='FE-1')
            assert any('RVC096' in i['message'] for i in exc.value.issues)

    def test_sin_regimen_no_se_supone_el_tipo_de_usuario(self, app, atencion_completa, periodo):
        """De el depende quien paga: suponerlo es reportar un pagador falso."""
        from models import User, db as _db
        with app.app_context():
            _db.session.get(User,
                            atencion_completa['patient'].id).affiliation_regime = None
            _db.session.commit()
            with pytest.raises(rips_json.RIPSJSONError) as exc:
                rips_json.generar(1, *periodo, num_factura='FE-1')
            assert any('tipo de usuario' in i['message'] for i in exc.value.issues)

    def test_sin_numero_de_factura_se_niega(self, app, atencion_completa, periodo):
        """El RIPS es soporte de la factura: sin factura no soporta nada."""
        with app.app_context():
            with pytest.raises(rips_json.RIPSJSONError) as exc:
                rips_json.generar(1, *periodo, num_factura='')
            assert any('factura' in i['message'].lower() for i in exc.value.issues)

    def test_sin_habilitacion_se_niega(self, app, atencion_completa, periodo):
        from models import Clinic, db as _db
        with app.app_context():
            _db.session.get(Clinic, 1).habilitacion_code = None
            _db.session.commit()
            with pytest.raises(rips_json.RIPSJSONError) as exc:
                rips_json.generar(1, *periodo, num_factura='FE-1')
            assert any('habilitacion' in i['message'] for i in exc.value.issues)

    def test_el_regimen_determina_el_tipo_de_usuario(self, app, atencion_completa, periodo):
        with app.app_context():
            doc, _ = rips_json.generar(1, *periodo, num_factura='FE-1')
        assert doc['usuarios'][0]['tipoUsuario'] == '04'   # subsidiado


class TestHonestidadDelAlcance:
    """El modulo tiene que decir lo que no hace, no dejarlo suponer."""

    def test_los_avisos_dicen_que_no_esta_radicado(self, app, atencion_completa, periodo):
        with app.app_context():
            _, avisos = rips_json.generar(1, *periodo, num_factura='FE-1')
        texto = ' '.join(avisos)
        assert 'NO esta radicado' in texto
        assert 'CUV' in texto

    def test_los_avisos_listan_los_campos_de_la_948_que_faltan(
            self, app, atencion_completa, periodo):
        with app.app_context():
            _, avisos = rips_json.generar(1, *periodo, num_factura='FE-1')
        texto = ' '.join(avisos)
        for campo in ('CIE-11', 'Codigo VIDA', 'SIRAS'):
            assert campo in texto, campo

    def test_preview_no_construye_el_archivo(self, app, atencion_completa, periodo):
        with app.app_context():
            informe = rips_json.preview(1, *periodo, num_factura='FE-1')
        assert informe['ok']
        assert informe['usuarios'] == 1
        assert informe['consultas'] == 1

    def test_preview_reporta_los_faltantes_sin_lanzar(self, app, atencion_completa, periodo):
        from models import MedicalHistory, db as _db
        with app.app_context():
            _db.session.get(MedicalHistory,
                            atencion_completa['history_id']).cie10_code = None
            _db.session.commit()
            informe = rips_json.preview(1, *periodo, num_factura='FE-1')
        assert not informe['ok']
        assert informe['issues']
