"""Benutzerverwaltung und Protokoll (nur Administratoren)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounts import set_password
from ..db import get_db
from ..models import AuditLog, User
from ..security import (ROLE_WAHLLEITER, admin_form, audit, hash_password, normalize_username, random_password, require_admin,
                        username_problem)
from ..web import flash, render
from .common import go

router = APIRouter(prefix="/admin")


def _users_page(request: Request, db: Session, status: int = 200, **ctx):
    """Rendert die Benutzerliste (optional mit einmalig angezeigten Zugangsdaten)."""
    users = db.scalars(select(User).order_by(User.role, User.username)).all()
    return render(request, "admin_users.html", status, users=users, error=ctx.pop("error", None),
                  new_credentials=ctx.pop("new_credentials", None), **ctx)


@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    """Benutzerliste (nur Administrator)."""
    return _users_page(request, db)


@router.post("/users")
def user_create(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Legt eine/n Wahlleiter/in mit Zufalls-Einmalpasswort an; es wird nur jetzt angezeigt."""
    name = normalize_username(str(form.get("username", "")))
    error = username_problem(name)
    if error is None and db.scalar(select(User.id).where(User.username == name)) is not None:
        error = "Diesen Benutzernamen gibt es schon."
    if error:
        return _users_page(request, db, 400, error=error)
    password = random_password()
    db.add(User(username=name, role=ROLE_WAHLLEITER, password_hash=hash_password(password), must_change=True))
    audit(db, request, "user_created", name)
    db.commit()
    return _users_page(request, db, new_credentials={"username": name, "password": password, "what": "angelegt"})


def _target(db: Session, user_id: int) -> User:
    """Lädt den Benutzer für Zurücksetzen/Löschen; Administratoren sind hier geschützt."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if target.role != ROLE_WAHLLEITER:
        raise HTTPException(403, "Administrator-Konten können hier nicht verwaltet werden. "
                                 "Das eigene Passwort ändert man unter „Mein Konto“.")
    return target


@router.post("/users/{user_id}/reset")
def user_reset(request: Request, user_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Setzt das Passwort einer/s Wahlleiter/in zurück (Zufalls-Einmalpasswort, alle Sitzungen beendet)."""
    target = _target(db, user_id)
    password = random_password()
    set_password(db, target, password, must_change=True)
    audit(db, request, "password_reset", target.username)
    db.commit()
    return _users_page(request, db, new_credentials={"username": target.username, "password": password,
                                                     "what": "zurückgesetzt"})


@router.post("/users/{user_id}/delete")
def user_delete(request: Request, user_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Löscht eine/n Wahlleiter/in."""
    target = _target(db, user_id)
    db.delete(target)
    audit(db, request, "user_deleted", target.username)
    db.commit()
    flash(request, f"Benutzer „{target.username}“ gelöscht.")
    return go("/admin/users")


@router.get("/audit")
def audit_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    """Protokoll der letzten 300 Verwaltungsaktionen (nur Administrator)."""
    entries = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(300)).all()
    return render(request, "admin_audit.html", entries=entries)
