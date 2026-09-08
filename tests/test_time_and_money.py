# -*- coding: utf-8 -*-
"""`time_utils` y `monetization`: dos modulos tempranos que no tenian pruebas.

Se cubren sobre todo los errores que ya estaban ahi, para que no vuelvan.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import monetization as M
import time_utils as T


# --- Tiempo -----------------------------------------------------------------

class TestZonaHoraria:

    def test_ahora_no_lleva_zona(self):
        """Convencion de toda la aplicacion: las fechas van naive."""
        assert T.colombia_now().tzinfo is None

    def test_hoy_usa_la_hora_de_colombia_no_la_del_servidor(self):
        """Entre las 19:00 y medianoche en Bogota, un servidor en UTC ya esta
        en el dia siguiente. Una agenda corrida un dia no es un detalle."""
        assert T.colombia_today() == T.colombia_now().date()

    def test_una_fecha_con_zona_se_convierte_antes_de_compararla(self):
        from datetime import timezone
        utc = datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc)
        # 03:00 UTC son las 22:00 del dia anterior en Colombia.
        assert T.to_naive(utc) == datetime(2026, 9, 6, 22, 0)
        assert T.to_naive(utc).tzinfo is None

    def test_una_naive_se_toma_como_hora_de_colombia(self):
        d = datetime(2026, 9, 7, 10, 0)
        assert T.to_naive(d) == d


class TestLecturaDeFechas:

    def test_lee_una_fecha_de_formulario(self):
        assert T.parse_date('2026-09-07') == date(2026, 9, 7)

    def test_una_fecha_invalida_no_revienta(self):
        """Lo que escribe un usuario es entrada esperable, no un fallo."""
        assert T.parse_date('no es fecha') is None
        assert T.parse_date('') is None
        assert T.parse_date(None, default=date(2026, 1, 1)) == date(2026, 1, 1)

    def test_lee_fecha_y_hora_en_los_dos_formatos(self):
        assert T.parse_datetime('2026-09-07 14:30') == datetime(2026, 9, 7, 14, 30)
        assert T.parse_datetime('2026-09-07T14:30') == datetime(2026, 9, 7, 14, 30)


class TestRangoDeDia:

    def test_el_rango_cubre_el_dia_completo(self):
        """Comparar una columna DateTime contra un date excluye todo lo del dia
        despues de medianoche, y el informe sale incompleto sin avisar."""
        inicio, fin = T.day_bounds(date(2026, 9, 7))
        assert inicio == datetime(2026, 9, 7, 0, 0)
        assert fin.date() == date(2026, 9, 7)
        assert fin.hour == 23 and fin.minute == 59

    def test_una_atencion_de_las_once_de_la_noche_entra_en_el_rango(self):
        inicio, fin = T.day_bounds(date(2026, 9, 7))
        atencion = datetime(2026, 9, 7, 23, 15)
        assert inicio <= atencion <= fin


class TestSemanaEpidemiologica:
    """La vigilancia en salud publica se organiza por semanas epidemiologicas,
    que van de domingo a sabado y no coinciden con las del calendario."""

    def test_devuelve_ano_y_semana(self):
        anio, semana = T.epidemiological_week(date(2026, 9, 7))
        assert anio == 2026
        assert 1 <= semana <= 53

    def test_la_semana_va_de_domingo_a_sabado(self):
        domingo = date(2026, 3, 1)      # domingo
        sabado = date(2026, 3, 7)       # sabado siguiente
        assert T.epidemiological_week(domingo) == T.epidemiological_week(sabado)

    def test_el_sabado_y_el_domingo_siguiente_son_semanas_distintas(self):
        sabado = date(2026, 3, 7)
        domingo = date(2026, 3, 8)
        assert T.epidemiological_week(sabado) != T.epidemiological_week(domingo)

    def test_el_cierre_semanal_cae_en_sabado(self):
        """Es el plazo real de una notificacion semanal al Sivigila. Sumar
        siete dias da una fecha que no coincide con el cierre del INS."""
        cierre = T.end_of_epidemiological_week(datetime(2026, 3, 4, 10, 0))
        assert cierre.weekday() == 5          # sabado
        assert cierre.date() == date(2026, 3, 7)

    def test_el_cierre_nunca_queda_antes_del_momento(self):
        for dia in range(1, 29):
            momento = datetime(2026, 3, dia, 12, 0)
            assert T.end_of_epidemiological_week(momento) >= momento


class TestEdad:

    def test_edad_en_anos_cumplidos(self):
        assert T.age_on(date(1990, 5, 15), date(2026, 9, 7)) == 36

    def test_el_dia_antes_del_cumpleanos_aun_no_suma(self):
        assert T.age_on(date(1990, 9, 8), date(2026, 9, 7)) == 35

    def test_el_dia_del_cumpleanos_ya_suma(self):
        assert T.age_on(date(1990, 9, 7), date(2026, 9, 7)) == 36

    def test_no_se_desplaza_en_anos_bisiestos(self):
        """Dividir los dias entre 365 desplaza la edad, y la dosificacion
        pediatrica depende de ella."""
        # Un nacido el 29 de febrero cumple anos igual.
        assert T.age_on(date(2016, 2, 29), date(2026, 3, 1)) == 10

    def test_una_fecha_futura_no_da_edad_negativa(self):
        assert T.age_on(date(2030, 1, 1), date(2026, 9, 7)) is None


# --- Dinero -----------------------------------------------------------------

class TestDescuentos:

    def test_el_descuento_se_calcula_sin_coma_flotante(self):
        """billing.py ya usaba enteros por esto. La aplicacion tenia dos
        criterios para el mismo peso: el que se muestra y el que se factura."""
        assert M.discounted_amount(100000, 15) == 85000

    def test_el_resultado_es_un_entero_de_pesos(self):
        """El peso colombiano no tiene subdivision en circulacion."""
        resultado = M.discounted_amount(33333, 10)
        assert isinstance(resultado, int)

    def test_sin_descuento_devuelve_el_mismo_importe(self):
        assert M.discounted_amount(45000, 0) == 45000

    def test_el_descuento_se_limita_entre_cero_y_cien(self):
        assert M.discounted_amount(50000, 150) == 0
        assert M.discounted_amount(50000, -20) == 50000

    def test_el_caso_clasico_de_la_coma_flotante(self):
        """Con float, 0.1 + 0.2 no es 0.3. Sobre importes grandes y sumados
        muchas veces, eso descuadra una factura."""
        assert M.discounted_amount(Decimal('10000'), Decimal('33.33')) == 6667

    def test_un_importe_ilegible_no_revienta(self):
        assert M.discounted_amount('no es plata', 10) is None
        assert M.discounted_amount(None, 10) is None


class TestEtiquetaDeVencimiento:

    def _dentro_de(self, **kw):
        ahora = datetime(2026, 1, 15, 12, 0)
        return M.remaining_label(ahora + timedelta(**kw), now=ahora)

    def test_escribe_ano_con_tilde(self):
        """Sin la tilde, esa palabra significa otra cosa. Aparecia en la
        pantalla de suscripcion de una plataforma de salud."""
        etiqueta = M.remaining_label(datetime(2028, 1, 15),
                                     now=datetime(2026, 1, 15))
        assert 'año' in etiqueta
        assert 'ano' not in etiqueta.replace('año', '')

    def test_no_dice_doce_meses_en_lugar_de_un_ano(self):
        """La version anterior dividia los dias entre 30: a 364 dias de vencer
        decia «12 meses y 4 dias»."""
        etiqueta = self._dentro_de(days=364)
        assert '12 mes' not in etiqueta

    def test_cuenta_meses_de_calendario(self):
        ahora = datetime(2026, 1, 31, 12, 0)
        # El 31 de enero mas un mes es el 28 de febrero.
        assert M.remaining_label(datetime(2026, 2, 28, 12, 0), now=ahora).startswith('1 mes')

    def test_singular_y_plural(self):
        assert self._dentro_de(days=1).startswith('1 día')
        assert self._dentro_de(days=3).startswith('3 días')

    def test_vencido(self):
        ahora = datetime(2026, 1, 15, 12, 0)
        assert M.remaining_label(ahora - timedelta(days=1), now=ahora) == 'Vencido'

    def test_sin_vencimiento(self):
        assert M.remaining_label(None) == 'Sin vencimiento'

    def test_menos_de_un_dia_se_expresa_en_horas(self):
        assert 'hora' in self._dentro_de(hours=5)


class TestCodigosLegibles:

    def test_no_incluye_caracteres_que_se_confunden(self):
        """Se leen por telefono y se copian a mano en un puesto de salud."""
        for _ in range(200):
            cuerpo = M.generate_human_code('POL').split('-')[1]
            assert not set(cuerpo) & set('IO01'), cuerpo

    def test_conserva_el_prefijo(self):
        assert M.generate_human_code('CLINICA').startswith('CLINICA-')

    def test_tiene_longitud_estable(self):
        """La version anterior quitaba caracteres despues de generar, asi que
        el codigo salia mas corto de lo previsto y con menos entropia."""
        for _ in range(50):
            assert len(M.generate_human_code('RH').split('-')[1]) == M.LONGITUD_CODIGO

    def test_no_se_repite_en_una_tanda_grande(self):
        codigos = {M.generate_human_code('RH') for _ in range(2000)}
        assert len(codigos) == 2000

    def test_un_prefijo_vacio_no_produce_un_codigo_invalido(self):
        assert M.generate_human_code('').startswith('RH-')
        assert M.generate_human_code(None).startswith('RH-')


class TestVigenciaFallaCerrado:

    def test_un_registro_sin_estado_no_se_da_por_activo(self):
        """La version anterior usaba getattr(record, 'status', ACCESS_ACTIVE):
        un objeto sin el campo por el que se decide el acceso quedaba abierto."""
        class SinEstado:
            starts_at = None
            expires_at = None

        assert M.is_record_active(SinEstado()) is False

    def test_none_no_esta_activo(self):
        assert M.is_record_active(None) is False

    def test_un_registro_vigente_si_esta_activo(self):
        from models import ACCESS_ACTIVE

        class Vigente:
            status = ACCESS_ACTIVE
            starts_at = None
            expires_at = None

        assert M.is_record_active(Vigente()) is True

    def test_un_registro_vencido_no_esta_activo(self):
        from models import ACCESS_ACTIVE
        ahora = datetime(2026, 1, 15)

        class Vencido:
            status = ACCESS_ACTIVE
            starts_at = None
            expires_at = datetime(2026, 1, 1)

        assert M.is_record_active(Vencido(), now=ahora) is False

    def test_un_registro_que_aun_no_empieza_no_esta_activo(self):
        from models import ACCESS_ACTIVE
        ahora = datetime(2026, 1, 15)

        class Futuro:
            status = ACCESS_ACTIVE
            starts_at = datetime(2026, 2, 1)
            expires_at = None

        assert M.is_record_active(Futuro(), now=ahora) is False


class TestVencimientoDePlanes:

    def test_el_plan_mensual_usa_meses_de_calendario(self):
        inicio = datetime(2026, 1, 31, 10, 0)
        assert M.expiration_for_plan('monthly', inicio) == datetime(2026, 2, 28, 10, 0)

    def test_el_plan_anual_cae_el_mismo_dia_del_ano_siguiente(self):
        inicio = datetime(2026, 3, 15, 10, 0)
        assert M.expiration_for_plan('annual', inicio) == datetime(2027, 3, 15, 10, 0)

    def test_por_consulta_dura_un_dia(self):
        inicio = datetime(2026, 3, 15, 10, 0)
        assert M.expiration_for_plan('per_consultation', inicio) == datetime(2026, 3, 16, 10, 0)

    def test_un_plan_desconocido_no_inventa_vencimiento(self):
        assert M.expiration_for_plan('inventado') is None
