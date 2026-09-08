"""Punto de entrada WSGI para producción.

    gunicorn -c gunicorn.conf.py wsgi:app

El servidor de desarrollo de Flask no debe usarse en producción: es de un solo
hilo, no controla el tamaño de las peticiones y no está preparado para tráfico
real. Con historia clínica de por medio, esa diferencia no es teórica.

El esquema NO se crea aquí. En producción lo gobierna Alembic:

    flask db upgrade

Crear tablas al arrancar dejaría la base sin versión conocida y haría imposible
revertir un cambio.
"""

import sys

from config import ConfigurationError

try:
    from app import app
except ConfigurationError as error:
    # Fallar aquí, y no más adelante, es deliberado: un worker que arranca con
    # una llave por defecto cifraría los datos con una clave pública.
    print('\n' + '=' * 72, file=sys.stderr)
    print(' RURALHEALTH CONNECT, ARRANQUE DETENIDO', file=sys.stderr)
    print('=' * 72, file=sys.stderr)
    print(f'\n{error}\n', file=sys.stderr)
    raise

application = app

if __name__ == '__main__':
    raise SystemExit(
        'Este modulo no debe ejecutarse directamente.\n'
        'Usa: gunicorn -c gunicorn.conf.py wsgi:app'
    )
