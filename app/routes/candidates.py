"""Wahlgänge und Kandidat/innen (nur vor Wahlbeginn änderbar) sowie Export/Import als Datei."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import candidate_io, images
from ..db import get_db
from ..models import Candidate, Race
from ..security import audit, require_staff, staff_form
from ..services import election_status
from ..web import flash, render
from .common import (back_to, drop_image, get_election, not_found, read_import_file, store_image, structure_locked)

router = APIRouter(prefix="/admin")


def _photo(request: Request, form) -> tuple[bytes, str] | None:
    """Liest und verarbeitet ein hochgeladenes Kandidatenfoto; None = keins hochgeladen."""
    try:
        raw = images.read_upload(form.get("photo"))
        return images.process(raw, "candidate") if raw else None
    except images.ImageError as err:
        flash(request, str(err), "error")
        return None


@router.get("/elections/{election_id}/candidates")
def candidates_page(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_staff)):
    """Reiter „Kandidat/innen“: Wahlgänge mit Kandidat/innen, Formulare zum Anlegen/Bearbeiten, Export/Import."""
    e = get_election(db, election_id)
    races = db.scalars(
        select(Race).where(Race.election_id == e.id)
        .options(selectinload(Race.candidates), selectinload(Race.department))
        .order_by(Race.position, Race.id)
    ).all()
    return render(request, "admin_candidates.html", e=e, races=races, status=election_status(e), tab="candidates")


@router.post("/elections/{election_id}/races")
def add_race(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Legt einen Wahlgang an (für alle oder eine Abteilung); nur vor Wahlbeginn."""
    e = get_election(db, election_id)
    title = str(form.get("title", "")).strip()
    dept_raw = str(form.get("department_id", ""))
    if not structure_locked(request, e) and title:
        dept_id = int(dept_raw) if dept_raw.isdigit() else None
        if dept_id is not None and dept_id not in {d.id for d in e.departments}:
            raise HTTPException(400, "Unbekannte Abteilung")
        db.add(Race(election_id=e.id, department_id=dept_id, title=title[:200], position=len(e.races)))
        db.commit()
    return back_to(e)


@router.post("/races/{race_id}/rename")
def rename_race(request: Request, race_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Benennt einen Wahlgang um; nur vor Wahlbeginn."""
    race = db.get(Race, race_id) or not_found()
    e = get_election(db, race.election_id)
    title = str(form.get("title", "")).strip()
    if not structure_locked(request, e) and title:
        race.title = title[:200]
        db.commit()
    return back_to(e)


@router.post("/races/{race_id}/move")
def move_race(request: Request, race_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Verschiebt einen Wahlgang in der Reihenfolge nach oben/unten; nur vor Wahlbeginn."""
    race = db.get(Race, race_id) or not_found()
    e = get_election(db, race.election_id)
    if not structure_locked(request, e):
        ordered = list(db.scalars(select(Race).where(Race.election_id == e.id).order_by(Race.position, Race.id)))
        i = next(k for k, r in enumerate(ordered) if r.id == race.id)
        j = i - 1 if form.get("dir") == "up" else i + 1
        if 0 <= j < len(ordered):
            ordered[i], ordered[j] = ordered[j], ordered[i]
        for pos, r in enumerate(ordered):
            r.position = pos
        db.commit()
    return back_to(e)


@router.post("/races/{race_id}/delete")
def delete_race(request: Request, race_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Löscht einen Wahlgang samt Kandidat/innen und Fotos; nur vor Wahlbeginn."""
    race = db.get(Race, race_id) or not_found()
    e = get_election(db, race.election_id)
    if not structure_locked(request, e):
        image_ids = [c.image_id for c in race.candidates]
        db.delete(race)
        db.flush()
        for image_id in image_ids:
            drop_image(db, image_id)
        db.commit()
    return back_to(e)


@router.post("/races/{race_id}/candidates")
def add_candidate(request: Request, race_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Legt einen Kandidaten an (Name, Klasse, optional Foto); nur vor Wahlbeginn."""
    race = db.get(Race, race_id) or not_found()
    e = get_election(db, race.election_id)
    name = str(form.get("name", "")).strip()
    if structure_locked(request, e):
        return back_to(e)
    if not name:
        flash(request, "Bitte einen Namen angeben.", "error")
        return back_to(e)
    photo = _photo(request, form)
    if photo is None and form.get("photo") is not None and getattr(form.get("photo"), "filename", ""):
        return back_to(e)  # Fehler wurde schon gemeldet, nichts anlegen
    cand = Candidate(race_id=race.id, name=name[:200], info=str(form.get("info", "")).strip()[:200])
    if photo:
        cand.image_id = store_image(db, photo)
    db.add(cand)
    db.commit()
    return back_to(e)


@router.post("/candidates/{candidate_id}/edit")
def edit_candidate(request: Request, candidate_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Ändert Name, Klasse und Foto eines Kandidaten; nur vor Wahlbeginn."""
    cand = db.get(Candidate, candidate_id) or not_found()
    e = get_election(db, db.get(Race, cand.race_id).election_id)
    if structure_locked(request, e):
        return back_to(e)
    name = str(form.get("name", "")).strip()
    if not name:
        flash(request, "Der Name darf nicht leer sein.", "error")
        return back_to(e)
    photo = _photo(request, form)
    if photo is None and getattr(form.get("photo"), "filename", ""):
        return back_to(e)
    cand.name, cand.info = name[:200], str(form.get("info", "")).strip()[:200]
    if photo or form.get("remove_photo"):
        _drop_image_later = cand.image_id
        cand.image_id = store_image(db, photo) if photo else None
        db.flush()
        drop_image(db, _drop_image_later)
    db.commit()
    return back_to(e)


@router.post("/candidates/{candidate_id}/delete")
def delete_candidate(request: Request, candidate_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Löscht einen Kandidaten samt Foto; nur vor Wahlbeginn."""
    cand = db.get(Candidate, candidate_id) or not_found()
    e = get_election(db, db.get(Race, cand.race_id).election_id)
    if not structure_locked(request, e):
        image_id = cand.image_id
        db.delete(cand)
        db.flush()
        drop_image(db, image_id)
        db.commit()
    return back_to(e)


@router.get("/elections/{election_id}/candidates.json")
def candidates_export(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_staff)):
    """Lädt alle Wahlgänge und Kandidat/innen als Datei herunter (zum Sichern oder für eine spätere Wahl)."""
    e = get_election(db, election_id)
    data = candidate_io.export_election(db, e)
    audit(db, request, "candidates_exported", e.name)
    db.commit()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", e.name.encode("ascii", "ignore").decode()).strip("-")[:40] or "wahl"
    return Response(data, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="kandidaten-{slug}.json"',
                             "Cache-Control": "no-store"})


@router.post("/elections/{election_id}/candidates/import")
def candidates_import(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Spielt eine Export-Datei in eine noch nicht gestartete Wahl ein (alles oder nichts)."""
    e = get_election(db, election_id)
    if structure_locked(request, e):
        return back_to(e)
    try:
        parsed = candidate_io.parse(read_import_file(form.get("import_file")))
    except candidate_io.ImportProblem as err:
        flash(request, str(err), "error")
        return back_to(e)
    stats = candidate_io.apply(db, e, parsed)
    audit(db, request, "candidates_imported", f"{e.name}: {stats['candidates']}")
    db.commit()
    flash(request, candidate_io.summary(stats))
    return back_to(e)
