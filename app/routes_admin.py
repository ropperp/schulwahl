"""Backoffice: Wahlen anlegen, Codes erzeugen, Dashboard, Exporte."""
import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import config
from .db import get_db
from . import images
from . import settings as app_settings
from .models import Candidate, Code, Department, Election, Image, Race
from .pdfs import codes_pdf, protocol_pdf
from .security import (admin_form, check_admin_password, client_ip, csrf_token, login_limiter,
                       require_admin, verify_csrf)
from .services import (STATUS_LABEL, compute_results, delete_election, election_status, generate_codes,
                       participation)
from .timeutil import parse_local, utcnow
from .web import flash, render

router = APIRouter(prefix="/admin")


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


# ------------------------------------------------------------ Login

@router.get("/login")
def login_page(request: Request):
    if request.session.get("admin"):
        return go("/admin")
    return render(request, "admin_login.html", error=None, disabled=not config.ADMIN_PASSWORD)


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    verify_csrf(request, form)
    ip = client_ip(request)
    wait = login_limiter.retry_after(ip)
    if wait:
        return render(request, "admin_login.html", 429, error=f"Zu viele Versuche. Bitte {wait} Sekunden warten.",
                      disabled=False)
    if check_admin_password(str(form.get("password", ""))):
        request.session.clear()
        request.session["admin"] = True
        csrf_token(request)
        return go("/admin")
    login_limiter.fail(ip)
    return render(request, "admin_login.html", 401, error="Falsches Passwort.", disabled=not config.ADMIN_PASSWORD)


@router.post("/logout")
def logout(request: Request, form=Depends(admin_form)):
    request.session.clear()
    return go("/admin/login")


# ------------------------------------------------------------ Übersicht / Anlegen

@router.get("")
def overview(request: Request, db: Session = Depends(get_db), _=Depends(require_admin)):
    elections = db.scalars(select(Election).order_by(Election.start_at.desc())).all()
    return render(request, "admin_elections.html", elections=elections)


@router.get("/elections/new")
def new_form(request: Request, _=Depends(require_admin)):
    from .timeutil import input_local
    start = utcnow().replace(second=0, microsecond=0) + timedelta(days=1)
    return render(request, "admin_new.html", start=input_local(start),
                  end=input_local(start + timedelta(hours=8)), error=None)


