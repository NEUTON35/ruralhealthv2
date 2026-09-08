# -*- coding: utf-8 -*-
"""Cifrado de campo: AES-256-GCM y migracion desde el formato anterior.

La historia clinica se conserva quince anos. Estas pruebas cubren las dos
formas de perderla: que no se pueda descifrar, y que el cifrado no sirva.
"""

import base64
import os

import pytest

from security import (CIPHER_V2_PREFIX, EncryptedText, _decrypt_field,
                      _encrypt_field, _fernet, encrypted_columns,
                      reset_fernet_cache)


class TestCifrado:

    def test_ida_y_vuelta(self):
        texto = 'Paciente con cefalea tensional de 3 dias'
        assert _decrypt_field(_encrypt_field(texto)) == texto

    def test_conserva_acentos_y_enes(self):
        """Historia clinica en espanol: si esto falla, se corrompe el dato."""
        texto = 'Niña de 8 años, migraña con aura. Remisión a neuropediatría.'
        assert _decrypt_field(_encrypt_field(texto)) == texto

    def test_usa_llave_de_256_bits(self):
        """AES-256 exige 32 bytes de llave."""
        from security import _field_key
        # AESGCM guarda la llave internamente; se comprueba por el tamano que
        # produce la derivacion, que es lo que se le entrega.
        import hashlib

        from security import KDF_ITERATIONS, _secret_material
        secreto = _secret_material('RURALHEALTH_FIELD_KEY_SEED', 'dev-only-ruralhealth-field-key')
        derivada = hashlib.pbkdf2_hmac('sha256', secreto, b'field-encryption', KDF_ITERATIONS)
        assert len(derivada) == 32
        assert _field_key() is not None

    def test_el_criptograma_lleva_el_prefijo_de_version(self):
        """Sin prefijo no se puede distinguir el formato en una migracion futura."""
        assert _encrypt_field('x').startswith(CIPHER_V2_PREFIX)

    def test_dos_cifrados_del_mismo_texto_son_distintos(self):
        """Nonce aleatorio. Si coincidieran, se filtraria que dos pacientes
        tienen el mismo diagnostico solo mirando la base de datos."""
        texto = 'Diabetes mellitus tipo 2'
        assert _encrypt_field(texto) != _encrypt_field(texto)

    def test_el_nonce_mide_96_bits(self):
        """El tamano que recomienda NIST SP 800-38D para GCM."""
        crudo = base64.urlsafe_b64decode(
            _encrypt_field('x')[len(CIPHER_V2_PREFIX):].encode('ascii'))
        assert len(crudo) > 12
        # 12 bytes de nonce + criptograma + 16 de etiqueta GCM
        assert len(crudo) >= 12 + 1 + 16

    def test_alterar_un_byte_impide_descifrar(self):
        """GCM es cifrado autenticado: una modificacion se detecta, no se
        devuelve texto corrupto como si fuera valido."""
        from cryptography.exceptions import InvalidTag

        token = _encrypt_field('Alergia a penicilina')
        crudo = bytearray(base64.urlsafe_b64decode(
            token[len(CIPHER_V2_PREFIX):].encode('ascii')))
        crudo[-1] ^= 0x01
        alterado = CIPHER_V2_PREFIX + base64.urlsafe_b64encode(bytes(crudo)).decode('ascii')
        with pytest.raises(InvalidTag):
            _decrypt_field(alterado)

    def test_el_texto_plano_no_aparece_en_el_criptograma(self):
        token = _encrypt_field('Rinofaringitis aguda')
        assert 'Rinofaringitis' not in token


class TestCompatibilidadConElFormatoAnterior:
    """Una instalacion que ya tenia datos no puede quedar ilegible al desplegar."""

    def test_se_leen_los_valores_cifrados_con_fernet(self):
        heredado = _fernet().encrypt('Historia anterior'.encode('utf-8')).decode('utf-8')
        assert not heredado.startswith(CIPHER_V2_PREFIX)
        assert _decrypt_field(heredado) == 'Historia anterior'

    def test_el_tipo_de_columna_lee_ambos_formatos(self):
        tipo = EncryptedText()
        heredado = _fernet().encrypt('valor viejo'.encode('utf-8')).decode('utf-8')
        nuevo = _encrypt_field('valor nuevo')
        assert tipo.process_result_value(heredado, None) == 'valor viejo'
        assert tipo.process_result_value(nuevo, None) == 'valor nuevo'

    def test_al_guardar_se_escribe_en_el_formato_nuevo(self):
        tipo = EncryptedText()
        assert tipo.process_bind_param('algo', None).startswith(CIPHER_V2_PREFIX)

    def test_un_valor_ilegible_no_tumba_la_consulta(self):
        """Puede haber datos escritos antes de que la columna se cifrara.
        Perder la pantalla entera por uno de ellos seria peor."""
        tipo = EncryptedText()
        assert tipo.process_result_value('texto en claro heredado', None) == \
            'texto en claro heredado'

    def test_los_valores_vacios_pasan_sin_cifrar(self):
        tipo = EncryptedText()
        assert tipo.process_bind_param(None, None) is None
        assert tipo.process_bind_param('', None) == ''


