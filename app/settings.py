"""Einstellungen aus der Datenbank (Schulname, Logo) mit kleinem Prozess-Cache."""
from sqlalchemy.orm import Session

from . import config
from .db import SessionLocal
from .models import Image, Setting

_cache: dict | None = None


def get() -> dict:
    global _cache
    if _cache is None:
        with SessionLocal() as db:
            rows = {s.key: s.value for s in db.query(Setting).all()}
        logo = rows.get("logo_image_id", "")
        _cache = {"school_name": rows.get("school_name") or config.SCHOOL_NAME,
                  "logo_id": int(logo) if logo.isdigit() else None}
    return _cache


def invalidate() -> None:
    global _cache
    _cache = None


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def save(db: Session, school_name: str, logo: tuple[bytes, str] | None = None, remove_logo: bool = False) -> None:
    current = get()["logo_id"]
    _set(db, "school_name", school_name)
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
