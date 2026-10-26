"""Benutzerverwaltung: Start-Admin anlegen, Passwörter setzen."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .db import SessionLocal
from .models import User
from .security import ROLE_ADMIN, hash_password

ADMIN_USERNAME = "admin"


def bootstrap_admin() -> str:
    """Legt beim allerersten Start den Benutzer `admin` mit dem Passwort aus ADMIN_PASSWORD an.

    Gibt zurück: "created" | "exists" | "missing" (keine Benutzer und kein ADMIN_PASSWORD gesetzt).
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
