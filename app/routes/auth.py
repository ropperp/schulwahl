"""Anmeldung, Abmeldung und das eigene Konto (Passwort ändern)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..accounts import set_password
from ..db import get_db
from ..models import User
from ..security import (audit, burn_password_time, client_ip, current_user, login_limiter, login_user, normalize_username,
                        password_problem, require_staff, staff_form, user_limiter, verify_csrf, verify_password)
from ..web import flash, render
from .common import go

router = APIRouter(prefix="/admin")


def _has_users(db: Session) -> bool:
    return db.scalar(select(func.count()).select_from(User)) > 0


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
