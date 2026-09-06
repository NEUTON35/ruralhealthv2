from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


try:
    COLOMBIA_TZ = ZoneInfo("America/Bogota")
except ZoneInfoNotFoundError:
    COLOMBIA_TZ = timezone(timedelta(hours=-5), name="America/Bogota")


def colombia_now():
    return datetime.now(COLOMBIA_TZ).replace(tzinfo=None)


def as_colombia(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=COLOMBIA_TZ)
    return dt.astimezone(COLOMBIA_TZ)


def colombia_iso(dt):
    local_dt = as_colombia(dt)
    return "" if local_dt is None else local_dt.isoformat()


def colombia_strftime(dt, fmt):
    local_dt = as_colombia(dt)
    return "" if local_dt is None else local_dt.strftime(fmt)
