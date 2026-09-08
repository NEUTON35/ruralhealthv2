# -*- coding: utf-8 -*-
"""PQRS del servicio de salud y farmacovigilancia.

Dos obligaciones que no existian: una que la plataforma ya prometia por escrito
en sus terminos, y otra que exige la Resolucion 1403 de 2007 al servicio
farmaceutico.
"""

from datetime import date, timedelta

import pytest

import service_quality as sq
from time_utils import colombia_now


# --- PQRS -------------------------------------------------------------------

class TestRadicarPQRS:

    def test_radicar_devuelve_un_numero(self, app, make_user):
        """Sin numero, quien reclama no puede hacer seguimiento ni acreditar
        que radico. Un canal de quejas sin radicado es un buzon sin fondo."""
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            r = sq.radicar_pqrs(_db, paciente, 'queja', 'No me dieron cita',
                                'Llame tres veces y no me asignaron cita de control')
            assert r.ticket_code.startswith('PQRS-')
            assert len(r.ticket_code) == 13

    def test_el_codigo_evita_caracteres_confundibles(self, app, make_user):
        """Se dicta por telefono o se anota a mano: I, O, 0 y 1 se confunden."""
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            for _ in range(20):
                codigo = sq.radicar_pqrs(
                    _db, paciente, 'peticion', 'Asunto de prueba',
                    'Detalle suficientemente largo para pasar').ticket_code
                cuerpo = codigo.split('-')[1]
                assert not set(cuerpo) & set('IO01'), codigo

    def test_el_plazo_se_fija_al_radicar(self, app, make_user):
        """Fijarlo al atenderla haria imposible saber que algo esta vencido
        sin que nadie lo haya mirado."""
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            r = sq.radicar_pqrs(_db, paciente, 'reclamo', 'Asunto',
                                'Detalle suficientemente largo para pasar')
            assert r.due_at is not None
            assert r.due_at > r.submitted_at

    def test_un_tipo_desconocido_se_rechaza(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            with pytest.raises(ValueError):
                sq.radicar_pqrs(_db, paciente, 'inventado', 'Asunto',
                                'Detalle suficientemente largo')

    def test_un_detalle_corto_se_rechaza(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            with pytest.raises(ValueError):
                sq.radicar_pqrs(_db, paciente, 'queja', 'Asunto', 'corto')

    def test_el_detalle_queda_cifrado_en_la_base(self, app, make_user):
        """Una queja puede describir una situacion clinica."""
        from sqlalchemy import text as sa_text
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            r = sq.radicar_pqrs(_db, paciente, 'queja', 'Asunto',
                                'Me trataron mal en la consulta de ayer')
            crudo = _db.session.execute(
                sa_text('SELECT detail FROM service_complaint WHERE id=:i'),
                {'i': r.id}).scalar()
            assert 'trataron mal' not in crudo


class TestResponderPQRS:

    def _radicada(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        return sq.radicar_pqrs(_db, paciente, 'queja', 'Asunto',
                               'Detalle suficientemente largo para pasar')

    def test_responder_exige_texto_sustantivo(self, app, make_user):
        from models import db as _db
        with app.app_context():
            r = self._radicada(app, make_user)
            with pytest.raises(ValueError):
                sq.responder_pqrs(_db, r, r.user_id, 'ok')

    def test_responder_cierra_y_deja_constancia(self, app, make_user):
        from models import PQRS_RESUELTA, db as _db
        with app.app_context():
            r = self._radicada(app, make_user)
            sq.responder_pqrs(_db, r, r.user_id,
                              'Se reprogramo la cita para el 15 y se llamo al paciente')
            assert r.status == PQRS_RESUELTA
            assert r.resolved_at is not None
            assert r.resolved_by_id == r.user_id

    def test_una_resuelta_ya_no_esta_vencida(self, app, make_user):
        from models import db as _db
        with app.app_context():
            r = self._radicada(app, make_user)
            r.due_at = colombia_now() - timedelta(days=1)
            _db.session.commit()
            assert r.is_overdue
            sq.responder_pqrs(_db, r, r.user_id,
                              'Se atendio el reclamo y se explico al usuario')
            assert not r.is_overdue

    def test_las_abiertas_salen_por_vencimiento(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            lejana = sq.radicar_pqrs(_db, paciente, 'peticion', 'Lejana',
                                     'Detalle suficientemente largo para pasar')
            cercana = sq.radicar_pqrs(_db, paciente, 'queja', 'Cercana',
                                      'Detalle suficientemente largo para pasar')
            cercana.due_at = colombia_now() - timedelta(days=2)
            _db.session.commit()
            abiertas = sq.pqrs_abiertas()
            assert abiertas[0].id == cercana.id
            assert lejana in abiertas


class TestPQRSEnLaAplicacion:

    def test_el_paciente_puede_radicar_y_ve_su_numero(self, app, client, login, make_user):
        from models import ServiceComplaint, db as _db
        paciente = make_user(role='patient')
        login(paciente.username)

        respuesta = client.post('/pqrs/radicar', data={
            'complaint_type': 'queja',
            'subject': 'No me entregaron el medicamento',
            'detail': 'Fui dos veces a la farmacia y me dijeron que no habia',
        }, follow_redirects=True)
        assert respuesta.status_code == 200

        with app.app_context():
            r = ServiceComplaint.query.filter_by(user_id=paciente.id).first()
            assert r is not None
            assert r.ticket_code.encode() in respuesta.data

    def test_un_paciente_no_ve_los_radicados_de_otro(self, app, client, login, make_user):
        from models import db as _db
        uno = make_user(role='patient')
        otro = make_user(role='patient')
        with app.app_context():
            ajeno = sq.radicar_pqrs(_db, otro, 'queja', 'Asunto del otro',
                                    'Detalle suficientemente largo para pasar')
            codigo = ajeno.ticket_code

        login(uno.username)
        html = client.get('/pqrs/').data.decode('utf-8')
        assert codigo not in html

    def test_un_paciente_no_entra_a_la_bandeja_de_gestion(self, app, client, login, make_user):
        paciente = make_user(role='patient')
        login(paciente.username)
        assert client.get('/pqrs/gestion').status_code in (302, 403)


# --- Farmacovigilancia ------------------------------------------------------

class TestFarmacovigilancia:

    def test_registrar_una_sospecha(self, app, make_user):
        from models import FARMACO_SOSPECHA, db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, 'Amoxicilina',
                'Exantema generalizado a las 6 horas de la primera dosis')
            assert evento.status == FARMACO_SOSPECHA
            assert evento.due_at is not None

    def test_una_reaccion_seria_tiene_plazo_de_72_horas(self, app, make_user):
        from models import SERIEDAD_SERIA, db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, 'Penicilina', 'Anafilaxia con broncoespasmo',
                seriedad=SERIEDAD_SERIA)
            assert evento.is_serious
            assert evento.due_at - evento.detected_at <= timedelta(hours=72)

    def test_una_no_seria_tiene_plazo_de_un_mes(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, 'Ibuprofeno', 'Molestia gastrica leve')
            assert evento.due_at - evento.detected_at == timedelta(days=30)

    def test_el_medicamento_se_normaliza_para_poder_agrupar(self, app, make_user):
        """Sin normalizar no se puede ver que varios eventos son del mismo
        principio activo sin descifrar toda la tabla."""
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, '  AMOXICILINA  ', 'Reaccion cutanea observada')
            assert evento.medication_normalized
            assert evento.medication_normalized == evento.medication_normalized.strip()

    def test_reportar_al_invima_exige_la_referencia(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, 'Amoxicilina', 'Exantema generalizado observado')
            with pytest.raises(ValueError) as exc:
                sq.marcar_reportado_invima(_db, evento, paciente.id, '')
            assert 'INVIMA' in str(exc.value)

    def test_reportar_guarda_quien_cuando_y_con_que_numero(self, app, make_user):
        from models import FARMACO_REPORTADO, db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, 'Amoxicilina', 'Exantema generalizado observado')
            sq.marcar_reportado_invima(_db, evento, paciente.id, 'FOREAM-2026-451')
            assert evento.status == FARMACO_REPORTADO
            assert evento.invima_reference == 'FOREAM-2026-451'
            assert evento.reported_at is not None

    def test_descartar_exige_justificacion(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, 'Amoxicilina', 'Exantema generalizado observado')
            with pytest.raises(ValueError):
                sq.descartar_evento(_db, evento, paciente.id, 'no')

    def test_un_evento_vencido_se_marca(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, 'Amoxicilina', 'Exantema generalizado observado')
            evento.due_at = colombia_now() - timedelta(hours=1)
            _db.session.commit()
            assert evento.is_overdue
            assert evento in sq.eventos_pendientes(solo_vencidos=True)

    def test_varios_eventos_del_mismo_farmaco_se_agrupan(self, app, make_user):
        """No es analisis de senal farmacoepidemiologico, que exige
        denominadores de exposicion que la aplicacion no tiene. Es lo que si
        puede hacerse: mostrar que un principio activo lleva varias sospechas."""
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            for i in range(3):
                sq.registrar_evento_adverso(
                    _db, paciente, 'Amoxicilina',
                    'Reaccion cutanea observada numero %d' % i)
            senales = sq.senal_por_medicamento(minimo=3)
            assert senales
            assert senales[0][1] >= 3

    def test_los_descartados_no_cuentan_como_senal(self, app, make_user):
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            eventos = [sq.registrar_evento_adverso(
                _db, paciente, 'Ibuprofeno',
                'Reaccion observada numero %d' % i) for i in range(3)]
            sq.descartar_evento(_db, eventos[0], paciente.id,
                                'Se confirmo que la causa fue otra distinta')
            assert sq.senal_por_medicamento(minimo=3) == []

    def test_la_descripcion_queda_cifrada(self, app, make_user):
        from sqlalchemy import text as sa_text
        from models import db as _db
        paciente = make_user(role='patient')
        with app.app_context():
            evento = sq.registrar_evento_adverso(
                _db, paciente, 'Amoxicilina', 'Exantema generalizado grave')
            crudo = _db.session.execute(
                sa_text('SELECT description FROM adverse_drug_event WHERE id=:i'),
                {'i': evento.id}).scalar()
            assert 'Exantema' not in crudo
