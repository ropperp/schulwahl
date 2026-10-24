"""Anmeldung, eigenes Konto, Benutzerverwaltung und Protokoll."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .accounts import set_password
from .db import get_db
from .models import AuditLog, User
from .security import (ROLE_ADMIN, ROLE_WAHLLEITER, admin_form, audit, burn_password_time, client_ip, csrf_token,
                       current_user, hash_password, login_limiter, login_user, normalize_username, password_problem,
                       random_password, require_admin, require_staff, staff_form, user_limiter, username_problem,
                       verify_csrf, verify_password)
from .web import flash, render

router = APIRouter(prefix="/admin")


def go(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _has_users(db: Session) -> bool:
    return db.scalar(select(func.count()).select_from(User)) > 0


# ------------------------------------------------------------ Login / Logout

@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    if current_user(request, db):
        return go("/admin")
    return render(request, "admin_login.html", error=None, disabled=not _has_users(db))


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, form)
    ip = client_ip(request)
    name = normalize_username(str(form.get("username", "")))[:50]
    password = str(form.get("password", ""))[:1000]
    wait = max(login_limiter.retry_after(ip), user_limiter.retry_after(name))
    if wait:
        return render(request, "admin_login.html", 429, error=f"Zu viele Versuche. Bitte {wait} Sekunden warten.",
                      disabled=False)
    user = db.scalar(select(User).where(User.username == name)) if name else None
    if user is not None and verify_password(password, user.password_hash):
        login_user(request, user)
        request.state.user = user
        audit(db, request, "login")
        db.commit()
        return go("/admin")
    if user is None:
        burn_password_time(password)
    login_limiter.fail(ip)
    user_limiter.fail(name)
    audit(db, request, "login_failed", f"Benutzername: {name}", username="")
    db.commit()
    return render(request, "admin_login.html", 401, error="Benutzername oder Passwort falsch.", disabled=not _has_users(db))


@router.post("/logout")
def logout(request: Request, form=Depends(staff_form), user: User = Depends(require_staff),
           db: Session = Depends(get_db)):
    # Sitzungen liegen im (signierten) Cookie. Damit ein kopiertes Cookie nach dem Abmelden wertlos ist,
    # wird die Sitzungsversion erhöht - das beendet auch Anmeldungen desselben Kontos auf anderen Geräten.
    user.session_version = (user.session_version or 1) + 1
    audit(db, request, "logout")
    db.commit()
    request.session.clear()
    return go("/admin/login")


# ------------------------------------------------------------ Eigenes Konto

@router.get("/account")
def account_page(request: Request, user: User = Depends(require_staff)):
    return render(request, "admin_account.html", error=None)


@router.post("/account")
def account_change(request: Request, form=Depends(staff_form), user: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    ip_wait = user_limiter.retry_after(user.username)
    if ip_wait:
        return render(request, "admin_account.html", 429, error=f"Zu viele Versuche. Bitte {ip_wait} Sekunden warten.")
    current, new, again = (str(form.get(k, "")) for k in ("current_password", "new_password", "new_password2"))
    error = None
    if not verify_password(current, user.password_hash):
        user_limiter.fail(user.username)
        error = "Das aktuelle Passwort stimmt nicht."
    elif new != again:
        error = "Die neuen Passwörter stimmen nicht überein."
    elif new == current:
        error = "Das neue Passwort muss sich vom bisherigen unterscheiden."
    else:
        error = password_problem(new, user.username)
    if error:
        return render(request, "admin_account.html", 400, error=error)
    set_password(db, user, new, must_change=False)
    audit(db, request, "password_changed")
    db.commit()
    request.session["sv"] = user.session_version  # andere Sitzungen dieses Kontos sind damit abgemeldet
    flash(request, "Passwort geändert. Alle anderen Anmeldungen mit diesem Konto wurden beendet.")
    return go("/admin")


# ------------------------------------------------------------ Benutzerverwaltung (nur Admin)

def _users_page(request: Request, db: Session, status: int = 200, **ctx):
    users = db.scalars(select(User).order_by(User.role, User.username)).all()
    return render(request, "admin_users.html", status, users=users, error=ctx.pop("error", None),
                  new_credentials=ctx.pop("new_credentials", None), **ctx)


@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    return _users_page(request, db)


@router.post("/users")
def user_create(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
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
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if target.role != ROLE_WAHLLEITER:
        raise HTTPException(403, "Administrator-Konten können hier nicht verwaltet werden. "
                                 "Das eigene Passwort ändert man unter „Mein Konto“.")
    return target


@router.post("/users/{user_id}/reset")
def user_reset(request: Request, user_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    target = _target(db, user_id)
    password = random_password()
    set_password(db, target, password, must_change=True)
    audit(db, request, "password_reset", target.username)
    db.commit()
    return _users_page(request, db, new_credentials={"username": target.username, "password": password,
                                                     "what": "zurückgesetzt"})


@router.post("/users/{user_id}/delete")
def user_delete(request: Request, user_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    target = _target(db, user_id)
    db.delete(target)
    audit(db, request, "user_deleted", target.username)
    db.commit()
    flash(request, f"Benutzer „{target.username}“ gelöscht.")
    return go("/admin/users")


# ------------------------------------------------------------ Protokoll (nur Admin)

@router.get("/audit")
def audit_page(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    entries = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(300)).all()
    return render(request, "admin_audit.html", entries=entries)
