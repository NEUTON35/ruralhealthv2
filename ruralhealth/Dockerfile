# =============================================================================
# RuralHealth Connect — imagen de producción
# =============================================================================
# Construcción en dos etapas: las dependencias se compilan en la primera y solo
# los artefactos resultantes pasan a la imagen final, que así no lleva
# compiladores ni cabeceras de desarrollo.
# =============================================================================

FROM python:3.12-slim AS builder

# `libmagic` es necesario en tiempo de compilación para python-magic, que
# comprueba el contenido real de los archivos subidos y no solo su extensión.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# -----------------------------------------------------------------------------
FROM python:3.12-slim

# En tiempo de ejecución solo hacen falta las bibliotecas compartidas.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libmagic1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios: si la aplicación se ve comprometida, no debe poder
# escribir en el sistema de archivos del contenedor.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 ruralhealth

COPY --from=builder /install /usr/local

WORKDIR /app
COPY --chown=ruralhealth:ruralhealth . .

# Directorios de datos. `uploads` contiene documentos clínicos y comprobantes:
# debe montarse como volumen persistente y respaldarse.
RUN mkdir -p /app/uploads /app/logs \
    && chown -R ruralhealth:ruralhealth /app/uploads /app/logs \
    && chmod 700 /app/uploads

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_ENV=production \
    PORT=5000

USER ruralhealth

EXPOSE 5000

# La sonda usa /health, que no toca la base de datos. La disponibilidad real
# (/ready, que sí la comprueba) la evalúa el orquestador, para no reiniciar el
# contenedor por una caída pasajera de la base.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/health || exit 1

# El esquema se aplica con `flask db upgrade` como paso previo del despliegue,
# no en el arranque del contenedor: con varias réplicas, todas ejecutarían la
# migración a la vez.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
