"""Wahlen: Übersicht, Anlegen, Detailseite, Start/Ende, Veröffentlichen, Stichwahl-Entscheidung, Stichwahlen, Dashboard, Exporte."""
import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import candidate_io
from .. import settings as app_settings
from ..db import get_db
from ..models import Candidate, Department, Election, Image, Race, User, Vote
from ..pdfs import official_protocol_pdf, protocol_pdf
from ..security import admin_form, audit, require_staff, staff_form
from ..services import (STATUS_LABEL, compute_results, delete_election, election_status, name_problem, participation,
                        set_manual_results, short_name)
from ..timeutil import parse_local, utcnow
from ..web import flash, render
from .common import (get_election, go, not_found, read_import_file, store_image)

router = APIRouter(prefix="/admin")


@router.get("")
def overview(request: Request, db: Session = Depends(get_db), _=Depends(require_staff)):
    """Startseite des Backoffice: alle Wahlen, neueste zuerst."""
    elections = db.scalars(select(Election).order_by(Election.start_at.desc())).all()
    return render(request, "admin_elections.html", elections=elections)


@router.get("/elections/new")
def new_form(request: Request, _=Depends(require_staff)):
    """Formular „Neue Wahl“ (Zeiten sind mit morgen 8 Stunden vorbelegt)."""
    from ..timeutil import input_local
    start = utcnow().replace(second=0, microsecond=0) + timedelta(days=1)
    return render(request, "admin_new.html", start=input_local(start),
                  end=input_local(start + timedelta(hours=8)), error=None)


