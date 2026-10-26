import io
from datetime import timedelta

from PIL import Image as PILImage
from sqlalchemy import func, select, text

from app.models import Candidate, Code, Image, Race
from app.timeutil import utcnow
from tests.conftest import admin_login, make_codes


def png(size=(300, 120), color=(200, 30, 30, 255)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def jpg(size=(800, 500)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, (20, 90, 200)).save(buf, "JPEG")
    return buf.getvalue()


def planned(election, db):
    election.start_at = utcnow() + timedelta(hours=1)
    election.end_at = utcnow() + timedelta(hours=5)
    db.commit()


def test_settings_name_and_logo(client, db):
    token = admin_login(client)
    assert client.get("/media/logo").status_code == 404
    r = client.post("/admin/settings", data={"csrf_token": token, "school_name": "HTL Beispielstadt"},
                    files={"logo": ("logo.png", png(), "image/png")})
    assert r.status_code == 303
    home = client.get("/")
    assert "HTL Beispielstadt" in home.text and "/media/logo?v=" in home.text
    logo = client.get("/media/logo")
    assert logo.status_code == 200 and logo.headers["content-type"] == "image/png"
    assert PILImage.open(io.BytesIO(logo.content)).size == (300, 120)

    # Entfernen
    client.post("/admin/settings", data={"csrf_token": token, "school_name": "HTL Beispielstadt", "remove_logo": "1"})
    assert client.get("/media/logo").status_code == 404
    assert db.scalar(select(func.count()).select_from(Image)) == 0


def test_rejects_non_images(client, db):
    token = admin_login(client)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    for name, data in [("x.svg", svg), ("x.png", b"das ist kein bild")]:
        client.post("/admin/settings", data={"csrf_token": token, "school_name": "S"}, files={"logo": (name, data, "image/png")})
        assert client.get("/media/logo").status_code == 404
    assert db.scalar(select(func.count()).select_from(Image)) == 0


def test_candidate_with_photo_edit_delete(client, db, election):
    planned(election, db)
    token = admin_login(client)
    race = election.races[0]
    r = client.post(f"/admin/races/{race.id}/candidates", data={"csrf_token": token, "name": "Neu", "info": "5BHET"},
                    files={"photo": ("p.jpg", jpg(), "image/jpeg")})
    assert r.status_code == 303 and r.headers["location"].endswith("/candidates")
    cand = db.scalar(select(Candidate).where(Candidate.name == "Neu"))
    assert cand.image_id
    photo = client.get(f"/media/candidate/{cand.id}")
    assert photo.status_code == 200 and PILImage.open(io.BytesIO(photo.content)).size == (240, 240)

    # Seite zeigt Foto, Stimmzettel ebenfalls
    assert f"/media/candidate/{cand.id}" in client.get(f"/admin/elections/{election.id}/candidates").text

    # Bearbeiten: Name ändern, Foto ersetzen -> altes Bild weg
    old = cand.image_id
    client.post(f"/admin/candidates/{cand.id}/edit", data={"csrf_token": token, "name": "Neu2", "info": "6A"},
                files={"photo": ("q.png", png((50, 80)), "image/png")})
    db.expire_all()
    cand = db.get(Candidate, cand.id)
    assert cand.name == "Neu2" and cand.image_id != old and db.get(Image, old) is None

    # Foto entfernen
    client.post(f"/admin/candidates/{cand.id}/edit", data={"csrf_token": token, "name": "Neu2", "remove_photo": "1"})
    db.expire_all()
    assert db.get(Candidate, cand.id).image_id is None
    assert db.scalar(select(func.count()).select_from(Image)) == 0

    # Ungültiges Foto: Kandidat wird nicht angelegt
    client.post(f"/admin/races/{race.id}/candidates", data={"csrf_token": token, "name": "Kaputt"},
                files={"photo": ("x.jpg", b"kein bild", "image/jpeg")})
    assert db.scalar(select(func.count()).select_from(Candidate).where(Candidate.name == "Kaputt")) == 0

    # Löschen mit Foto
    client.post(f"/admin/races/{race.id}/candidates", data={"csrf_token": token, "name": "Weg"},
                files={"photo": ("p.jpg", jpg(), "image/jpeg")})
    weg = db.scalar(select(Candidate).where(Candidate.name == "Weg"))
    client.post(f"/admin/candidates/{weg.id}/delete", data={"csrf_token": token})
    assert db.scalar(select(func.count()).select_from(Image)) == 0


def test_candidates_locked_after_start(client, db, election):
    token = admin_login(client)  # Wahl läuft
    cand = election.races[0].candidates[0]
    client.post(f"/admin/candidates/{cand.id}/edit", data={"csrf_token": token, "name": "Geändert"})
    db.expire_all()
    assert db.get(Candidate, cand.id).name == "Anna"
    assert client.get(f"/admin/elections/{election.id}/candidates").status_code == 200


def test_race_rename_move(client, db, election):
    planned(election, db)
    token = admin_login(client)
    ids = [r.id for r in election.races]
    client.post(f"/admin/races/{ids[0]}/rename", data={"csrf_token": token, "title": "Schulsprecher"})
    client.post(f"/admin/races/{ids[0]}/move", data={"csrf_token": token, "dir": "down"})
    order = [r.id for r in db.scalars(select(Race).order_by(Race.position, Race.id))]
    assert order == [ids[1], ids[0], ids[2]]
    db.expire_all()
    assert db.get(Race, ids[0]).title == "Schulsprecher"


def test_delete_election_removes_images(client, db, election):
    planned(election, db)
    token = admin_login(client)
    client.post(f"/admin/races/{election.races[0].id}/candidates", data={"csrf_token": token, "name": "Foto"},
                files={"photo": ("p.jpg", jpg(), "image/jpeg")})
    assert db.scalar(select(func.count()).select_from(Image)) == 1
    client.post(f"/admin/elections/{election.id}/delete", data={"csrf_token": token})
    assert db.scalar(select(func.count()).select_from(Image)) == 0


def test_codes_page_and_delete(client, db, election):
    token = admin_login(client)
    codes = make_codes(db, election, 0, 3)
    page = client.get(f"/admin/elections/{election.id}/codes")
    assert page.status_code == 200 and codes[0][:3] in page.text
    assert client.get(f"/admin/elections/{election.id}/codes?state=used").status_code == 200
    c = db.scalar(select(Code).where(Code.code == codes[0]))
    client.post(f"/admin/codes/{c.id}/delete", data={"csrf_token": token})
    assert db.scalar(select(Code).where(Code.code == codes[0])) is None
    assert client.post("/vote/start", data={"code": codes[0]}).status_code == 400
    # verwendeter Code bleibt
    from tests.conftest import ballot_choices
    client.post("/vote/submit", data=ballot_choices(election, db, codes[1]))
    used = db.scalar(select(Code).where(Code.code == codes[1]))
    client.post(f"/admin/codes/{used.id}/delete", data={"csrf_token": token})
    assert db.scalar(select(Code).where(Code.code == codes[1])) is not None


def test_protocol_with_logo_and_overview_pages(client, db, election):
    token = admin_login(client)
    client.post("/admin/settings", data={"csrf_token": token, "school_name": "HTL Test"},
                files={"logo": ("l.png", png(), "image/png")})
    for path in ["", "/candidates", "/codes", "/dashboard"]:
        assert client.get(f"/admin/elections/{election.id}{path}").status_code == 200, path
    assert client.get("/admin/settings").status_code == 200
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    pdf = client.get(f"/admin/elections/{election.id}/protocol.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_migration_adds_image_column(db):
    import pytest
    from app import db as dbmod
    if dbmod.engine.dialect.name != "sqlite":
        pytest.skip("nur SQLite; PostgreSQL wird manuell geprüft")
    from app.models import Base
    with dbmod.engine.begin() as conn:
        conn.execute(text("DROP TABLE candidates"))
        conn.execute(text("CREATE TABLE candidates (id INTEGER PRIMARY KEY, race_id INTEGER, name VARCHAR(200), info VARCHAR(200))"))
    dbmod.init_db()
    cols = [r[1] for r in db.execute(text("PRAGMA table_info(candidates)"))] if dbmod.engine.dialect.name == "sqlite" else ["image_id"]
    assert "image_id" in cols
