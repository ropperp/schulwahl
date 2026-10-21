from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import init_db
from .routes_admin import router as admin_router
from .routes_public import router as public_router
from .routes_vote import router as vote_router
from .security import LoginRequired
from .web import render


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not config.ADMIN_PASSWORD:
        print("WARNUNG: ADMIN_PASSWORD ist nicht gesetzt - der Admin-Zugang ist gesperrt.")
    yield


app = FastAPI(title="Schulwahl", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="schulwahl_admin",
    max_age=8 * 3600,
    same_site="strict",
    https_only=config.COOKIE_SECURE,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'",
    )
    return response


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/admin/login", status_code=303)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if "text/html" in request.headers.get("accept", ""):
        return render(request, "error.html", exc.status_code, message=exc.detail)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(vote_router)
app.include_router(public_router)
app.include_router(admin_router)
