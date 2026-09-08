# -*- coding: utf-8 -*-
"""Paginas de error.

Lo que se comprueba, sobre todo, es que la pagina de error no dependa de nada
que pueda estar roto cuando se dibuja: si un 500 lo causa la base de datos, la
pagina que lo informa no puede consultar la base de datos.
"""

import re

import pytest


class TestContenido:

    def test_el_404_dice_que_hacer_y_no_solo_que_paso(self, client):
        html = client.get('/una-ruta-que-no-existe').data.decode('utf-8')
        assert 'no encontrada' in html.lower()
        # Un error util dice el siguiente paso.
        assert 'Revise el enlace' in html

    def test_cada_codigo_tiene_su_propio_texto(self, app):
        """Un mensaje generico para todo no informa de nada."""
        with app.test_request_context():
            from flask import render_template
            textos = set()
            for code in (400, 401, 403, 404, 405, 413, 429, 503):
                html = render_template('error.html', code=code,
                                       title='t%d' % code, message='m%d' % code)
                textos.add(html)
            assert len(textos) == 8

    def test_el_403_no_explica_como_funciona_el_permiso(self, app, client, login, make_user):
        """Detallar por que se denego le dice a quien no deberia estar ahi
        como esta construido el control de acceso."""
        paciente = make_user(role='patient')
        login(paciente.username)
        html = client.get('/superadmin/').data.decode('utf-8')
        for filtracion in ('role_required', 'clinic_id', 'superadmin', 'decorator'):
            assert filtracion not in html, filtracion


class TestUrgencias:
    """Si la plataforma se cae en mitad de una consulta, lo que el paciente
    necesita saber no es el codigo del error: es a donde ir."""

    def test_el_fallo_del_sistema_recuerda_la_linea_123(self, app):
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=500, title='x', message='y')
        assert '123' in html
        assert 'urgencias' in html.lower()

    def test_el_404_no_habla_de_urgencias(self, app):
        """Seria ruido: un enlace roto no es una emergencia."""
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=404, title='x', message='y')
        assert 'linea 123' not in html.lower()
        assert 'línea 123' not in html.lower()


class TestAutonomia:
    """La pagina no puede depender de lo que quiza este roto."""

    def test_no_hereda_de_la_plantilla_base(self):
        """`base.html` consulta sesion, usuario y clinica. Un 500 causado por
        la base de datos haria fallar tambien a la pagina que lo informa."""
        import io
        fuente = io.open('templates/error.html', encoding='utf-8').read()
        assert 'extends' not in fuente
        assert '<!doctype html>' in fuente.lower()

    def test_no_depende_de_css_ni_javascript_externo(self):
        """Si el CSS no carga, el error se veria como texto plano sin formato;
        si dependiera de JS, podria no verse en absoluto."""
        import io
        fuente = io.open('templates/error.html', encoding='utf-8').read()
        assert '<link' not in fuente
        assert '<script' not in fuente
        assert 'data-lucide' not in fuente
        assert '<style>' in fuente

    def test_se_dibuja_sin_usuario_en_sesion(self, app):
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=500, title='x', message='y')
        assert '<h1>' in html

    def test_se_dibuja_aunque_la_base_de_datos_falle(self, app, monkeypatch):
        """La prueba que justifica todo lo anterior."""
        from models import db

        def revienta(*a, **k):
            raise RuntimeError('base de datos caida')

        monkeypatch.setattr(db.session, 'execute', revienta)
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=500,
                                   title='Error del sistema',
                                   message='Fallo del lado del servidor')
        assert 'Error del sistema' in html


class TestAccesibilidad:

    def test_declara_el_idioma(self, app):
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=404, title='x', message='y')
        assert 'lang="es"' in html

    def test_tiene_un_solo_encabezado_principal(self, app):
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=404, title='x', message='y')
        assert len(re.findall(r'<h1[ >]', html)) == 1

    def test_es_adaptable_a_pantalla_pequena(self, app):
        """Se consulta desde el telefono, con frecuencia el unico dispositivo."""
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=404, title='x', message='y')
        assert 'width=device-width' in html

    def test_contempla_el_tema_oscuro(self, app):
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=404, title='x', message='y')
        assert 'prefers-color-scheme: dark' in html


class TestAPI:
    """Las rutas de API devuelven JSON, no HTML."""

    def test_la_api_responde_json(self, client):
        respuesta = client.get('/api/no-existe')
        assert respuesta.status_code == 404
        assert respuesta.is_json
        assert respuesta.get_json()['error'] == 'not_found'

    def test_la_api_no_devuelve_la_pagina_html(self, client):
        cuerpo = client.get('/api/no-existe').data.decode('utf-8')
        assert '<html' not in cuerpo.lower()


class TestIncidente:

    def test_el_codigo_del_incidente_se_muestra_cuando_existe(self, app):
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=500, title='x',
                                   message='y', incident='a1b2c3d4')
        assert 'a1b2c3d4' in html

    def test_sin_incidente_no_aparece_el_bloque_vacio(self, app):
        """La clase CSS existe siempre; lo que no debe salir es el bloque."""
        with app.test_request_context():
            from flask import render_template
            html = render_template('error.html', code=404, title='x', message='y')
        assert 'Codigo del incidente' not in html
        assert 'Código del incidente' not in html
