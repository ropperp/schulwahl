import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import DataError
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .accounts import bootstrap_admin
from .db import init_db
from .routes_account import router as account_router
from .routes_admin import router as admin_router
from .routes_public import router as public_router
from .routes_vote import router as vote_router
from .security import LoginRequired, PasswordChangeRequired
from .web import render


# Die schlanke Docker-Umgebung kennt .woff2 nicht: sonst käme application/octet-stream
mimetypes.add_type("font/woff2", ".woff2")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if bootstrap_admin() == "missing":
        print("WARNUNG: Es gibt noch keinen Benutzer und ADMIN_PASSWORD ist nicht gesetzt - das Backoffice ist gesperrt. "
              "Passwort setzen mit: docker compose exec app python -m app.manage reset-password admin")
    yield


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Weist zu große Anfragen ab (auch ohne Content-Length), bevor sie den Speicher füllen."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit = config.MAX_BODY_ADMIN if scope["path"].startswith("/admin") else config.MAX_BODY_PUBLIC
        declared = dict(scope["headers"]).get(b"content-length", b"")
        started = False

        async def too_large():
            body = b"Anfrage zu gross"
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                    (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
            await send({"type": "http.response.body", "body": body})

        if declared.isdigit() and int(declared) > limit:
            return await too_large()
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                await too_large()


app = FastAPI(title="Schulwahl", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="schulwahl_admin",
    max_age=8 * 3600,
    same_site="strict",
    https_only=config.COOKIE_SECURE,
)
app.add_middleware(BodyLimit)

_CSP = ("default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'")


class SecurityHeaders:
    """Setzt Sicherheits-Header auf jede Antwort (reine ASGI-Middleware: deutlich schneller als @app.middleware)."""

    STATIC = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"content-security-policy", _CSP.encode()),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"cross-origin-resource-policy", b"same-origin"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        static = scope["path"].startswith("/static/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message["headers"]}
                extra = [(k, v) for k, v in self.STATIC if k not in present]
                if config.COOKIE_SECURE:  # HSTS nur mit funktionierendem HTTPS sinnvoll
                    extra.append((b"strict-transport-security", b"max-age=31536000"))
                if static and b"cache-control" not in present:
                    extra.append((b"cache-control", b"public, max-age=600"))
                message = {**message, "headers": [*message["headers"], *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/admin/login", status_code=303)


@app.exception_handler(PasswordChangeRequired)
async def password_change_handler(request: Request, exc: PasswordChangeRequired):
    return RedirectResponse("/admin/account", status_code=303)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if "text/html" in request.headers.get("accept", ""):
        return render(request, "error.html", exc.status_code, message=exc.detail)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(DataError)
@app.exception_handler(OverflowError)
async def bad_input(request: Request, exc: Exception):
    """Unmögliche Werte (riesige Zahlen, Nullbytes ...) sind ein Eingabefehler und kein Serverfehler."""
    return await http_error(request, HTTPException(400, "Ungültige Eingabe."))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(vote_router)
app.include_router(public_router)
app.include_router(account_router)
app.include_router(admin_router)
