"""Seiten für Schüler: Code eingeben, wählen, prüfen, abschicken."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..security import client_ip, code_limiter, public_form
from ..services import VoteError, ballot_for, cast_vote, lookup_code, parse_choices
from ..web import render

router = APIRouter()

ERROR_STATUS = {"notfound": 400, "used": 400, "closed": 403, "notstarted": 403, "choice": 400, "invalid": 400}


def _entry_error(request: Request, err: VoteError, code: str = ""):
    """Zeigt die Code-Eingabe mit Fehlermeldung (Statuscode je nach Fehlerart)."""
    return render(request, "index.html", ERROR_STATUS.get(err.kind, 400), error=err.message, code=code)


def _check_rate(request: Request):
    """Sperre pro IP nach zu vielen falschen Codes; liefert die Fehlerseite, wenn gesperrt, sonst None."""
    wait = code_limiter.retry_after(client_ip(request))
    if wait:
        return render(request, "index.html", 429,
                      error=f"Zu viele Fehlversuche. Bitte warte {wait} Sekunden und versuche es dann erneut.",
                      code="")
    return None


@router.get("/")
def index(request: Request):
    """Startseite der Schüler/innen: Code eingeben."""
    return render(request, "index.html", error=None, code="")


@router.get("/c/{code}")
def index_with_code(request: Request, code: str):
    # Der Link aus dem QR-Code füllt nur das Feld aus. Es wird nichts verbraucht.
    """Ziel des QR-Links: füllt nur das Code-Feld aus, verbraucht nichts."""
    return render(request, "index.html", error=None, code=code[:12])


def _open_ballot(request, db, raw_code, prefill=None, error=None):
    """Prüft Code und Wahlstatus und zeigt den Stimmzettel (Kandidaten in zufälliger Reihenfolge)."""
    blocked = _check_rate(request)
    if blocked:
        return blocked
    try:
        code = lookup_code(db, raw_code)
    except VoteError as err:
        code_limiter.fail(client_ip(request))
        return _entry_error(request, err, raw_code or "")
    try:
        election, races = ballot_for(db, code)
    except VoteError as err:
        return _entry_error(request, err, raw_code or "")
    return render(request, "ballot.html", 400 if error else 200, election=election, races=races,
                  department=code.department.name, code=code.code, prefill=prefill or {}, error=error)


@router.post("/vote/start")
def start(request: Request, form=Depends(public_form), db: Session = Depends(get_db)):
    """Schritt 1: Code prüfen, Stimmzettel anzeigen. Speichert nichts."""
    prefill = {k: v for k, v in form.items() if k.startswith("race_")}
    return _open_ballot(request, db, form.get("code"), prefill)


@router.post("/vote/review")
def review(request: Request, form=Depends(public_form), db: Session = Depends(get_db)):
    """Schritt 2: Auswahl prüfen und Zusammenfassung zeigen. Speichert nichts."""
    blocked = _check_rate(request)
    if blocked:
        return blocked
    raw = form.get("code")
    try:
        code = lookup_code(db, raw)
        election, races = ballot_for(db, code)
    except VoteError as err:
        code_limiter.fail(client_ip(request)) if err.kind == "notfound" else None
        return _entry_error(request, err, raw or "")
    try:
        choices = parse_choices(races, form)
    except VoteError as err:
        prefill = {k: v for k, v in form.items() if k.startswith("race_")}
        return render(request, "ballot.html", 400, election=election, races=races,
                      department=code.department.name, code=code.code, prefill=prefill, error=err.message)
    summary = []
    for race in races:
        cid = choices[race.id]
        label = next((c.name for c in race.candidates if c.id == cid), None) if cid else None
        summary.append({"race": race, "label": label, "invalid": cid is None,
                        "value": "invalid" if cid is None else str(cid)})
    return render(request, "review.html", election=election, summary=summary, code=code.code)


@router.post("/vote/submit")
def submit(request: Request, form=Depends(public_form), db: Session = Depends(get_db)):
    """Schritt 3: Stimmen verbindlich abgeben (Code atomar verbrauchen, Stimmen in die Urne)."""
    blocked = _check_rate(request)
    if blocked:
        return blocked
    raw = form.get("code")
    try:
        cast_vote(db, raw or "", form)
    except VoteError as err:
        if err.kind == "notfound":
            code_limiter.fail(client_ip(request))
        if err.kind == "choice":
            return _open_ballot(request, db, raw, {k: v for k, v in form.items() if k.startswith("race_")}, err.message)
        return _entry_error(request, err, raw or "")
    return render(request, "done.html")
