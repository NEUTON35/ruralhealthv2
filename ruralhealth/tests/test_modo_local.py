"""`--local` tiene que poder arrancar, y la protección de producción tiene que
seguir en pie.

Las dos mitades importan y tiran en direcciones opuestas.

La protección: SQLite no soporta el bloqueo de filas (`SELECT ... FOR UPDATE`)
que impide que dos expendedores entreguen el mismo inventario a la vez. Por eso
el arranque se detiene si alguien configura SQLite en producción. Eso no se
toca.

El fallo: `--local` usaba `os.environ.setdefault('FLASK_ENV', 'development')`,
y `setdefault` no hace nada si la variable ya existe. `load_dotenv()` corre al
importar el módulo, así que el `.env` ya había puesto `FLASK_ENV=production` y
el valor por defecto nunca se aplicaba. Resultado: pedir explícitamente una
base local hacía saltar la protección de producción, y quien solo quería probar
en su portátil se quedaba fuera.

Pedir `--local` es declarar que eso no es producción. La bandera manda sobre el
archivo de configuración.
"""

import io
import os

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fuente_app():
    with io.open(os.path.join(RAIZ, 'app.py'), encoding='utf-8') as f:
        return f.read()


class TestLaBanderaLocalManda:

    def test_local_asigna_el_entorno_no_lo_sugiere(self):
        """`setdefault` es inútil aquí: el `.env` ya lo definió."""
        fuente = _fuente_app()
        bloque = fuente[fuente.index("'--local' in sys.argv"):]
        bloque = bloque[:bloque.index('# Instancia de modulo')]

        assert "os.environ['FLASK_ENV'] = 'development'" in bloque, (
            'la bandera --local no impone el entorno de desarrollo')
        assert "setdefault('FLASK_ENV'" not in bloque, (
            '`setdefault` no hace nada si `load_dotenv()` ya definió '
            'FLASK_ENV; hay que asignar')

    def test_local_apunta_a_una_base_sqlite_propia(self):
        fuente = _fuente_app()
        bloque = fuente[fuente.index("'--local' in sys.argv"):]
        bloque = bloque[:bloque.index('# Instancia de modulo')]

        assert "os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///'" in bloque
        assert 'instance' in bloque

    def test_local_avisa_de_para_que_no_sirve(self):
        """Quien arranca en local tiene que saber por qué no vale para operar."""
        fuente = _fuente_app()
        bloque = fuente[fuente.index("'--local' in sys.argv"):]
        bloque = bloque[:bloque.index('# Instancia de modulo')]

        assert 'dispensacion' in bloque.lower(), (
            'el modo local no explica por qué SQLite no sirve con pacientes')


class TestLaProteccionSigueEnPie:
    """Quitar el bloqueo a SQLite en producción sería peor que el fallo."""

    def test_sqlite_en_produccion_detiene_el_arranque(self, monkeypatch):
        from config import ConfigurationError, load_config

        monkeypatch.setenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///cualquiera.db')
        for clave in ('RURALHEALTH_SECRET_KEY', 'RURALHEALTH_JWT_SECRET_KEY',
                      'RURALHEALTH_HASH_PEPPER', 'RURALHEALTH_FIELD_KEY_SEED'):
            monkeypatch.setenv(clave, 'k' * 64)

        with pytest.raises(ConfigurationError) as fallo:
            load_config('production')
        assert 'sqlite' in str(fallo.value).lower(), (
            'produccion acepto SQLite: se perdio el bloqueo de filas que '
            'impide la doble dispensacion')

    def test_sqlite_en_desarrollo_se_permite(self, monkeypatch):
        from config import load_config

        monkeypatch.setenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///cualquiera.db')
        config = load_config('development')
        assert 'sqlite' in config.SQLALCHEMY_DATABASE_URI
