"""Öffentliche Ergebnisseite - nur wenn der Admin das Ergebnis veröffentlicht hat."""
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from . import settings
from .models import Candidate, Election, Image
from .services import compute_results, election_status, participation
from .web import render

router = APIRouter()


@router.get("/ergebnis/{election_id}")
def public_results(request: Request, election_id: int, db: Session = Depends(get_db)):
    e = db.get(Election, election_id)
    if e is None or not e.results_public or election_status(e) != "ended":
        raise HTTPException(404, "Kein veröffentlichtes Ergebnis")
    return render(request, "public_results.html", e=e, results=compute_results(db, e), part=participation(db, e))


_IMAGE_HEADERS = {"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"}


def _image_response(db: Session, image_id: int | None) -> Response:
    img = db.get(Image, image_id) if image_id else None
    if img is None:
        raise HTTPException(404, "Kein Bild")
    return Response(img.data, media_type=img.content_type, headers=_IMAGE_HEADERS)


# Kandidatenfotos werden beim Wählen pro Stimmzettel ~10x abgerufen: kurz im Speicher halten, statt jedes Mal
# zwei Datenbankabfragen zu stellen. Änderungen (nur vor Wahlbeginn möglich) sind nach spätestens einer Minute sichtbar.
_PHOTO_TTL = 60.0
_PHOTO_MAX = 512
_photo_cache: dict[int, tuple[float, bytes, str]] = {}


@router.get("/media/logo")
def logo(db: Session = Depends(get_db)):
    return _image_response(db, settings.get()["logo_id"])


@router.get("/media/candidate/{candidate_id}")
def candidate_photo(candidate_id: int, db: Session = Depends(get_db)):
    now = time.monotonic()
    hit = _photo_cache.get(candidate_id)
    if hit and hit[0] > now:
        return Response(hit[1], media_type=hit[2], headers=_IMAGE_HEADERS)
    cand = db.get(Candidate, candidate_id)
    response = _image_response(db, cand.image_id if cand else None)
    if len(_photo_cache) >= _PHOTO_MAX:
        _photo_cache.clear()
    _photo_cache[candidate_id] = (now + _PHOTO_TTL, response.body, response.media_type)
    return response


@router.get("/vergangene")
def past_elections(request: Request, db: Session = Depends(get_db)):
    """Archiv: alle veröffentlichten, beendeten Wahlen mit Gewählten."""
    elections = [e for e in db.scalars(select(Election).where(Election.results_public.is_(True))
                                       .order_by(Election.start_at.desc()))
                 if election_status(e) == "ended"]
    items = [{"e": e, "results": compute_results(db, e)} for e in elections]
    return render(request, "past.html", items=items)
