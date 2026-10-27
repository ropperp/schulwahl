"""Protokoll-Logo (farbiges SVG) und das Wahlprotokoll im Papierform-Stil."""
from sqlalchemy import func, select

from app.models import Image
from tests.conftest import admin_login


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="#0088ad"/></svg>'


def test_protocol_logo_upload_preview_and_remove(client, db):
    token = admin_login(client)
    assert client.get("/admin/settings/protocol-logo").status_code == 404
    r = client.post("/admin/settings", data={"csrf_token": token, "school_name": "HTL Beispielstadt"},
                    files={"protocol_logo": ("logo.svg", SVG, "image/svg+xml")})
    assert r.status_code == 303
    settings_page = client.get("/admin/settings")
    assert "/admin/settings/protocol-logo?v=" in settings_page.text
    preview = client.get("/admin/settings/protocol-logo")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/svg+xml"
    assert preview.content == SVG

    client.post("/admin/settings", data={"csrf_token": token, "school_name": "HTL Beispielstadt", "remove_protocol_logo": "1"})
    assert client.get("/admin/settings/protocol-logo").status_code == 404
    assert db.scalar(select(func.count()).select_from(Image)) == 0


def test_protocol_logo_rejects_non_svg_and_xxe(client, db):
    token = admin_login(client)
    bad_payloads = [
        ("x.png", b"das ist kein bild"),
        ("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></notsvg>'),
        ("xxe.svg", b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                    b'<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'),
    ]
    for name, data in bad_payloads:
        r = client.post("/admin/settings", data={"csrf_token": token, "school_name": "S"},
                        files={"protocol_logo": (name, data, "image/svg+xml")})
        assert r.status_code == 303
        assert client.get("/admin/settings/protocol-logo").status_code == 404
    assert db.scalar(select(func.count()).select_from(Image)) == 0


def test_protocol_form_requires_ended_election(client, db, election):
    token = admin_login(client)
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    assert r.status_code == 303  # noch nicht beendet -> Weiterleitung zum Dashboard


def test_protocol_form_pdf_renders_with_and_without_logo(client, db, election):
    token = admin_login(client)
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})

    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")

    client.post("/admin/settings", data={"csrf_token": token, "school_name": "HTL Beispielstadt"},
               files={"protocol_logo": ("logo.svg", SVG, "image/svg+xml")})
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf?signers=3")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
