"""Einstellungen (nur Administratoren): Schulname, Logo, Adresse im QR-Code, Protokoll-Gestaltung."""
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import images
from .. import settings as app_settings
from ..db import get_db
from ..models import Image
from ..pdfs import DEFAULT_FONT_SIZES, official_protocol_pdf
from ..security import admin_form, audit, require_admin
from ..timeutil import utcnow
from ..web import flash, render
from .common import go

router = APIRouter(prefix="/admin")


@router.get("/settings")
def settings_page(request: Request, _=Depends(require_admin)):
    """Einstellungen: Schulname, Logo, Adresse im QR-Code (nur Administrator)."""
    return render(request, "admin_settings.html", cfg=app_settings.get(), tab=None, error=None,
                  qr_url=app_settings.public_url(request))


@router.post("/settings")
def settings_save(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Speichert die Einstellungen (Adresse und Logo werden streng geprüft) und protokolliert die Änderung."""
    name = str(form.get("school_name", "")).strip()[:100]
    if not name:
        flash(request, "Bitte einen Schulnamen angeben.", "error")
        return go("/admin/settings")
    url, problem = None, None
    if form.get("public_url") is not None:
        url, problem = app_settings.clean_public_url(str(form.get("public_url")))
    if problem:
        flash(request, problem, "error")
        return go("/admin/settings")
    logo = None
    try:
        raw = images.read_upload(form.get("logo"))
        logo = images.process(raw, "logo") if raw else None
    except images.ImageError as err:
        flash(request, str(err), "error")
        return go("/admin/settings")
    changed_url = url is not None and url != app_settings.get()["public_url"]
    app_settings.save(db, name, logo, remove_logo=bool(form.get("remove_logo")), public_url=url)
    audit(db, request, "settings_changed", f"QR-Adresse: {url}" if changed_url else "")
    db.commit()
    flash(request, "Einstellungen gespeichert." + (
        " Bereits gedruckte Zettel behalten die alte Adresse im QR-Code." if changed_url else ""))
    return go("/admin/settings")


@router.get("/settings/protocol-logo")
def protocol_logo_preview(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Vorschau des farbigen Protokoll-Logos auf der Einstellungsseite (nur Administrator)."""
    cfg = app_settings.get()
    img = db.get(Image, cfg["protocol_logo_id"]) if cfg["protocol_logo_id"] else None
    if img is None:
        raise HTTPException(404, "Kein Logo")
    return Response(img.data, media_type=img.content_type, headers={"Cache-Control": "no-store"})


@router.get("/settings/protocol")
def protocol_settings_page(request: Request, _=Depends(require_admin)):
    """Reiter „Protokollbearbeitung“: Protokoll-Logo, Ort und Schriftgrößen des Papierform-Protokolls."""
    return render(request, "admin_settings_protocol.html", cfg=app_settings.get())


def _font_from_form(form, key: str) -> float:
    """Liest eine Schriftgröße aus dem Formular; bei ungültiger/fehlender Eingabe der Vorgabewert, sonst 6-40 pt begrenzt."""
    raw = str(form.get(f"protocol_font_{key}", "")).strip()
    try:
        n = float(raw)
    except ValueError:
        return DEFAULT_FONT_SIZES[key]
    return max(6.0, min(40.0, n))


@router.post("/settings/protocol")
def protocol_settings_save(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Speichert Protokoll-Logo, Ort und Schriftgrößen für das Papierform-Protokoll."""
    protocol_logo = None
    try:
        raw_svg = images.read_svg_upload(form.get("protocol_logo"))
        protocol_logo = (images.process_svg(raw_svg), "image/svg+xml") if raw_svg else None
    except images.ImageError as err:
        flash(request, str(err), "error")
        return go("/admin/settings/protocol")
    location = str(form.get("protocol_location", "")).strip()[:100]
    fonts = {key: _font_from_form(form, key) for key in DEFAULT_FONT_SIZES}
    app_settings.save(db, app_settings.get()["school_name"], protocol_logo=protocol_logo,
                      remove_protocol_logo=bool(form.get("remove_protocol_logo")), protocol_location=location,
                      protocol_fonts=fonts)
    audit(db, request, "settings_changed", "Protokoll-Gestaltung")
    db.commit()
    flash(request, "Protokoll-Einstellungen gespeichert.")
    return go("/admin/settings/protocol")


def _preview_results() -> list[dict]:
    """Zwei Beispiel-Wahlgänge (Schulsprecher/in, Abteilungssprecher/in) mit Testwerten für die Protokoll-Vorschau."""
    def cand(cid, name, info, votes):
        """Baut eine Beispiel-Kandidat/in mit Stimmenzahl (candidate, votes)."""
        return SimpleNamespace(id=cid, name=name, info=info), votes

    school = [cand(1, "Anna Musterfrau", "5AHET", 70), cand(2, "Ben Beispiel", "4BHMES", 55),
             cand(3, "Clara Testweis", "4BHMES", 20)]
    dept = [cand(4, "David Probe", "4AHET", 40), cand(5, "Erna Muster", "5AHET", 18)]
    race_school = SimpleNamespace(title="Schulsprecher/in", department_id=None, runoff_kind="")
    race_dept = SimpleNamespace(title="Abteilungssprecher/in", department_id=1, runoff_kind="")

    def build(race, cands, department, invalid):
        """Baut ein Ergebnis-Dict für `official_protocol_pdf`, wie `compute_results` es sonst liefert."""
        rows = [{"candidate": c, "votes": v} for c, v in cands]
        rows.sort(key=lambda r: (-r["votes"], r["candidate"].name.lower()))
        winner = rows[0]["candidate"] if rows else None
        deputy = rows[1]["candidate"] if len(rows) > 1 else None
        return {"race": race, "department": department, "rows": rows, "winner": winner, "deputy": deputy,
               "decision": "", "plan": [], "plan_kind": "speaker", "invalid": invalid}

    return [build(race_school, school, None, 4), build(race_dept, dept, "Elektrotechnik", 2)]


@router.post("/settings/protocol/preview")
def protocol_settings_preview(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
    """Protokoll-Vorschau mit Testdaten anhand der (noch nicht gespeicherten) Formularwerte."""
    logo = None
    upload = form.get("protocol_logo")
    if upload is not None and getattr(upload, "filename", ""):
        try:
            raw_svg = images.read_svg_upload(upload)
            logo = images.process_svg(raw_svg) if raw_svg else None
        except images.ImageError as err:
            flash(request, str(err), "error")
            return go("/admin/settings/protocol")
    cfg = app_settings.get()
    if logo is None and cfg["protocol_logo_id"]:
        img = db.get(Image, cfg["protocol_logo_id"])
        logo = img.data if img else None
    location = str(form.get("protocol_location", "")).strip()[:100]
    fonts = {key: _font_from_form(form, key) for key in DEFAULT_FONT_SIZES}
    election = SimpleNamespace(name="Vorschau", closed_at=utcnow(), end_at=utcnow())
    pdf = official_protocol_pdf(election, _preview_results(), cfg["school_name"], logo, "Mia Mustermann",
                                witnesses=["Max Beispiel", "Lena Beispiel"], location=location, font_sizes=fonts)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="protokoll-vorschau.pdf"'})
