"""Configuración de Gunicorn para RuralHealth Connect.

    gunicorn -c gunicorn.conf.py wsgi:app
"""

import multiprocessing
import os

# --- Red --------------------------------------------------------------------
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# --- Workers ----------------------------------------------------------------
# Regla habitual: 2 × núcleos + 1. Se acota a 8 porque estas instalaciones
# corren en servidores modestos de puesto de salud, y cada worker mantiene su
# propio pool de conexiones a la base de datos.
workers = int(os.environ.get(
    'GUNICORN_WORKERS',
    min(multiprocessing.cpu_count() * 2 + 1, 8),
))

# Hilos por worker: las peticiones son mayoritariamente de E/S (base de datos),
# así que unos pocos hilos aprovechan mejor cada proceso.
threads = int(os.environ.get('GUNICORN_THREADS', 4))
worker_class = 'gthread'

# --- Tiempos ----------------------------------------------------------------
# Generoso, porque la exportación de RIPS y de historia clínica puede tardar en
# instalaciones con muchos registros.
timeout = int(os.environ.get('GUNICORN_TIMEOUT', 120))
graceful_timeout = 30
# Mayor que el del proxy que haya delante, para evitar cerrar una conexión que
# el proxy todavía considera viva.
keepalive = 65

# --- Reciclado de workers ---------------------------------------------------
# Reiniciar periódicamente acota el efecto de cualquier fuga de memoria en
# procesos de larga duración. El jitter evita que todos reinicien a la vez.
max_requests = 1000
max_requests_jitter = 100

# --- Límites de la petición -------------------------------------------------
# Contención de cabeceras desmedidas antes de que lleguen a la aplicación.
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# --- Registro ---------------------------------------------------------------
accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '-')
errorlog = os.environ.get('GUNICORN_ERROR_LOG', '-')
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')

# Formato de acceso SIN cadena de consulta.
#
# La ruta con `?` puede llevar términos de búsqueda escritos por el personal
# clínico (nombres, documentos) y el registro de acceso suele enviarse a
# sistemas de agregación con controles más laxos que la base de datos.
access_log_format = '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(D)sus "%(a)s"'

# --- Proceso ----------------------------------------------------------------
proc_name = 'ruralhealth'
preload_app = False   # cada worker construye su app: evita compartir conexiones


def on_starting(server):
    server.log.info('RuralHealth Connect, iniciando (%s workers)', workers)


def post_fork(server, worker):
    # Cada worker necesita su propio pool: heredarlo del padre produce errores
    # intermitentes por conexiones compartidas entre procesos.
    from models import db
    try:
        db.engine.dispose()
    except Exception:
        pass


def worker_int(worker):
    worker.log.info('Worker %s interrumpido', worker.pid)


def on_exit(server):
    server.log.info('RuralHealth Connect, detenido')