@router.post("/elections")
def create(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
    name = str(form.get("name", "")).strip()
    dept_names = [d.strip() for d in str(form.get("departments", "")).splitlines() if d.strip()]
    try:
        start, end = parse_local(str(form.get("start_at", ""))), parse_local(str(form.get("end_at", "")))
    except ValueError:
        start = end = None
    error = None
    if not name:
        error = "Bitte einen Namen angeben."
    elif start is None:
        error = "Bitte Start und Ende angeben."
    elif end <= start:
        error = "Das Ende muss nach dem Start liegen."
    elif not dept_names:
        error = "Mindestens eine Abteilung angeben."
    elif len(set(n.lower() for n in dept_names)) != len(dept_names):
        error = "Abteilungsnamen müssen unterschiedlich sein."
    if error:
        return render(request, "admin_new.html", 400, error=error, start=form.get("start_at", ""),
                      end=form.get("end_at", ""))

    e = Election(name=name, start_at=start, end_at=end,
                 code_mode="class" if form.get("code_mode") == "class" else "department")
    e.departments = [Department(name=n) for n in dept_names]
    db.add(e)
    db.flush()
    if form.get("standard_races"):
        e.races.append(Race(title="Schulsprecher/in", position=0))
        for i, d in enumerate(e.departments, start=1):
            e.races.append(Race(title="Abteilungssprecher/in", department_id=d.id, position=i))
    db.commit()
    flash(request, "Wahl angelegt. Als Nächstes die Kandidat/innen eintragen und Codes erzeugen.")
    return go(f"/admin/elections/{e.id}")


# ------------------------------------------------------------ Detailseite

@router.get("/elections/{election_id}")
def detail(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    e = get_election(db, election_id)
    races = db.scalars(
        select(Race).where(Race.election_id == e.id).options(selectinload(Race.candidates))
        .order_by(Race.position, Race.id)
    ).all()
    part = participation(db, e)
    checks = [
        ("Wahlgänge angelegt", bool(races)),
        ("Jeder Wahlgang hat Kandidat/innen", bool(races) and all(r.candidates for r in races)),
        ("Für jede Abteilung wurden Codes erzeugt", all(d["total"] > 0 for d in part["departments"])),
    ]
    return render(request, "admin_election.html", e=e, status=election_status(e), part=part, checks=checks, tab="overview")


@router.post("/elections/{election_id}/settings")
def settings(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    e = get_election(db, election_id)
    if election_status(e) == "ended":
        flash(request, "Eine beendete Wahl kann nicht mehr geändert werden.", "error")
        return go(f"/admin/elections/{e.id}")
    try:
        start, end = parse_local(str(form.get("start_at", ""))), parse_local(str(form.get("end_at", "")))
    except ValueError:
        flash(request, "Ungültiges Datum.", "error")
        return go(f"/admin/elections/{e.id}")
    name = str(form.get("name", "")).strip() or e.name
    if end <= start:
        flash(request, "Das Ende muss nach dem Start liegen.", "error")
    elif end <= utcnow():
        flash(request, "Das Ende liegt in der Vergangenheit. Zum Beenden den Button „Jetzt beenden“ nutzen.", "error")
    else:
        e.name, e.start_at, e.end_at = name, start, end
        db.commit()
        flash(request, "Gespeichert.")
    return go(f"/admin/elections/{e.id}")


@router.post("/elections/{election_id}/start-now")
def start_now(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    e = get_election(db, election_id)
    if election_status(e) == "planned":
        e.start_at = utcnow()
        if e.end_at <= e.start_at:
            e.end_at = e.start_at + timedelta(hours=8)
        db.commit()
        flash(request, "Wahl gestartet.")
    return go(f"/admin/elections/{e.id}")


@router.post("/elections/{election_id}/close")
def close(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    e = get_election(db, election_id)
    if election_status(e) != "ended":
        e.closed_at = utcnow()
        db.commit()
        flash(request, "Wahl beendet. Es werden keine Stimmen mehr angenommen.")
    return go(f"/admin/elections/{e.id}/dashboard")


@router.post("/elections/{election_id}/publish")
def publish(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    e = get_election(db, election_id)
    if election_status(e) != "ended":
        flash(request, "Das Ergebnis kann erst nach Ende der Wahl veröffentlicht werden.", "error")
    else:
        e.results_public = not e.results_public
        db.commit()
        flash(request, "Ergebnis veröffentlicht." if e.results_public else "Veröffentlichung zurückgenommen.")
    return go(f"/admin/elections/{e.id}/dashboard")


@router.post("/elections/{election_id}/delete")
def delete(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    e = get_election(db, election_id)
    delete_election(db, e)
    flash(request, "Wahl gelöscht.")
    return go("/admin")


# ------------------------------------------------------------ Wahlgänge & Kandidaten (nur vor dem Start)

def _structure_locked(request: Request, e: Election) -> bool:
    if election_status(e) != "planned":
        flash(request, "Wahlgänge und Kandidat/innen können nach dem Start nicht mehr geändert werden.", "error")
        return True
    return False


def _back(e: Election) -> RedirectResponse:
    return go(f"/admin/elections/{e.id}/candidates")


def _photo(request: Request, form) -> tuple[bytes, str] | None:
    """Liest und verarbeitet ein hochgeladenes Kandidatenfoto; None = keins hochgeladen."""
    try:
        raw = images.read_upload(form.get("photo"))
        return images.process(raw, "candidate") if raw else None
    except images.ImageError as err:
        flash(request, str(err), "error")
        return None


def _store_image(db: Session, data: tuple[bytes, str]) -> int:
    img = Image(data=data[0], content_type=data[1])
    db.add(img)
    db.flush()
    return img.id


def _drop_image(db: Session, image_id: int | None) -> None:
    if image_id:
        db.query(Image).filter(Image.id == image_id).delete()


@router.get("/elections/{election_id}/candidates")
def candidates_page(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    e = get_election(db, election_id)
    races = db.scalars(
        select(Race).where(Race.election_id == e.id)
        .options(selectinload(Race.candidates), selectinload(Race.department))
        .order_by(Race.position, Race.id)
    ).all()
    return render(request, "admin_candidates.html", e=e, races=races, status=election_status(e), tab="candidates")


@router.post("/elections/{election_id}/races")
def add_race(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    e = get_election(db, election_id)
    title = str(form.get("title", "")).strip()
    dept_raw = str(form.get("department_id", ""))
    if not _structure_locked(request, e) and title:
        dept_id = int(dept_raw) if dept_raw.isdigit() else None
        if dept_id is not None and dept_id not in {d.id for d in e.departments}:
            raise HTTPException(400, "Unbekannte Abteilung")
        db.add(Race(election_id=e.id, department_id=dept_id, title=title[:200], position=len(e.races)))
        db.commit()
    return _back(e)


@router.post("/races/{race_id}/rename")
def rename_race(request: Request, race_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    race = db.get(Race, race_id) or _404()
    e = get_election(db, race.election_id)
    title = str(form.get("title", "")).strip()
    if not _structure_locked(request, e) and title:
        race.title = title[:200]
        db.commit()
    return _back(e)


@router.post("/races/{race_id}/move")
def move_race(request: Request, race_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    race = db.get(Race, race_id) or _404()
    e = get_election(db, race.election_id)
    if not _structure_locked(request, e):
        ordered = list(db.scalars(select(Race).where(Race.election_id == e.id).order_by(Race.position, Race.id)))
        i = next(k for k, r in enumerate(ordered) if r.id == race.id)
        j = i - 1 if form.get("dir") == "up" else i + 1
        if 0 <= j < len(ordered):
            ordered[i], ordered[j] = ordered[j], ordered[i]
        for pos, r in enumerate(ordered):
            r.position = pos
        db.commit()
    return _back(e)


@router.post("/races/{race_id}/delete")
def delete_race(request: Request, race_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    race = db.get(Race, race_id) or _404()
    e = get_election(db, race.election_id)
    if not _structure_locked(request, e):
        image_ids = [c.image_id for c in race.candidates]
        db.delete(race)
        db.flush()
        for image_id in image_ids:
            _drop_image(db, image_id)
        db.commit()
    return _back(e)


@router.post("/races/{race_id}/candidates")
def add_candidate(request: Request, race_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    race = db.get(Race, race_id) or _404()
    e = get_election(db, race.election_id)
    name = str(form.get("name", "")).strip()
    if _structure_locked(request, e):
        return _back(e)
    if not name:
        flash(request, "Bitte einen Namen angeben.", "error")
        return _back(e)
    photo = _photo(request, form)
    if photo is None and form.get("photo") is not None and getattr(form.get("photo"), "filename", ""):
        return _back(e)  # Fehler wurde schon gemeldet, nichts anlegen
    cand = Candidate(race_id=race.id, name=name[:200], info=str(form.get("info", "")).strip()[:200])
    if photo:
        cand.image_id = _store_image(db, photo)
    db.add(cand)
    db.commit()
    return _back(e)


@router.post("/candidates/{candidate_id}/edit")
def edit_candidate(request: Request, candidate_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    cand = db.get(Candidate, candidate_id) or _404()
    e = get_election(db, db.get(Race, cand.race_id).election_id)
    if _structure_locked(request, e):
        return _back(e)
    name = str(form.get("name", "")).strip()
    if not name:
        flash(request, "Der Name darf nicht leer sein.", "error")
        return _back(e)
    photo = _photo(request, form)
    if photo is None and getattr(form.get("photo"), "filename", ""):
        return _back(e)
    cand.name, cand.info = name[:200], str(form.get("info", "")).strip()[:200]
    if photo or form.get("remove_photo"):
        _drop_image_later = cand.image_id
        cand.image_id = _store_image(db, photo) if photo else None
        db.flush()
        _drop_image(db, _drop_image_later)
    db.commit()
    return _back(e)


@router.post("/candidates/{candidate_id}/delete")
def delete_candidate(request: Request, candidate_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    cand = db.get(Candidate, candidate_id) or _404()
    e = get_election(db, db.get(Race, cand.race_id).election_id)
    if not _structure_locked(request, e):
        image_id = cand.image_id
        db.delete(cand)
        db.flush()
        _drop_image(db, image_id)
        db.commit()
    return _back(e)


def _404():
    raise HTTPException(404, "Nicht gefunden")


# ------------------------------------------------------------ Einstellungen (Schulname, Logo)

@router.get("/settings")
def settings_page(request: Request, _=Depends(require_admin)):
    return render(request, "admin_settings.html", cfg=app_settings.get(), tab=None)


@router.post("/settings")
def settings_save(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
    name = str(form.get("school_name", "")).strip()[:100]
    if not name:
        flash(request, "Bitte einen Schulnamen angeben.", "error")
        return go("/admin/settings")
    logo = None
    try:
        raw = images.read_upload(form.get("logo"))
        logo = images.process(raw, "logo") if raw else None
    except images.ImageError as err:
        flash(request, str(err), "error")
        return go("/admin/settings")
    app_settings.save(db, name, logo, remove_logo=bool(form.get("remove_logo")))
    flash(request, "Einstellungen gespeichert.")
    return go("/admin/settings")


# ------------------------------------------------------------ Codes

@router.get("/elections/{election_id}/codes")
def codes_page(request: Request, election_id: int, department_id: int | None = None, group_name: str = "",
               state: str = "all",
               db: Session = Depends(get_db), _=Depends(require_admin)):
    e = get_election(db, election_id)
    q = select(Code).where(Code.election_id == e.id).options(selectinload(Code.department))
    if department_id:
        q = q.where(Code.department_id == department_id)
    if group_name:
        q = q.where(Code.group_name == group_name)
    if state == "unused":
        q = q.where(Code.used.is_(False))
    elif state == "used":
        q = q.where(Code.used.is_(True))
    codes = db.scalars(q).all()
    codes.sort(key=lambda c: (c.department.name, c.group_name, c.code))
    return render(request, "admin_codes.html", e=e, status=election_status(e), part=participation(db, e),
                  codes=codes, department_id=department_id, group_name=group_name, state=state, tab="codes",
                  default_base=config.PUBLIC_URL or str(request.base_url).rstrip("/"))


@router.post("/elections/{election_id}/codes/delete-unused")
def delete_unused_codes(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Löscht alle noch nicht verwendeten Codes einer Abteilung (und optional einer Klasse)."""
    e = get_election(db, election_id)
    if election_status(e) == "ended":
        flash(request, "Die Wahl ist beendet.", "error")
        return go(f"/admin/elections/{e.id}/codes")
    dept_raw = str(form.get("department_id", ""))
    if not dept_raw.isdigit() or int(dept_raw) not in {d.id for d in e.departments}:
        raise HTTPException(400, "Unbekannte Abteilung")
    q = sa_delete(Code).where(Code.election_id == e.id, Code.department_id == int(dept_raw), Code.used.is_(False))
    group = str(form.get("group_name", "")).strip()
    if group:
        q = q.where(Code.group_name == group)
    n = db.execute(q).rowcount
    db.commit()
    flash(request, f"{n} offene Codes gelöscht. Bereits verwendete Codes bleiben bestehen.")
    return go(f"/admin/elections/{e.id}/codes")


@router.post("/codes/{code_id}/delete")
def delete_code(request: Request, code_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    code = db.get(Code, code_id) or _404()
    e = get_election(db, code.election_id)
    if code.used:
        flash(request, "Ein bereits verwendeter Code kann nicht gelöscht werden.", "error")
    elif election_status(e) == "ended":
        flash(request, "Die Wahl ist beendet.", "error")
    else:
        db.delete(code)
        db.commit()
        flash(request, "Code gelöscht (er ist nun ungültig).")
    return go(f"/admin/elections/{e.id}/codes")


@router.post("/elections/{election_id}/codes")
def make_codes(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    e = get_election(db, election_id)
    if election_status(e) == "ended":
        flash(request, "Die Wahl ist beendet, es können keine Codes mehr erzeugt werden.", "error")
        return go(f"/admin/elections/{e.id}/codes")
    dept = next((d for d in e.departments if str(d.id) == str(form.get("department_id"))), None)
    try:
        count = int(str(form.get("count", "0")))
    except ValueError:
        count = 0
    if dept is None or not 1 <= count <= 2000:
        flash(request, "Bitte Abteilung und eine Anzahl zwischen 1 und 2000 angeben.", "error")
        return go(f"/admin/elections/{e.id}/codes")
    group = str(form.get("group_name", "")).strip()[:100] if e.code_mode == "class" else ""
    if e.code_mode == "class" and not group:
        flash(request, "Bitte die Klasse angeben (z. B. 5AHD).", "error")
        return go(f"/admin/elections/{e.id}/codes")
    batch = generate_codes(db, e, dept, count, group)
    base = str(form.get("base_url", "")).strip().rstrip("/")
    flash(request, f"{count} Codes für {dept.name}{' · ' + group if group else ''} erzeugt. Das PDF wird geöffnet.")
    from urllib.parse import quote
    return go(f"/admin/elections/{e.id}/codes.pdf?batch={batch}&base_url={quote(base, safe='')}")


@router.get("/elections/{election_id}/codes.pdf")
def codes_download(election_id: int, request: Request, batch: str = "", department_id: int | None = None,
                   group_name: str = "", unused: int = 1, base_url: str = "", db: Session = Depends(get_db),
                   _=Depends(require_admin)):
    e = get_election(db, election_id)
    q = select(Code).where(Code.election_id == e.id).options(selectinload(Code.department))
    if batch:
        q = q.where(Code.batch == batch)
    if department_id:
        q = q.where(Code.department_id == department_id)
    if group_name:
        q = q.where(Code.group_name == group_name)
    if unused:
        q = q.where(Code.used.is_(False))
    codes = db.scalars(q).all()
    codes.sort(key=lambda c: (c.department.name, c.group_name, c.code))
    base = (base_url or config.PUBLIC_URL or str(request.base_url)).rstrip("/")
    pdf = codes_pdf(e.name, [(c.department.name + (f" · {c.group_name}" if c.group_name else ""), c.code)
                             for c in codes], base)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="wahlcodes-{e.id}.pdf"',
                             "Cache-Control": "no-store"})


# ------------------------------------------------------------ Stichwahl

@router.post("/elections/{election_id}/runoff")
def create_runoff(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Legt bei Stimmengleichheit eine Stichwahl als neue (geplante) Wahl an."""
    e = get_election(db, election_id)
    if election_status(e) != "ended":
        flash(request, "Eine Stichwahl kann erst nach Ende der Wahl angelegt werden.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    kind = "deputy" if form.get("kind") == "deputy" else "speaker"
    result = next((r for r in compute_results(db, e) if str(r["race"].id) == str(form.get("race_id"))), None)
    tied = (result or {}).get("runoff_second" if kind == "deputy" else "runoff_first") or []
    if not tied:
        flash(request, "Für diesen Wahlgang ist keine Stichwahl nötig.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    race = result["race"]
    suffix = f" – {race.department.name}" if race.department else ""
    start = utcnow() + timedelta(hours=1)
    new = Election(name=f"Stichwahl {race.title}{suffix} ({e.name})", start_at=start, end_at=start + timedelta(hours=8),
                   code_mode=e.code_mode)
    depts = [d for d in e.departments if race.department_id in (None, d.id)]
    new.departments = [Department(name=d.name) for d in depts]
    db.add(new)
    db.flush()
    new_race = Race(election_id=new.id, title=f"Stichwahl {race.title}", runoff_kind=kind, position=0)
    new.races.append(new_race)
    db.flush()
    for c in tied:
        copy = Candidate(race_id=new_race.id, name=c.name, info=c.info)
        if c.image_id:
            src = db.get(Image, c.image_id)
            copy.image_id = _store_image(db, (src.data, src.content_type))
        db.add(copy)
    db.commit()
    flash(request, "Stichwahl angelegt. Als Nächstes Zeitfenster prüfen und neue Codes erzeugen.")
    return go(f"/admin/elections/{new.id}")


# ------------------------------------------------------------ Dashboard & Exporte

@router.get("/elections/{election_id}/dashboard")
def dashboard(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    e = get_election(db, election_id)
    status = election_status(e)
    results = compute_results(db, e) if status == "ended" else None
    return render(request, "admin_dashboard.html", e=e, status=status, part=participation(db, e), results=results, tab="dashboard")


def _ended_or_redirect(request, e):
    if election_status(e) != "ended":
        flash(request, "Ergebnisse gibt es erst nach Ende der Wahl.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    return None


@router.get("/elections/{election_id}/results.csv")
def results_csv(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    e = get_election(db, election_id)
    early = _ended_or_redirect(request, e)
    if early:
        return early
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Wahlgang", "Abteilung", "Kandidat/in", "Stimmen"])
    for r in compute_results(db, e):
        for row in r["rows"]:
            w.writerow([r["race"].title, r["department"] or "alle", row["candidate"].name, row["votes"]])
        w.writerow([r["race"].title, r["department"] or "alle", "Ungültig", r["invalid"]])
    # utf-8-sig, damit Excel Umlaute richtig anzeigt
    return Response(out.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="ergebnis-{e.id}.csv"'})


@router.get("/elections/{election_id}/protocol.pdf")
def protocol(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    e = get_election(db, election_id)
    early = _ended_or_redirect(request, e)
    if early:
        return early
    cfg = app_settings.get()
    logo = db.get(Image, cfg["logo_id"]) if cfg["logo_id"] else None
    pdf = protocol_pdf(e, STATUS_LABEL["ended"], participation(db, e), compute_results(db, e),
                       cfg["school_name"], logo.data if logo else None)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="wahlprotokoll-{e.id}.pdf"'})
