# -*- coding: utf-8 -*-
"""Tiempo en hora de Colombia.

Por qué las fechas se guardan sin zona horaria
----------------------------------------------
`colombia_now()` devuelve un `datetime` **naive**: calcula la hora en
`America/Bogota` y luego quita la información de zona. Es deliberado y es la
convención de toda la aplicación.

El motivo es que las columnas `DateTime` de SQLAlchemy, sobre SQLite y sobre
PostgreSQL sin `timezone=True`, guardan valores naive. Si una parte del código
produjera fechas con zona y otra sin ella, cualquier comparación entre las dos
lanzaría `TypeError` en tiempo de ejecución, y lo haría de forma intermitente:
solo en el camino donde se cruzan. Un solo criterio, aplicado en todas partes,
evita esa clase de fallo por completo.

La contrapartida es que **nada en la base sabe en qué zona está**. Por eso todo
lo que entra pasa por aquí, y no por `datetime.now()`, que daría la hora del
servidor. Un servidor en Virginia con `datetime.now()` fecharía las atenciones
con una hora de diferencia, y esa hora acaba impresa en una orden médica.

Colombia no aplica horario de verano desde 1993, así que el desfase con UTC es
constante (-05:00). Eso hace que el respaldo por desplazamiento fijo, cuando la
base de datos de zonas horarias del sistema no está disponible, sea equivalente
y no una aproximación.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Windows y algunas imágenes de contenedor minimalistas no traen la base de
# datos de zonas horarias de la IANA. El respaldo es exacto para Colombia por lo
# dicho arriba: sin horario de verano, el desplazamiento no cambia nunca.
try:
    COLOMBIA_TZ = ZoneInfo("America/Bogota")
    TZ_DESDE_SISTEMA = True
except ZoneInfoNotFoundError:  # pragma: no cover - depende del sistema
    COLOMBIA_TZ = timezone(timedelta(hours=-5), name="America/Bogota")
    TZ_DESDE_SISTEMA = False

FORMATO_FECHA = "%Y-%m-%d"
FORMATO_FECHA_HORA = "%Y-%m-%d %H:%M"


def colombia_now():
    """Ahora, en hora de Colombia, sin zona. Ver el encabezado del módulo."""
    return datetime.now(COLOMBIA_TZ).replace(tzinfo=None)


def colombia_today():
    """La fecha de hoy en Colombia.

    No es lo mismo que `date.today()`, que usa la hora del servidor: entre las
    19:00 y la medianoche en Bogotá, un servidor en UTC ya está en el día
    siguiente. Una agenda que se corra un día no es un detalle.
    """
    return colombia_now().date()


def as_colombia(dt):
    """Lleva un `datetime` a hora de Colombia.

    Uno naive se interpreta como que ya está en hora de Colombia, que es la
    convención de la base de datos. Uno con zona se convierte.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=COLOMBIA_TZ)
    return dt.astimezone(COLOMBIA_TZ)


def to_naive(dt):
    """Deja un `datetime` en la forma que acepta la base de datos.

    Sirve para lo que llega de fuera: una fecha con zona horaria proveniente de
    una API o de una cabecera HTTP no puede compararse con las de la base sin
    pasar por aquí.
    """
    convertido = as_colombia(dt)
    return None if convertido is None else convertido.replace(tzinfo=None)


def colombia_iso(dt):
    local = as_colombia(dt)
    return "" if local is None else local.isoformat()


def colombia_strftime(dt, fmt):
    local = as_colombia(dt)
    return "" if local is None else local.strftime(fmt)


def parse_date(texto, default=None):
    """Lee una fecha 'AAAA-MM-DD' de un formulario.

    Devuelve `default` si el texto está vacío o mal formado, en lugar de lanzar.
    Una fecha inválida escrita por un usuario es una entrada esperable, no un
    fallo del programa: quien llama decide qué hacer con ella.
    """
    texto = (texto or "").strip()
    if not texto:
        return default
    try:
        return datetime.strptime(texto[:10], FORMATO_FECHA).date()
    except ValueError:
        return default


