"""Zeit: In der DB liegt alles als UTC (ohne tzinfo). Angezeigt wird in der Schulzeitzone."""
from datetime import datetime, timezone

from . import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(config.TZ)


def fmt_local(dt: datetime | None) -> str:
    return to_local(dt).strftime("%d.%m.%Y %H:%M") if dt else "–"


def input_local(dt: datetime | None) -> str:
    """Wert für <input type=datetime-local>."""
    return to_local(dt).strftime("%Y-%m-%dT%H:%M") if dt else ""


def parse_local(value: str) -> datetime:
    """datetime-local-Eingabe (Ortszeit) -> naive UTC."""
    local = datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M").replace(tzinfo=config.TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)
