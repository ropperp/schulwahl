"""Admin-Login, CSRF-Schutz und Rate-Limit."""
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from . import config


class LoginRequired(Exception):
    pass


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
            q = self._prune(key, now)
            if len(q) >= self.max_fails:
                return max(1, int(q[0] + self.window - now))
            return 0

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now).append(now)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()


code_limiter = FailureLimiter(config.CODE_MAX_FAILS, config.CODE_WINDOW_SECONDS)
login_limiter = FailureLimiter(config.LOGIN_MAX_FAILS, config.LOGIN_WINDOW_SECONDS)


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_admin_password(candidate: str) -> bool:
    if not config.ADMIN_PASSWORD:
        return False  # ohne gesetztes Passwort ist der Admin-Zugang gesperrt
    return hmac.compare_digest(candidate.encode(), config.ADMIN_PASSWORD.encode())


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
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Ungültiges Formular-Token. Seite neu laden.")


def require_admin(request: Request) -> None:
    if not request.session.get("admin"):
        raise LoginRequired()


async def admin_form(request: Request):
    """Dependency für Admin-POSTs: Login prüfen, CSRF prüfen, Formulardaten liefern."""
    require_admin(request)
    form = await request.form()
    verify_csrf(request, form)
    return form


async def public_form(request: Request):
    return await request.form()