def parse_datetime(texto, default=None):
    """Lee 'AAAA-MM-DD HH:MM' o 'AAAA-MM-DDTHH:MM'. Devuelve naive."""
    texto = (texto or "").strip().replace("T", " ")
    if not texto:
        return default
    for formato in (FORMATO_FECHA_HORA, "%Y-%m-%d %H:%M:%S", FORMATO_FECHA):
        try:
            return datetime.strptime(texto[:len(formato) + 4], formato)
        except ValueError:
            continue
    return default


def day_bounds(dia=None):
    """Primer y último instante de un día, para filtrar por rango.

    Existe porque el error de comparar una columna `DateTime` contra un `date`
    es fácil de cometer y silencioso: `created_at <= date(2026, 9, 7)` excluye
    todo lo ocurrido ese día después de la medianoche, y el informe sale
    incompleto sin avisar.
    """
    dia = dia or colombia_today()
    if isinstance(dia, datetime):
        dia = dia.date()
    inicio = datetime.combine(dia, datetime.min.time())
    return inicio, inicio + timedelta(days=1) - timedelta(microseconds=1)


def epidemiological_week(momento=None):
    """Semana epidemiológica y año, según la definición del INS.

    La vigilancia en salud pública se organiza por semanas epidemiológicas, no
    por semanas del calendario. La semana 1 es la primera que tiene al menos
    cuatro días en el año nuevo, y va de domingo a sábado.

    `surveillance.py` la necesita para saber cuándo cierra el plazo de una
    notificación semanal, y los informes al INS se rotulan con ella.

    Devuelve `(año, semana)`.
    """
    momento = momento or colombia_now()
    if isinstance(momento, datetime):
        momento = momento.date()

    def primer_domingo(anio):
        # Semana 1: la primera con cuatro o más días del año. Equivale al
        # domingo más cercano al 1 de enero.
        enero = date(anio, 1, 1)
        # weekday(): lunes=0 ... domingo=6. Se busca el domingo anterior o igual.
        desplazamiento = (enero.weekday() + 1) % 7
        domingo_previo = enero - timedelta(days=desplazamiento)
        # Si esa semana deja menos de cuatro días en el año nuevo, la semana 1
        # empieza el domingo siguiente.
        if (domingo_previo + timedelta(days=6) - enero).days < 3:
            return domingo_previo + timedelta(days=7)
        return domingo_previo

    anio = momento.year
    inicio = primer_domingo(anio)
    if momento < inicio:
        anio -= 1
        inicio = primer_domingo(anio)
    else:
        siguiente = primer_domingo(anio + 1)
        if momento >= siguiente:
            anio += 1
            inicio = siguiente

    semana = ((momento - inicio).days // 7) + 1
    return anio, semana


def end_of_epidemiological_week(momento=None):
    """Último instante de la semana epidemiológica: el sábado a medianoche.

    Es el plazo real de una notificación semanal al Sivigila. Sumar siete días
    desde el momento de la detección da una fecha que no coincide con el cierre
    que usa el INS.
    """
    momento = momento or colombia_now()
    dia = momento.date() if isinstance(momento, datetime) else momento
    # Domingo = inicio de semana. weekday(): lunes=0 ... domingo=6.
    dias_desde_domingo = (dia.weekday() + 1) % 7
    sabado = dia + timedelta(days=6 - dias_desde_domingo)
    return day_bounds(sabado)[1]


def age_on(birth_date, reference=None):
    """Edad en años cumplidos.

    Existe aquí y no calculada a ojo porque el error clásico -dividir los días
    entre 365- desplaza la edad en los años bisiestos, y la dosificación
    pediátrica depende de ella.
    """
    if not birth_date:
        return None
    if isinstance(birth_date, datetime):
        birth_date = birth_date.date()
    reference = reference or colombia_today()
    if isinstance(reference, datetime):
        reference = reference.date()
    if birth_date > reference:
        return None
    años = reference.year - birth_date.year
    if (reference.month, reference.day) < (birth_date.month, birth_date.day):
        años -= 1
    return años
