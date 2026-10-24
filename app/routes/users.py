"""Benutzerverwaltung und Protokoll (nur Administratoren)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..accounts import set_password
from ..db import get_db
from ..models import HIDDEN_USERNAME, AuditLog, User
from ..security import (ROLE_ADMIN, ROLE_LABEL, ROLE_WAHLLEITER, admin_form, audit, hash_password, normalize_username,
                        random_password, require_admin, username_problem)
from ..web import flash, render
from .common import go

router = APIRouter(prefix="/admin")


def _users_page(request: Request, db: Session, status: int = 200, **ctx):
    """Rendert die Benutzerliste (optional mit einmalig angezeigten Zugangsdaten)."""
    # Der interne Entwickler-Notfallzugang (siehe developer_access.py) erscheint nie in der Liste
    users = db.scalars(select(User).where(User.username != HIDDEN_USERNAME).order_by(User.role, User.username)).all()
    return render(request, "admin_users.html", status, users=users, error=ctx.pop("error", None),
                  new_credentials=ctx.pop("new_credentials", None), **ctx)


@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    """Benutzerliste (nur Administrator)."""
    return _users_page(request, db)


@router.post("/users")
def user_create(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Legt eine/n Wahlleiter/in oder weitere/n Administrator/in mit Zufalls-Einmalpasswort an; es wird nur jetzt angezeigt."""
    name = normalize_username(str(form.get("username", "")))
    role = ROLE_ADMIN if form.get("role") == ROLE_ADMIN else ROLE_WAHLLEITER
    error = username_problem(name)
    if error is None and db.scalar(select(User.id).where(User.username == name)) is not None:
        error = "Diesen Benutzernamen gibt es schon."
    if error:
        return _users_page(request, db, 400, error=error)
    password = random_password()
    db.add(User(username=name, role=role, password_hash=hash_password(password), must_change=True))
    audit(db, request, "user_created", f"{name} ({ROLE_LABEL[role]})")
    db.commit()
    return _users_page(request, db, new_credentials={"username": name, "password": password, "what": "angelegt"})


def _target(db: Session, admin: User, user_id: int) -> User:
    """Lädt den Benutzer für Zurücksetzen/Löschen. Nicht erlaubt: sich selbst (dafür gibt es „Mein Konto“) und der interne Zugang."""
    target = db.get(User, user_id)
    if target is None or target.username == HIDDEN_USERNAME:  # den internen Zugang gibt es hier nicht
        raise HTTPException(404, "Benutzer nicht gefunden")
    if target.id == admin.id:
        raise HTTPException(403, "Das eigene Konto verwaltest du unter „Mein Konto“ (Passwort ändern).")
    return target


@router.post("/users/{user_id}/reset")
def user_reset(request: Request, user_id: int, form=Depends(admin_form), admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    """Setzt das Passwort eines anderen Benutzers zurück (Zufalls-Einmalpasswort, alle Sitzungen beendet)."""
    target = _target(db, admin, user_id)
    password = random_password()
    set_password(db, target, password, must_change=True)
    audit(db, request, "password_reset", target.username)
    db.commit()
    return _users_page(request, db, new_credentials={"username": target.username, "password": password,
                                                     "what": "zurückgesetzt"})


@router.post("/users/{user_id}/delete")
def user_delete(request: Request, user_id: int, form=Depends(admin_form), admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """Löscht einen anderen Benutzer; der letzte sichtbare Administrator bleibt immer erhalten."""
    target = _target(db, admin, user_id)
    if target.role == ROLE_ADMIN and db.scalar(
            select(func.count()).select_from(User).where(User.role == ROLE_ADMIN, User.username != HIDDEN_USERNAME)) <= 1:
        raise HTTPException(403, "Der letzte Administrator kann nicht gelöscht werden.")
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