@router.post("/elections")
def create(request: Request, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Legt eine Wahl an (Name höchstens 40 Zeichen), optional mit Standard-Wahlgängen und Kandidaten aus einer Export-Datei."""
    name = str(form.get("name", "")).strip()
    dept_names = [d.strip() for d in str(form.get("departments", "")).splitlines() if d.strip()]
    try:
        start, end = parse_local(str(form.get("start_at", ""))), parse_local(str(form.get("end_at", "")))
    except ValueError:
        start = end = None
    error = None
    if not name:
        error = "Bitte einen Namen angeben."
    elif name_problem(name):
        error = name_problem(name)
    elif start is None:
        error = "Bitte Start und Ende angeben."
    elif end <= start:
        error = "Das Ende muss nach dem Start liegen."
    elif not dept_names:
        error = "Mindestens eine Abteilung angeben."
    elif len(set(n.lower() for n in dept_names)) != len(dept_names):
        error = "Abteilungsnamen müssen unterschiedlich sein."
    parsed = None
    if not error:
        try:
            raw = read_import_file(form.get("import_file"))
            parsed = candidate_io.parse(raw) if raw else None
        except candidate_io.ImportProblem as err:
            error = str(err)
    if error:
        return render(request, "admin_new.html", 400, error=error, start=form.get("start_at", ""),
                      end=form.get("end_at", ""))

    source = "manual" if form.get("source") == "manual" else "live"
    e = Election(name=name, start_at=start, end_at=end, source=source,
                 code_mode="class" if form.get("code_mode") == "class" else "department")
    e.departments = [Department(name=n) for n in dept_names]
    db.add(e)
    db.flush()
    audit(db, request, "election_created", name)
    if form.get("standard_races"):
        e.races.append(Race(title="Schulsprecher/in", position=0))
        for i, d in enumerate(e.departments, start=1):
            e.races.append(Race(title="Abteilungssprecher/in", department_id=d.id, position=i))
    if parsed:
        stats = candidate_io.apply(db, e, parsed)
        audit(db, request, "candidates_imported", f"{e.name}: {stats['candidates']}")
    db.commit()
    if source == "manual":
        flash(request, "Wahl angelegt. " + (candidate_io.summary(stats) + " " if parsed else "") +
                       "Als Nächstes die Kandidat/innen eintragen (falls noch nicht geschehen) und im Reiter "
                       "„Ergebnisse eintragen“ die Endergebnisse erfassen.")
    else:
        flash(request, "Wahl angelegt. " + (candidate_io.summary(stats) + " Als Nächstes die Codes erzeugen." if parsed
                                            else "Als Nächstes die Kandidat/innen eintragen und Codes erzeugen."))
    return go(f"/admin/elections/{e.id}")


@router.get("/elections/{election_id}")
def detail(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_staff)):
    """Übersicht einer Wahl: Checkliste, Start/Ende steuern, Name und Zeitfenster."""
    e = get_election(db, election_id)
    races = db.scalars(
        select(Race).where(Race.election_id == e.id).options(selectinload(Race.candidates))
        .order_by(Race.position, Race.id)
    ).all()
    part = participation(db, e)
    if e.source == "manual":
        has_votes = bool(races) and db.scalar(
            select(func.count()).select_from(Vote).where(Vote.race_id.in_([r.id for r in races]))) > 0
        checks = [
            ("Wahlgänge angelegt", bool(races)),
            ("Jeder Wahlgang hat Kandidat/innen", bool(races) and all(r.candidates for r in races)),
            ("Endergebnisse eingetragen", has_votes),
        ]
    else:
        checks = [
            ("Wahlgänge angelegt", bool(races)),
            ("Jeder Wahlgang hat Kandidat/innen", bool(races) and all(r.candidates for r in races)),
            ("Für jede Abteilung wurden Codes erzeugt", all(d["total"] > 0 for d in part["departments"])),
        ]
    return render(request, "admin_election.html", e=e, status=election_status(e), part=part, checks=checks, tab="overview")


@router.post("/elections/{election_id}/settings")
def settings(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Ändert Name und Zeitfenster einer noch nicht beendeten Wahl."""
    e = get_election(db, election_id)
    if election_status(e) == "ended" and e.source != "manual":
        flash(request, "Eine beendete Wahl kann nicht mehr geändert werden.", "error")
        return go(f"/admin/elections/{e.id}")
    try:
        start, end = parse_local(str(form.get("start_at", ""))), parse_local(str(form.get("end_at", "")))
    except ValueError:
        flash(request, "Ungültiges Datum.", "error")
        return go(f"/admin/elections/{e.id}")
    name = str(form.get("name", "")).strip() or e.name
    if name_problem(name):
        flash(request, name_problem(name), "error")
        return go(f"/admin/elections/{e.id}")
    if end <= start:
        flash(request, "Das Ende muss nach dem Start liegen.", "error")
    elif end <= utcnow() and e.source != "manual":
        flash(request, "Das Ende liegt in der Vergangenheit. Zum Beenden den Button „Jetzt beenden“ nutzen.", "error")
    else:
        e.name, e.start_at, e.end_at = name, start, end
        audit(db, request, "election_changed", name)
        db.commit()
        flash(request, "Gespeichert.")
    return go(f"/admin/elections/{e.id}")


@router.get("/elections/{election_id}/manual-results")
def manual_results_form(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_staff)):
    """Formular zum Eintragen der Endergebnisse einer manuell erfassten (bereits analog durchgeführten) Wahl."""
    e = get_election(db, election_id)
    if e.source != "manual":
        raise HTTPException(404, "Nicht gefunden")
    races = db.scalars(
        select(Race).where(Race.election_id == e.id)
        .options(selectinload(Race.candidates), selectinload(Race.department))
        .order_by(Race.position, Race.id)
    ).all()
    counts = {
        race.id: {c.id: db.scalar(select(func.count()).select_from(Vote)
                                   .where(Vote.race_id == race.id, Vote.candidate_id == c.id)) for c in race.candidates}
        for race in races
    }
    invalid = {race.id: db.scalar(select(func.count()).select_from(Vote)
                                   .where(Vote.race_id == race.id, Vote.candidate_id.is_(None))) for race in races}
    return render(request, "admin_manual_results.html", e=e, races=races, status=election_status(e),
                 counts=counts, invalid=invalid, tab="manual_results")


