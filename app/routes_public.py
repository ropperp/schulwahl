"""Öffentliche Ergebnisseite - nur wenn der Admin das Ergebnis veröffentlicht hat."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import Election
from .services import compute_results, election_status, participation
from .web import render

router = APIRouter()


@router.get("/ergebnis/{election_id}")
def public_results(request: Request, election_id: int, db: Session = Depends(get_db)):
    e = db.get(Election, election_id)
    if e is None or not e.results_public or election_status(e) != "ended":
        raise HTTPException(404, "Kein veröffentlichtes Ergebnis")
    return render(request, "public_results.html", e=e, results=compute_results(db, e), part=participation(db, e))
