"""Gemeinsame Template-Hilfen."""
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config
from .security import csrf_token
from .services import STATUS_LABEL, election_status
from .timeutil import fmt_local, input_local

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters["local"] = fmt_local
templates.env.filters["localinput"] = input_local
templates.env.globals["csrf"] = csrf_token
templates.env.globals["status_of"] = election_status
templates.env.globals["STATUS_LABEL"] = STATUS_LABEL


def render(request: Request, name: str, status_code: int = 200, **ctx):
    flashes = request.session.pop("flashes", []) if "flashes" in request.session else []
    ctx.update(school_name=config.SCHOOL_NAME, flashes=flashes)
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session.setdefault("flashes", []).append({"kind": kind, "text": message})