@router.post("/elections/{election_id}/manual-results")
def manual_results_save(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Speichert die eingetragenen Endergebnisse (ersetzt bestehende Stimmen je Wahlgang vollständig)."""
    e = get_election(db, election_id)
    if e.source != "manual":
        raise HTTPException(404, "Nicht gefunden")
    if e.results_public:
        flash(request, "Das Ergebnis ist veröffentlicht. Zum Ändern zuerst die Veröffentlichung zurücknehmen.", "error")
        return go(f"/admin/elections/{e.id}/manual-results")
    races = db.scalars(
        select(Race).where(Race.election_id == e.id).options(selectinload(Race.candidates))
    ).all()

    def _num(key: str) -> int:
        """Liest ein nichtnegatives Zahlenfeld aus dem Formular; ungültige Eingaben werden zu 0."""
        raw = str(form.get(key, "0")).strip()
        try:
            return max(0, int(raw)) if raw else 0
        except ValueError:
            return 0

    for race in races:
        counts = {c.id: _num(f"votes_{c.id}") for c in race.candidates}
        set_manual_results(db, race, counts, _num(f"invalid_{race.id}"))
    audit(db, request, "manual_results_saved", e.name)
    db.commit()
    flash(request, "Ergebnisse gespeichert.")
    return go(f"/admin/elections/{e.id}/dashboard")


@router.post("/elections/{election_id}/start-now")
def start_now(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Startet eine geplante Wahl sofort."""
    e = get_election(db, election_id)
    if election_status(e) == "planned":
        e.start_at = utcnow()
        if e.end_at <= e.start_at:
            e.end_at = e.start_at + timedelta(hours=8)
        audit(db, request, "election_started", e.name)
        db.commit()
        flash(request, "Wahl gestartet.")
    return go(f"/admin/elections/{e.id}")


@router.post("/elections/{election_id}/close")
def close(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Beendet die Wahl vorzeitig; danach werden keine Stimmen mehr angenommen (nicht rückgängig zu machen)."""
    e = get_election(db, election_id)
    if election_status(e) != "ended":
        e.closed_at = utcnow()
        audit(db, request, "election_closed", e.name)
        db.commit()
        flash(request, "Wahl beendet. Es werden keine Stimmen mehr angenommen.")
    return go(f"/admin/elections/{e.id}/dashboard")


@router.post("/elections/{election_id}/publish")
def publish(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Veröffentlicht das Ergebnis (nur wenn jeder Wahlgang zur Stichwahl entschieden ist) oder nimmt es zurück."""
    e = get_election(db, election_id)
    if election_status(e) != "ended":
        flash(request, "Das Ergebnis kann erst nach Ende der Wahl veröffentlicht werden.", "error")
    elif not e.results_public and db.scalar(
            select(func.count()).select_from(Race).where(Race.election_id == e.id, Race.runoff_decision == "")):
        flash(request, "Bitte zuerst bei jedem Wahlgang entscheiden, ob eine Stichwahl durchgeführt wird. "
                       "Erst danach kann das Ergebnis veröffentlicht werden.", "error")
    else:
        e.results_public = not e.results_public
        audit(db, request, "results_published" if e.results_public else "results_unpublished", e.name)
        db.commit()
        flash(request, "Ergebnis veröffentlicht." if e.results_public else "Veröffentlichung zurückgenommen.")
    return go(f"/admin/elections/{e.id}/dashboard")


@router.post("/elections/{election_id}/delete")
def delete(request: Request, election_id: int, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Löscht eine Wahl samt Codes und Stimmen endgültig (nur Administrator)."""
    e = get_election(db, election_id)
    audit(db, request, "election_deleted", e.name)
    delete_election(db, e)
    flash(request, "Wahl gelöscht.")
    return go("/admin")


@router.post("/races/{race_id}/runoff-decision")
def runoff_decision(request: Request, race_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Wahlleitung entscheidet pro Wahlgang: Stichwahl ja/nein (oder Entscheidung zurücknehmen). Gesperrt, solange veröffentlicht."""
    race = db.get(Race, race_id) or not_found()
    e = get_election(db, race.election_id)
    answer = str(form.get("answer", ""))
    if answer not in ("yes", "no", ""):
        raise HTTPException(400, "Ungültige Eingabe")
    if election_status(e) != "ended":
        flash(request, "Über eine Stichwahl kann erst nach Ende der Wahl entschieden werden.", "error")
    elif e.results_public:
        flash(request, "Das Ergebnis ist veröffentlicht. Zum Ändern zuerst die Veröffentlichung zurücknehmen.", "error")
    else:
        race.runoff_decision = answer
        audit(db, request, "runoff_decision", f"{e.name} / {race.title}: {answer or 'zurückgenommen'}")
        db.commit()
    return go(f"/admin/elections/{e.id}/dashboard#race-{race.id}")


def runoff_departments(e: Election, results: list[dict]) -> list[Department]:
    """Abteilungen, die an den Stichwahlen teilnehmen: alle, sobald ein Wahlgang für alle gilt, sonst nur die betroffenen."""
    if any(r["race"].department_id is None for r in results):
        return list(e.departments)
    ids = {r["race"].department_id for r in results}
    return [d for d in e.departments if d.id in ids]


def runoff_candidates(results: list[dict]) -> list[dict]:
    """Wahlgänge, bei denen die Wahlleitung „Stichwahl: ja“ entschieden hat und Kandidat/innen antreten."""
    return [r for r in results if r["decision"] == "yes" and r["plan"]]


def _build_runoff(db: Session, e: Election, results: list[dict], name: str) -> Election:
    """Legt EINE neue (geplante) Wahl mit den Stichwahl-Wahlgängen an. Wahlgänge einer Abteilung gelten nur für diese."""
    start = utcnow() + timedelta(hours=1)
    new = Election(name=name, start_at=start, end_at=start + timedelta(hours=8), code_mode=e.code_mode)
    new.departments = [Department(name=d.name) for d in runoff_departments(e, results)]
    db.add(new)
    db.flush()
    new_dept = {d.name: d for d in new.departments}
    old_dept = {d.id: d for d in e.departments}
    for position, result in enumerate(results):
        race = result["race"]
        dept_id = new_dept[old_dept[race.department_id].name].id if race.department_id else None
        new_race = Race(election_id=new.id, department_id=dept_id, title=f"Stichwahl {race.title}",
                        runoff_kind=result["plan_kind"], position=position)
        db.add(new_race)
        db.flush()
        for c in result["plan"]:
            copy = Candidate(race_id=new_race.id, name=c.name, info=c.info)
            if c.image_id:
                src = db.get(Image, c.image_id)
                copy.image_id = store_image(db, (src.data, src.content_type))
            db.add(copy)
    return new


@router.post("/elections/{election_id}/runoff")
def create_runoff(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Legt für EINEN Wahlgang (Entscheidung „Stichwahl: ja“) eine Stichwahl als neue (geplante) Wahl an."""
    e = get_election(db, election_id)
    if election_status(e) != "ended":
        flash(request, "Eine Stichwahl kann erst nach Ende der Wahl angelegt werden.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    result = next((r for r in compute_results(db, e) if str(r["race"].id) == str(form.get("race_id"))), None)
    if result is None or result["decision"] != "yes":
        flash(request, "Bitte zuerst bei diesem Wahlgang entscheiden: „Stichwahl durchführen“.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    if not result["plan"]:
        flash(request, "Für diesen Wahlgang gibt es keine zwei Kandidat/innen mit Stimmen - keine Stichwahl möglich.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    race = result["race"]
    suffix = f" – {race.department.name}" if race.department else ""
    new = _build_runoff(db, e, [result], short_name(f"Stichwahl {race.title}{suffix}", f"({e.name})"))
    audit(db, request, "runoff_created", new.name)
    db.commit()
    flash(request, "Stichwahl angelegt. Als Nächstes Zeitfenster prüfen und neue Codes erzeugen.")
    return go(f"/admin/elections/{new.id}")


@router.post("/elections/{election_id}/runoff-all")
def create_runoff_all(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    """Fasst ALLE Wahlgänge mit Entscheidung „Stichwahl: ja“ in einer einzigen neuen Wahl zusammen.

    Jede/r wählt mit einem Code alle Stichwahlen, die für die eigene Abteilung gelten. Abteilungen ohne Stichwahl
    (nur Abteilungssprecher betroffen) kommen in der neuen Wahl gar nicht vor.
    """
    e = get_election(db, election_id)
    if election_status(e) != "ended":
        flash(request, "Eine Stichwahl kann erst nach Ende der Wahl angelegt werden.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    chosen = runoff_candidates(compute_results(db, e))
    if not chosen:
        flash(request, "Es gibt keinen Wahlgang mit der Entscheidung „Stichwahl durchführen“.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    new = _build_runoff(db, e, chosen, short_name("Stichwahlen", f"({e.name})"))
    audit(db, request, "runoff_created", f"{new.name}: {len(chosen)} Wahlgänge")
    db.commit()
    flash(request, f"Stichwahl mit {len(chosen)} Wahlgängen angelegt. Als Nächstes Zeitfenster prüfen und neue Codes erzeugen.")
    return go(f"/admin/elections/{new.id}")


@router.get("/elections/{election_id}/dashboard")
def dashboard(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_staff)):
    """Beteiligung live; nach Wahlende Ergebnisse, Empfehlung und Entscheidung zur Stichwahl."""
    e = get_election(db, election_id)
    status = election_status(e)
    results = compute_results(db, e) if status == "ended" else None
    combined = runoff_candidates(results) if results else []
    return render(request, "admin_dashboard.html", e=e, status=status, part=participation(db, e), results=results,
                  combined=combined, combined_depts=[d.name for d in runoff_departments(e, combined)] if combined else [],
                  tab="dashboard")


def _ended_or_redirect(request, e):
    """Ergebnisse gibt es erst nach Wahlende: sonst Hinweis und Weiterleitung zum Dashboard."""
    if election_status(e) != "ended":
        flash(request, "Ergebnisse gibt es erst nach Ende der Wahl.", "error")
        return go(f"/admin/elections/{e.id}/dashboard")
    return None


def _csv_safe(value):
    """Verhindert Formel-Einschleusung in Excel/LibreOffice (Zellen, die mit = + - @ beginnen)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@router.get("/elections/{election_id}/results.csv")
def results_csv(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_staff)):
    """Ergebnis als CSV (Excel-tauglich, Formelzeichen entschärft); erst nach Wahlende."""
    e = get_election(db, election_id)
    early = _ended_or_redirect(request, e)
    if early:
        return early
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Wahlgang", "Abteilung", "Kandidat/in", "Stimmen"])
    for r in compute_results(db, e):
        for row in r["rows"]:
            w.writerow([_csv_safe(x) for x in (r["race"].title, r["department"] or "alle", row["candidate"].name, row["votes"])])
        w.writerow([_csv_safe(r["race"].title), r["department"] or "alle", "Ungültig", r["invalid"]])
    # utf-8-sig, damit Excel Umlaute richtig anzeigt
    return Response(out.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="ergebnis-{e.id}.csv"'})


@router.get("/elections/{election_id}/protocol.pdf")
def protocol(request: Request, election_id: int, db: Session = Depends(get_db), _=Depends(require_staff)):
    """Wahlprotokoll als PDF mit Logo und Unterschriftenzeilen; erst nach Wahlende."""
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


@router.get("/elections/{election_id}/protocol-form.pdf")
def protocol_form(request: Request, election_id: int, witnesses: list[str] = Query(default=[]),
                  race_id: str = "", db: Session = Depends(get_db), user: User = Depends(require_staff)):
    """Wahlprotokoll im Stil des bisherigen Papierformulars (farbiges Logo, Unterschriftsfelder); erst nach Wahlende.

    Ein Blatt je Wahlgang mit eigenem Kopf und eigenen Unterschriftsfeldern. `race_id` (optional) beschränkt auf
    einen einzelnen Wahlgang; leerer String (so schickt das Dashboard-Formular es ohne Auswahl) zählt als kein Filter.
    """
    e = get_election(db, election_id)
    early = _ended_or_redirect(request, e)
    if early:
        return early
    cfg = app_settings.get()
    logo = db.get(Image, cfg["protocol_logo_id"]) if cfg["protocol_logo_id"] else None
    results = compute_results(db, e)
    if race_id.strip():
        try:
            rid = int(race_id)
        except ValueError:
            raise HTTPException(400, "Ungültiger Wahlgang") from None
        results = [r for r in results if r["race"].id == rid]
        if not results:
            raise HTTPException(404, "Wahlgang nicht gefunden")
    pdf = official_protocol_pdf(e, results, cfg["school_name"], logo.data if logo else None,
                                user.full_name or user.username, witnesses=witnesses,
                                location=cfg["protocol_location"], font_sizes=cfg["protocol_fonts"],
                                spacing=cfg["protocol_spacing"])
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="protokoll-papierform-{e.id}.pdf"'})