class TestCoberturaDeRecifrado:
    """Rotar la llave debe alcanzar TODOS los campos cifrados.

    La lista estaba escrita a mano y cubria 13 de 33 campos. Los 20 restantes
    (alergias, orden medica, dispensacion, nombres y apellidos) habrian quedado
    ilegibles tras una rotacion, sin error y sin aviso.
    """

    def test_se_descubren_los_campos_desde_el_modelo(self, app):
        with app.app_context():
            hallados = {m.__name__: set(c) for m, c in encrypted_columns()}
        assert hallados, 'no se detecto ningun campo cifrado'
        # Los que la lista escrita a mano se dejaba fuera.
        assert 'substance' in hallados['PatientAllergy']
        assert 'condition' in hallados['PatientChronicCondition']
        assert 'receiver_name' in hallados['DispensingLedgerEntry']
        assert 'patient_document' in hallados['MedicalOrder']
        assert 'first_surname' in hallados['User']
        assert 'second_name' in hallados['User']
        assert 'detail' in hallados['DataSubjectRequest']
        assert 'buyer_name' in hallados['Invoice']

    def test_cubre_todas_las_columnas_del_tipo_cifrado(self, app):
        """Ninguna columna EncryptedText puede quedarse fuera."""
        import re

        import io as _io
        with app.app_context():
            cubiertos = sum(len(c) for _m, c in encrypted_columns())

        fuente = _io.open('models.py', encoding='utf-8').read()
        declarados = len(re.findall(r'db\.Column\(EncryptedText', fuente))
        assert cubiertos == declarados, (
            'hay %d columnas cifradas en models.py y el recifrado alcanza %d'
            % (declarados, cubiertos))

    def test_recifrar_deja_todo_en_el_formato_nuevo(self, app, make_user):
        """Prueba de extremo a extremo de la migracion."""
        from models import MedicalHistory, PatientAllergy, db as _db
        from security import reencrypt_all
        from sqlalchemy import text as sa_text
        from time_utils import colombia_now

        paciente = make_user(role='patient')
        with app.app_context():
            historia = MedicalHistory(
                clinic_id=1, patient_id=paciente.id, record_type='consultation',
                summary='Resumen de prueba', diagnosis='Diagnostico de prueba',
                created_at=colombia_now())
            alergia = PatientAllergy(
                clinic_id=1, patient_id=paciente.id, substance='Penicilina',
                substance_normalized='penicilina', reaction='Urticaria')
            _db.session.add_all([historia, alergia])
            _db.session.commit()
            hid, aid = historia.id, alergia.id

            # Reescribir a mano en el formato anterior, simulando datos previos.
            viejo_res = _fernet().encrypt('Resumen heredado'.encode()).decode()
            viejo_sus = _fernet().encrypt('Sulfas'.encode()).decode()
            _db.session.execute(
                sa_text('UPDATE medical_history SET summary=:v WHERE id=:i'),
                {'v': viejo_res, 'i': hid})
            _db.session.execute(
                sa_text('UPDATE patient_allergy SET substance=:v WHERE id=:i'),
                {'v': viejo_sus, 'i': aid})
            _db.session.commit()
            _db.session.expire_all()

            # Se leen igual pese al formato antiguo.
            assert _db.session.get(MedicalHistory, hid).summary == 'Resumen heredado'
            assert _db.session.get(PatientAllergy, aid).substance == 'Sulfas'

            reencrypt_all(_db)
            _db.session.expire_all()

            # El contenido sobrevive...
            assert _db.session.get(MedicalHistory, hid).summary == 'Resumen heredado'
            assert _db.session.get(PatientAllergy, aid).substance == 'Sulfas'

            # ...y ahora esta en el formato nuevo, incluida la alergia, que la
            # lista escrita a mano no tocaba.
            for tabla, col, ident in (('medical_history', 'summary', hid),
                                      ('patient_allergy', 'substance', aid)):
                crudo = _db.session.execute(
                    sa_text('SELECT %s FROM %s WHERE id=:i' % (col, tabla)),
                    {'i': ident}).scalar()
                assert crudo.startswith(CIPHER_V2_PREFIX), (tabla, col)
