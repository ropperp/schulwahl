"""Gemeinsame Hilfen der Backoffice-Seiten (Weiterleitung, Wahl laden, Bilder, Sperre nach Wahlbeginn)."""
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import candidate_io
from ..models import Election, Image
from ..services import election_status
from ..web import flash


def go(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def get_election(db: Session, election_id: int) -> Election:
    e = db.scalar(
        select(Election).where(Election.id == election_id)
        .options(selectinload(Election.departments), selectinload(Election.races))
    )
    if e is None:
        raise HTTPException(404, "Wahl nicht gefunden")
    return e


def not_found():
    raise HTTPException(404, "Nicht gefunden")


def read_import_file(upload) -> bytes | None:
    if upload is None or not getattr(upload, "filename", ""):
        return None
    data = upload.file.read(candidate_io.MAX_FILE_BYTES + 1)
    if len(data) > candidate_io.MAX_FILE_BYTES:
        raise candidate_io.ImportProblem("Die Datei ist zu groß (max. 6 MB).")
    return data or None


def store_image(db: Session, data: tuple[bytes, str]) -> int:
    img = Image(data=data[0], content_type=data[1])
    db.add(img)
    db.flush()
    return img.id


def drop_image(db: Session, image_id: int | None) -> None:
    if image_id:
        db.query(Image).filter(Image.id == image_id).delete()


def structure_locked(request: Request, e: Election) -> bool:
    if election_status(e) != "planned":
        flash(request, "Wahlgänge und Kandidat/innen können nach dem Start nicht mehr geändert werden.", "error")
        return True
    return False


def back_to(e: Election) -> RedirectResponse:
    return go(f"/admin/elections/{e.id}/candidates")
