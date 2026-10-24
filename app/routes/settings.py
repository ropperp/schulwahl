"""Einstellungen (nur Administratoren): Schulname, Logo, Adresse im QR-Code."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import images
from .. import settings as app_settings
from ..db import get_db
from ..security import admin_form, audit, require_admin
from ..web import flash, render
from .common import go

router = APIRouter(prefix="/admin")


@router.get("/settings")
def settings_page(request: Request, _=Depends(require_admin)):
    return render(request, "admin_settings.html", cfg=app_settings.get(), tab=None, error=None,
                  qr_url=app_settings.public_url(request))


@router.post("/settings")
def settings_save(request: Request, form=Depends(admin_form), db: Session = Depends(get_db)):
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
