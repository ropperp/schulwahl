"""Wahlcodes: erzeugen, auflisten, löschen und als druckfertiges PDF ausgeben."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import settings as app_settings
from ..db import get_db
from ..models import Code
from ..pdfs import codes_pdf
from ..security import audit, require_staff, staff_form
from ..services import (election_status, generate_codes, participation)
from ..web import flash, render
from .common import (get_election, go, not_found)

router = APIRouter(prefix="/admin")


@router.get("/elections/{election_id}/codes")
def codes_page(request: Request, election_id: int, department_id: int | None = None, group_name: str = "",
               state: str = "all",
               db: Session = Depends(get_db), _=Depends(require_staff)):
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
                  qr_url=app_settings.public_url(request), is_admin=request.state.user.role == "admin")


@router.post("/elections/{election_id}/codes/delete-unused")
def delete_unused_codes(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
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
    audit(db, request, "codes_deleted", f"{e.name}: {n} offene Codes")
    db.commit()
    flash(request, f"{n} offene Codes gelöscht. Bereits verwendete Codes bleiben bestehen.")
    return go(f"/admin/elections/{e.id}/codes")


@router.post("/codes/{code_id}/delete")
def delete_code(request: Request, code_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
    code = db.get(Code, code_id) or not_found()
    e = get_election(db, code.election_id)
    if code.used:
        flash(request, "Ein bereits verwendeter Code kann nicht gelöscht werden.", "error")
    elif election_status(e) == "ended":
        flash(request, "Die Wahl ist beendet.", "error")
    else:
        db.delete(code)
        audit(db, request, "code_deleted", e.name)
        db.commit()
        flash(request, "Code gelöscht (er ist nun ungültig).")
    return go(f"/admin/elections/{e.id}/codes")


@router.post("/elections/{election_id}/codes")
def make_codes(request: Request, election_id: int, form=Depends(staff_form), db: Session = Depends(get_db)):
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
    audit(db, request, "codes_created", f"{e.name}: {count} für {dept.name}{' · ' + group if group else ''}")
    db.commit()
    flash(request, f"{count} Codes für {dept.name}{' · ' + group if group else ''} erzeugt. Das PDF wird geöffnet.")
    return go(f"/admin/elections/{e.id}/codes.pdf?batch={batch}")


@router.get("/elections/{election_id}/codes.pdf")
def codes_download(election_id: int, request: Request, batch: str = "", department_id: int | None = None,
                   group_name: str = "", unused: int = 1, db: Session = Depends(get_db),
                   _=Depends(require_staff)):
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
    base = app_settings.public_url(request)
    audit(db, request, "codes_pdf", e.name)
    db.commit()
    pdf = codes_pdf(e.name, [(c.department.name + (f" · {c.group_name}" if c.group_name else ""), c.code)
                             for c in codes], base)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="wahlcodes-{e.id}.pdf"',
                             "Cache-Control": "no-store"})
