"""Anmeldung, Abmeldung und das eigene Konto (Passwort ändern)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import settings as app_settings
from ..accounts import complete_setup, has_users, set_password, setup_code_matches
from ..db import get_db
from ..models import User
from ..security import (audit, burn_password_time, client_ip, current_user, login_limiter, login_user, normalize_username,
                        password_problem, require_staff, staff_form, user_limiter, verify_csrf, verify_password)
from ..web import flash, render
from .common import go

router = APIRouter(prefix="/admin")


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    """Anmeldeseite. Gibt es noch keinen Benutzer (frische Installation), geht es zur Ersteinrichtung."""
    if not has_users(db):
        return go("/admin/setup")
    if current_user(request, db):
        return go("/admin")
    return render(request, "admin_login.html", error=None)


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    """Prüft Benutzername und Passwort (mit Sperre nach zu vielen Fehlversuchen) und startet die Sitzung."""
    form = await request.form()
    verify_csrf(request, form)
    if not has_users(db):
        return go("/admin/setup")
    ip = client_ip(request)
    name = normalize_username(str(form.get("username", "")))[:50]
    password = str(form.get("password", ""))[:1000]
    wait = max(login_limiter.retry_after(ip), user_limiter.retry_after(name))
    if wait:
        return render(request, "admin_login.html", 429, error=f"Zu viele Versuche. Bitte {wait} Sekunden warten.",
                      )
    user = db.scalar(select(User).where(User.username == name)) if name else None
    if user is not None and verify_password(password, user.password_hash):
        login_user(request, user)
        request.state.user = user
        audit(db, request, "login")
        db.commit()
        return go("/admin")
    if user is None:  # gleiche Rechenzeit wie bei vorhandenem Benutzer (verrät nicht, ob es ihn gibt)
        burn_password_time(password)
    login_limiter.fail(ip)
    user_limiter.fail(name)
    audit(db, request, "login_failed", f"Benutzername: {name}", username="")
    db.commit()
    return render(request, "admin_login.html", 401, error="Benutzername oder Passwort falsch.")


# ------------------------------------------------------------ Ersteinrichtung

def _setup_page(request: Request, db: Session, status: int = 200, error: str | None = None, **values):
    """Zeigt das Einrichtungsformular (mit sinnvollen Vorgaben, falls nichts eingegeben wurde)."""
    values.setdefault("school_name", app_settings.get()["school_name"])
    values.setdefault("public_url", app_settings.public_url(request))
    return render(request, "admin_setup.html", status, error=error, **values)


@router.get("/setup")
def setup_page(request: Request, db: Session = Depends(get_db)):
    """Ersteinrichtung einer frischen Installation (nur solange es noch keinen Benutzer gibt)."""
    if has_users(db):
        return go("/admin/login")
    return _setup_page(request, db)


@router.post("/setup")
async def setup(request: Request, db: Session = Depends(get_db)):
    """Legt mit dem Einrichtungscode den Administrator an und speichert Schulname und QR-Adresse."""
    if has_users(db):  # Einrichtung schon abgeschlossen: nichts mehr zu tun (und nichts zu verraten)
        return go("/admin/login")
    form = await request.form()
    verify_csrf(request, form)
    ip = client_ip(request)
    values = {"school_name": str(form.get("school_name", "")).strip()[:100], "public_url": str(form.get("public_url", "")).strip()}
    wait = login_limiter.retry_after(ip)
    if wait:
        return _setup_page(request, db, 429, f"Zu viele Versuche. Bitte {wait} Sekunden warten.", **values)
    if not setup_code_matches(db, str(form.get("setup_code", ""))[:40]):
        login_limiter.fail(ip)
        audit(db, request, "setup_failed", "falscher Einrichtungscode", username="")
        db.commit()
        return _setup_page(request, db, 403, "Der Einrichtungscode stimmt nicht. Er steht im Protokoll des Servers "
                           "(docker compose logs app) bzw. am Ende von setup.sh.", **values)
    password, again = str(form.get("password", "")), str(form.get("password2", ""))
    url, url_problem = app_settings.clean_public_url(values["public_url"])
    error = None
    if password != again:
        error = "Die beiden Passwörter stimmen nicht überein."
    else:
        error = password_problem(password, "admin") or url_problem or (None if values["school_name"] else "Bitte einen Schulnamen angeben.")
    if error:
        return _setup_page(request, db, 400, error, **values)
    try:
        if not complete_setup(db, password):
            return go("/admin/login")
        app_settings.save(db, values["school_name"], public_url=url)  # speichert auch den neuen Administrator
    except IntegrityError:  # zwei Personen gleichzeitig: nur eine kommt durch
        db.rollback()
        return go("/admin/login")
    admin = db.scalar(select(User).where(User.username == "admin"))
    login_user(request, admin)
    request.state.user = admin
    audit(db, request, "setup_completed", f"Schulname: {values['school_name']}")
    db.commit()
    flash(request, "Einrichtung abgeschlossen. Willkommen! Weitere Benutzer (Wahlleiter) legst du unter „Benutzer“ an.")
    return go("/admin")


@router.post("/logout")
def logout(request: Request, form=Depends(staff_form), user: User = Depends(require_staff),
           db: Session = Depends(get_db)):
    # Sitzungen liegen im (signierten) Cookie. Damit ein kopiertes Cookie nach dem Abmelden wertlos ist,
    # wird die Sitzungsversion erhöht - das beendet auch Anmeldungen desselben Kontos auf anderen Geräten.
    """Meldet ab und entwertet alle Sitzungen dieses Kontos (auch kopierte Cookies)."""
    user.session_version = (user.session_version or 1) + 1
    audit(db, request, "logout")
    db.commit()
    request.session.clear()
    return go("/admin/login")


@router.get("/account")
def account_page(request: Request, user: User = Depends(require_staff)):
    """Seite „Mein Konto“ (Passwort ändern)."""
    return render(request, "admin_account.html", error=None)


@router.post("/account")
def account_change(request: Request, form=Depends(staff_form), user: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    """Ändert das eigene Passwort (aktuelles Passwort nötig) und beendet alle anderen Sitzungen."""
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
