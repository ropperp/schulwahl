"""Backoffice-Anmeldung (Benutzer, Rollen, Passwörter), CSRF-Schutz und Rate-Limits."""
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import AuditLog, User


class LoginRequired(Exception):
    pass


class PasswordChangeRequired(Exception):
    """Der Benutzer muss zuerst ein eigenes Passwort wählen (nach Zurücksetzen durch den Admin)."""


class FailureLimiter:
    """Sliding Window: erlaubt `max_fails` Fehlversuche je Schlüssel (IP) im Zeitfenster."""

    def __init__(self, max_fails: int, window: int):
        self.max_fails = max_fails
        self.window = window
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._fails[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Sekunden bis zur nächsten erlaubten Anfrage; 0 = nicht gesperrt."""
        now = time.monotonic()
        with self._lock:
            if key not in self._fails:
                return 0
            q = self._prune(key, now)
            if len(q) >= self.max_fails:
                return max(1, int(q[0] + self.window - now))
            return 0

    MAX_KEYS = 10_000

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now).append(now)
            if len(self._fails) > self.MAX_KEYS:  # Speicherschutz: abgelaufene Schlüssel entfernen
                for k in [k for k in self._fails if not self._prune(k, now)]:
                    del self._fails[k]

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()


code_limiter = FailureLimiter(config.CODE_MAX_FAILS, config.CODE_WINDOW_SECONDS)
login_limiter = FailureLimiter(config.LOGIN_MAX_FAILS, config.LOGIN_WINDOW_SECONDS)
user_limiter = FailureLimiter(config.LOGIN_USER_MAX_FAILS, config.LOGIN_USER_WINDOW_SECONDS)


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------ Passwörter

ROLE_ADMIN = "admin"
ROLE_WAHLLEITER = "wahlleiter"
ROLE_LABEL = {ROLE_ADMIN: "Administrator/in", ROLE_WAHLLEITER: "Wahlleiter/in"}

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1  # ca. 16 MB RAM, auch auf einem Raspberry Pi flott genug
_WEAK = {"passwort123", "password123", "1234567890", "0123456789", "qwertzuiop", "qwertyuiop", "passwort12",
         "schulwahl1", "willkommen1", "wahlleiter1"}


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode()[:MAX_PASSWORD_LENGTH * 4], salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH = hash_password(secrets.token_hex(8))


def burn_password_time(password: str) -> None:
    """Gleicht die Antwortzeit bei unbekanntem Benutzernamen an (kein Rückschluss, ob es den Benutzer gibt)."""
    verify_password(password, _DUMMY_HASH)


def password_problem(password: str, username: str = "") -> str | None:
    """None, wenn das Passwort akzeptabel ist; sonst eine Fehlermeldung."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Das Passwort darf höchstens {MAX_PASSWORD_LENGTH} Zeichen lang sein."
    if len(set(password)) < 4:
        return "Das Passwort ist zu einfach (zu wenige verschiedene Zeichen)."
    low = password.lower()
    if low in _WEAK or (username and username.lower() in low):
        return "Das Passwort ist zu leicht zu erraten. Bitte etwas anderes wählen (z. B. drei Wörter hintereinander)."
    return None


def random_password(length: int = 14) -> str:
    """Zufallspasswort ohne leicht verwechselbare Zeichen (für Erstpasswörter, die der Admin vergibt)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_username(raw: str) -> str:
    return raw.strip().lower()


def username_problem(name: str) -> str | None:
    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,29}", name):
        return "Der Benutzername muss 3–30 Zeichen lang sein und darf nur Kleinbuchstaben, Zahlen, Punkt, Strich und Unterstrich enthalten."
    return None


# ------------------------------------------------------------ Sitzung / Rollen

def login_user(request: Request, user: User) -> None:
    """Neue Sitzung nach erfolgreichem Login (verhindert Session-Fixation)."""
    request.session.clear()
    request.session["uid"] = user.id
    request.session["sv"] = user.session_version
    csrf_token(request)


def current_user(request: Request, db: Session) -> User | None:
    uid, sv = request.session.get("uid"), request.session.get("sv")
    if not isinstance(uid, int) or not isinstance(sv, int):
        return None
    user = db.get(User, uid)
    if user is None or user.session_version != sv:
        return None
    return user


_PASSWORD_PAGES = ("/admin/account", "/admin/logout")


def require_staff(request: Request, db: Session = Depends(get_db)) -> User:
    """Jede angemeldete Person im Backoffice (Admin oder Wahlleiter)."""
    user = current_user(request, db)
    if user is None:
        raise LoginRequired()
    if user.must_change and request.url.path not in _PASSWORD_PAGES:
        raise PasswordChangeRequired()
    request.state.user = user
    return user


def require_admin(user: User = Depends(require_staff)) -> User:
    """Nur Administrator/innen."""
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Dafür sind Administrator-Rechte nötig.")
    return user


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, form) -> None:
    expected = request.session.get("csrf", "")
    given = form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(str(given).encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Ungültiges Formular-Token. Seite neu laden.")


async def staff_form(request: Request, user: User = Depends(require_staff)):
    """Dependency für POSTs im Backoffice: Login prüfen, CSRF prüfen, Formulardaten liefern."""
    form = await request.form()
    verify_csrf(request, form)
    return form


async def admin_form(request: Request, user: User = Depends(require_admin)):
    """Wie staff_form, aber nur für Administrator/innen."""
    form = await request.form()
    verify_csrf(request, form)
    return form


async def public_form(request: Request):
    return await request.form()


def audit(db: Session, request: Request, action: str, detail: str = "", username: str | None = None) -> None:
    """Protokolliert eine Verwaltungsaktion (wird mit dem nächsten commit gespeichert)."""
    user = getattr(request.state, "user", None)
    db.add(AuditLog(username=username if username is not None else (user.username if user else ""),
                    ip=client_ip(request)[:64], action=action[:60], detail=detail[:300]))
