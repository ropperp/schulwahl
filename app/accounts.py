"""Benutzerverwaltung und Ersteinrichtung.

Ablauf bei einer neuen Installation: Es gibt noch keinen Benutzer. Beim Start erzeugt `ensure_setup_code` einen
Einrichtungscode (steht im Log, `setup.sh` zeigt ihn an). Wer den Code kennt, legt auf der Seite /admin/setup das
Admin-Passwort, den Schulnamen und die QR-Adresse fest (`complete_setup`). Danach gibt es den Code nicht mehr.
Optional (Tests, automatische Installation): ist ADMIN_PASSWORD gesetzt, wird der Admin stattdessen sofort damit angelegt.
"""
import re
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .db import SessionLocal
from .models import Setting, User
from .security import ROLE_ADMIN, hash_password

ADMIN_USERNAME = "admin"
SETUP_CODE_KEY = "setup_code"
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne leicht verwechselbare Zeichen (0/O, 1/I)


def bootstrap_admin() -> str:
    """Legt beim ersten Start den Benutzer `admin` an, falls ADMIN_PASSWORD gesetzt ist (optional, siehe Modul-Text).

    Gibt zurück: "created" | "exists" | "missing" (keine Benutzer und kein ADMIN_PASSWORD: Ersteinrichtung nötig).
    """
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is not None:
            return "exists"
        if not config.ADMIN_PASSWORD:
            return "missing"
        db.add(User(username=ADMIN_USERNAME, role=ROLE_ADMIN, password_hash=hash_password(config.ADMIN_PASSWORD)))
        try:
            db.commit()
        except IntegrityError:  # ein anderer Worker war schneller
            db.rollback()
            return "exists"
        return "created"


def set_password(db: Session, user: User, password: str, must_change: bool) -> None:
    """Setzt ein neues Passwort und macht alle bestehenden Sitzungen dieses Benutzers ungültig."""
    user.password_hash = hash_password(password)
    user.must_change = must_change
    user.session_version = (user.session_version or 1) + 1


def has_users(db: Session) -> bool:
    """True, sobald es mindestens einen Benutzer gibt, also die Ersteinrichtung abgeschlossen ist."""
    return db.scalar(select(User.id).limit(1)) is not None


def normalize_setup_code(raw: str) -> str:
    """Eingabe vergleichbar machen (Groß-/Kleinschreibung, Bindestrich und Leerzeichen egal)."""
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def format_setup_code(code: str) -> str:
    """Anzeige als XXXX-XXXX."""
    return f"{code[:4]}-{code[4:]}"


def ensure_setup_code() -> str | None:
    """Gibt den Einrichtungscode zurück (und legt ihn an, falls es noch keinen gibt). None, wenn die Einrichtung fertig ist.

    Der Code liegt in der Datenbank, damit alle Worker-Prozesse denselben kennen.
    """
    with SessionLocal() as db:
        if has_users(db):
            return None
        row = db.get(Setting, SETUP_CODE_KEY)
        if row is None:
            db.add(Setting(key=SETUP_CODE_KEY, value="".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))))
            try:
                db.commit()
            except IntegrityError:  # ein anderer Worker war schneller
                db.rollback()
            row = db.get(Setting, SETUP_CODE_KEY)
        return row.value if row else None


def setup_code_matches(db: Session, entered: str) -> bool:
    """Prüft den eingegebenen Einrichtungscode (zeitkonstant)."""
    row = db.get(Setting, SETUP_CODE_KEY)
    return bool(row) and secrets.compare_digest(normalize_setup_code(entered).encode(), row.value.encode())


def complete_setup(db: Session, password: str) -> bool:
    """Legt den Administrator an und verwirft den Einrichtungscode. False, wenn die Einrichtung schon abgeschlossen war.

    Schulname und Adresse speichert der Aufrufer in derselben Transaktion (settings.save), das commit macht dieser Aufrufer.
    """
    if has_users(db):
        return False
    db.add(User(username=ADMIN_USERNAME, role=ROLE_ADMIN, password_hash=hash_password(password)))
    row = db.get(Setting, SETUP_CODE_KEY)
    if row is not None:
        db.delete(row)
    return True
