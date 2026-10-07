"""Read the server clock without relying on dates retained in model context."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def current_datetime(timezone_name=None):
    try:
        zone = ZoneInfo(timezone_name) if timezone_name is not None else None
    except (ZoneInfoNotFoundError, ValueError):
        return {
            "error": "Unknown timezone. Use an IANA name such as America/Sao_Paulo or UTC.",
            "type": "validation_error",
        }
    now = datetime.now(UTC)
    local = now.astimezone(zone)
    return {
        "datetime": local.isoformat(timespec="seconds"),
        "date": local.date().isoformat(),
        "time": local.strftime("%H:%M:%S"),
        "weekday": (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )[local.weekday()],
        "timezone": timezone_name or local.tzname(),
        "timezone_source": "requested" if timezone_name else "server_local",
        "utc_offset": local.isoformat(timespec="seconds")[-6:],
        "utc_datetime": now.isoformat(timespec="seconds"),
        "unix_timestamp": int(now.timestamp()),
    }
