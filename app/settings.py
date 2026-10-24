"""Einstellungen aus der Datenbank (Schulname, Logo, QR-Adresse) mit kurzem Prozess-Cache.

Der Cache läuft nach wenigen Sekunden ab, damit mehrere Worker-Prozesse Änderungen zeitnah übernehmen.
"""
import time
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from . import config
from .db import SessionLocal
from .models import Image, Setting

CACHE_SECONDS = 5

_cache: dict | None = None
_loaded_at = 0.0


def get() -> dict:
    """Aktuelle Einstellungen (Schulname, Logo, QR-Adresse); für 5 Sekunden zwischengespeichert."""
    global _cache, _loaded_at
    if _cache is None or time.monotonic() - _loaded_at > CACHE_SECONDS:
        with SessionLocal() as db:
            rows = {s.key: s.value for s in db.query(Setting).all()}
        logo = rows.get("logo_image_id", "")
        _cache = {"school_name": rows.get("school_name") or config.SCHOOL_NAME,
                  "logo_id": int(logo) if logo.isdigit() else None,
                  "public_url": rows.get("public_url") or config.PUBLIC_URL}
        _loaded_at = time.monotonic()
    return _cache


def invalidate() -> None:
    """Zwischenspeicher verwerfen (nach dem Speichern)."""
    global _cache
    _cache = None


def public_url(request=None) -> str:
    """Adresse, die in die QR-Codes geschrieben wird (Einstellung, sonst .env, sonst die aktuelle Adresse)."""
    url = get()["public_url"]
    if not url and request is not None:
        url = str(request.base_url)
    return url.rstrip("/")


def clean_public_url(raw: str) -> tuple[str | None, str | None]:
    """Prüft die QR-Adresse. Gibt (bereinigte Adresse, Fehlermeldung) zurück."""
    raw = raw.strip().rstrip("/")
    if not raw:
        return None, "Bitte eine Adresse angeben, z. B. https://wahl.meineschule.at"
    if len(raw) > 200 or any(c.isspace() or ord(c) < 32 for c in raw):
        return None, "Die Adresse ist ungültig."
    try:
        parts = urlsplit(raw)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None, "Die Adresse ist ungültig."
    if parts.scheme not in ("http", "https") or not host:
        return None, "Die Adresse muss mit https:// (oder http://) beginnen, z. B. https://wahl.meineschule.at"
    if parts.username or parts.password or parts.path or parts.query or parts.fragment:
        return None, "Bitte nur die Hauptadresse ohne Pfad, Parameter oder Zugangsdaten angeben."
    if any(not (c.isalnum() or c in ".-") for c in host):
        return None, "Der Rechnername enthält ungültige Zeichen."
    return f"{parts.scheme}://{host}{':' + str(port) if port else ''}", None


def _set(db: Session, key: str, value: str) -> None:
    """Setzt einen Einstellungswert (legt die Zeile bei Bedarf an), ohne zu committen."""
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def save(db: Session, school_name: str, logo: tuple[bytes, str] | None = None, remove_logo: bool = False,
         public_url: str | None = None) -> None:
    """Speichert Schulname, optional Logo und QR-Adresse und committet (auch andere offene Änderungen derselben Sitzung)."""
    current = get()["logo_id"]
    _set(db, "school_name", school_name)
    if public_url is not None:
        _set(db, "public_url", public_url)
    if logo or remove_logo:
        if current:
            db.query(Image).filter(Image.id == current).delete()
        if logo:
            img = Image(data=logo[0], content_type=logo[1])
            db.add(img)
            db.flush()
            _set(db, "logo_image_id", str(img.id))
        else:
            _set(db, "logo_image_id", "")
    db.commit()
    invalidate()
