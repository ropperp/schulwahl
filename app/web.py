"""Gemeinsame Template-Hilfen."""
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config, settings
from .security import ROLE_LABEL, csrf_token
from .services import STATUS_LABEL, election_status
from .timeutil import fmt_local, input_local

BASE = Path(__file__).parent

# Abteilungsfarben laut Designrichtlinien der HTL 1 (Farben selbst stehen in static/style.css als .dept-*)
_DEPT_WORDS = (("maschinenbau", "dept-mb"), ("elektrotechnik", "dept-et"), ("mechatronik", "dept-me"))
_DEPT_SHORT = {"mb": "dept-mb", "et": "dept-et", "me": "dept-me", "lu": "dept-lu", "lut": "dept-lu"}


def dept_class(name) -> str:
    """CSS-Klasse der Abteilungsfarbe; unbekannte Abteilungen bleiben neutral (leerer String)."""
    n = str(name or "").strip().lower()
    for word, css in _DEPT_WORDS:
        if word in n:
            return css
    if "umwelt" in n and "land" in n:
        return "dept-lu"
    return _DEPT_SHORT.get(n, "")

templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters["dept_class"] = dept_class
templates.env.filters["local"] = fmt_local
templates.env.filters["localinput"] = input_local
templates.env.globals["csrf"] = csrf_token
templates.env.globals["status_of"] = election_status
templates.env.globals["STATUS_LABEL"] = STATUS_LABEL
templates.env.globals["ROLE_LABEL"] = ROLE_LABEL
templates.env.globals["year"] = lambda: datetime.now(config.TZ).year


def render(request: Request, name: str, status_code: int = 200, **ctx):
    """Rendert eine Vorlage mit den gemeinsamen Angaben (Schulname, Logo, Meldungen, angemeldeter Benutzer); Seiten sind nie zwischenspeicherbar."""
    flashes = request.session.pop("flashes", []) if "flashes" in request.session else []
    cfg = settings.get()
    ctx.update(school_name=cfg["school_name"], flashes=flashes, user=getattr(request.state, "user", None),
               logo_url=f"/media/logo?v={cfg['logo_id']}" if cfg["logo_id"] else None)
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def flash(request: Request, message: str, kind: str = "ok") -> None:
    """Merkt eine Meldung für die nächste angezeigte Seite (steht in der Sitzung)."""
    request.session.setdefault("flashes", []).append({"kind": kind, "text": message})
