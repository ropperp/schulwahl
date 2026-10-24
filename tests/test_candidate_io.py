"""Export und Import von Kandidat/innen."""
import base64
import io
import json
from datetime import timedelta

import pytest
from PIL import Image as PILImage
from sqlalchemy import func, select

from app import candidate_io
from app.models import Candidate, Department, Election, Image, Race
from app.timeutil import input_local, utcnow
from tests.conftest import admin_login, make_user


def jpeg(color="red"):
    buf = io.BytesIO()
    PILImage.new("RGB", (300, 200), color).save(buf, "JPEG")
    return buf.getvalue()


def planned_election(db, name="Vorbereitet"):
    e = Election(name=name, start_at=utcnow() + timedelta(days=1), end_at=utcnow() + timedelta(days=2))
    e.departments = [Department(name="Elektrotechnik"), Department(name="Maschinenbau")]
    db.add(e)
    db.commit()
    return e


def upload(client, token, election, payload, name="k.json"):
    return client.post(f"/admin/elections/{election.id}/candidates/import", data={"csrf_token": token},
                       files={"import_file": (name, payload, "application/json")})


def doc(races):
    return json.dumps({"format": "schulwahl-kandidaten", "version": 1, "races": races}).encode()


def test_export_import_roundtrip_with_photos(client, db, election):
    cand = db.scalar(select(Candidate).where(Candidate.name == "Anna"))
    img = Image(data=jpeg(), content_type="image/jpeg")
    db.add(img)
    db.flush()
    cand.image_id, cand.info = img.id, "5AHET"
    db.commit()

    token = admin_login(client)
    r = client.get(f"/admin/elections/{election.id}/candidates.json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    exported = json.loads(r.content)
    assert exported["format"] == "schulwahl-kandidaten"
    titles = [(x["title"], x["department"], [c["name"] for c in x["candidates"]]) for x in exported["races"]]
    assert ("Schulsprecher/in", None, ["Anna", "Ben"]) in titles
    assert ("Abteilungssprecher/in", "Elektrotechnik", ["Clara", "David"]) in titles
    assert "codes" not in r.text.lower() and "votes" not in r.text.lower()

    new = planned_election(db)
    assert upload(client, token, new, r.content).status_code == 303
    races = db.scalars(select(Race).where(Race.election_id == new.id).order_by(Race.position)).all()
    assert [x.title for x in races] == ["Schulsprecher/in", "Abteilungssprecher/in", "Abteilungssprecher/in"]
    anna = db.scalar(select(Candidate).join(Race).where(Race.election_id == new.id, Candidate.name == "Anna"))
    assert anna.info == "5AHET" and anna.image_id is not None
    assert db.get(Image, anna.image_id).content_type == "image/jpeg"
    # Foto ist eine eigene Kopie
    assert anna.image_id != cand.image_id
    # Abteilungen zugeordnet
    ma = [x for x in races if x.department_id][1]
    assert db.get(Department, ma.department_id).name == "Maschinenbau"


def test_import_twice_skips_duplicates(client, db):
    token = admin_login(client)
    e = planned_election(db)
    payload = doc([{"title": "Schulsprecher/in", "department": None, "candidates": [{"name": "Anna"}, {"name": "Ben"}]}])
    upload(client, token, e, payload)
    upload(client, token, e, doc([{"title": "schulsprecher/in", "department": None,
                                   "candidates": [{"name": "ANNA"}, {"name": "Carl"}]}]))
    names = sorted(c.name for c in db.scalars(select(Candidate).join(Race).where(Race.election_id == e.id)))
    assert names == ["Anna", "Ben", "Carl"]
    assert db.scalar(select(func.count()).select_from(Race).where(Race.election_id == e.id)) == 1


def test_import_creates_missing_departments(client, db):
    token = admin_login(client)
    e = planned_election(db)
    upload(client, token, e, doc([{"title": "Abteilungssprecher/in", "department": "Mechatronik", "candidates": [{"name": "Eva"}]}]))
    db.expire_all()
    assert "Mechatronik" in [d.name for d in db.get(Election, e.id).departments]


def test_import_only_before_start(client, db, election):
    token = admin_login(client)   # election läuft bereits
    r = upload(client, token, election, doc([{"title": "X", "department": None, "candidates": [{"name": "Neu"}]}]))
    assert r.status_code == 303
    assert db.scalar(select(Candidate).where(Candidate.name == "Neu")) is None
    assert client.get(f"/admin/elections/{election.id}/candidates.json").status_code == 200  # Export geht immer
    assert "Importieren" not in client.get(f"/admin/elections/{election.id}/candidates").text


@pytest.mark.parametrize("payload", [
    b"", b"kein json", b"[]", b"{}", b'{"format": "anderes", "version": 1, "races": []}',
    b'{"format": "schulwahl-kandidaten", "version": 99, "races": [{"title": "x"}]}',
    b'{"format": "schulwahl-kandidaten", "version": 1, "races": []}',
    b'{"format": "schulwahl-kandidaten", "version": 1, "races": [1]}',
    b'{"format": "schulwahl-kandidaten", "version": 1, "races": [{"title": 5, "candidates": []}]}',
    b'{"format": "schulwahl-kandidaten", "version": 1, "races": [{"title": "T", "candidates": [{"name": ""}]}]}',
    b'{"format": "schulwahl-kandidaten", "version": 1, "races": [{"title": "T", "candidates": "x"}]}',
    b"\xff\xfe\x00binaer", b"[" * 100000,
])
def test_broken_files_are_rejected_without_changes(client, db, payload):
    token = admin_login(client)
    e = planned_election(db)
    r = upload(client, token, e, payload)
    assert r.status_code == 303   # Fehler als Hinweis, kein Absturz
    assert db.scalar(select(func.count()).select_from(Race).where(Race.election_id == e.id)) == 0
    assert db.scalar(select(func.count()).select_from(Candidate)) == 0


def test_limits(client, db):
    token = admin_login(client)
    e = planned_election(db)
    many = doc([{"title": f"R{i}", "department": None, "candidates": [{"name": "A"}]} for i in range(51)])
    upload(client, token, e, many)
    cands = doc([{"title": "R", "department": None, "candidates": [{"name": f"K{i}"} for i in range(501)]}])
    upload(client, token, e, cands)
    assert db.scalar(select(func.count()).select_from(Race).where(Race.election_id == e.id)) == 0
    # zu große Datei wird schon von der Anfragegrenze abgewiesen
    big = upload(client, token, e, b"x" * (9 * 1024 * 1024))
    assert big.status_code == 413


def test_hostile_values_are_sanitised(client, db):
    token = admin_login(client)
    e = planned_election(db)
    upload(client, token, e, doc([{"title": "T\x00itel", "department": None,
                                   "candidates": [{"name": "<script>alert(1)</script>\x00x", "info": "i" * 1000}]}]))
    cand = db.scalar(select(Candidate))
    assert "\x00" not in cand.name and len(cand.info) == 200
    page = client.get(f"/admin/elections/{e.id}/candidates").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page


def test_invalid_photos_are_dropped_not_trusted(client, db):
    token = admin_login(client)
    e = planned_election(db)
    svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>').decode()
    good = base64.b64encode(jpeg()).decode()
    upload(client, token, e, doc([{"title": "T", "department": None, "candidates": [
        {"name": "Mit SVG", "photo": svg}, {"name": "Kaputt", "photo": "!!!nicht-base64!!!"},
        {"name": "Zahl", "photo": 5}, {"name": "Gut", "photo": good}]}]))
    cands = {c.name: c for c in db.scalars(select(Candidate))}
    assert set(cands) == {"Mit SVG", "Kaputt", "Zahl", "Gut"}
    assert cands["Mit SVG"].image_id is None and cands["Kaputt"].image_id is None and cands["Zahl"].image_id is None
    stored = db.get(Image, cands["Gut"].image_id)
    assert PILImage.open(io.BytesIO(stored.data)).size == (240, 240)   # neu kodiert, nicht Original


def test_import_while_creating_a_new_election(client, db, election):
    token = admin_login(client)
    export = client.get(f"/admin/elections/{election.id}/candidates.json").content
    start = utcnow() + timedelta(days=3)
    r = client.post("/admin/elections", data={
        "csrf_token": token, "name": "Wahl 2027", "start_at": input_local(start), "end_at": input_local(start + timedelta(hours=8)),
        "standard_races": "1", "departments": "Elektrotechnik\nMaschinenbau"},
        files={"import_file": ("k.json", export, "application/json")})
    assert r.status_code == 303
    new = db.scalar(select(Election).where(Election.name == "Wahl 2027"))
    # Standard-Wahlgänge wurden ergänzt statt verdoppelt
    races = db.scalars(select(Race).where(Race.election_id == new.id)).all()
    assert len(races) == 3
    names = sorted(c.name for c in db.scalars(select(Candidate).join(Race).where(Race.election_id == new.id)))
    assert names == ["Anna", "Ben", "Clara", "David", "Eva"]


def test_bad_file_when_creating_election_creates_nothing(client, db):
    token = admin_login(client)
    start = utcnow() + timedelta(days=3)
    r = client.post("/admin/elections", data={
        "csrf_token": token, "name": "Wahl X", "start_at": input_local(start), "end_at": input_local(start + timedelta(hours=8)),
        "departments": "A"}, files={"import_file": ("k.json", b"kein json", "application/json")})
    assert r.status_code == 400 and "keine gültige Export-Datei" in r.text
    assert db.scalar(select(Election).where(Election.name == "Wahl X")) is None


def test_wahlleiter_can_export_and_import(client, db):
    make_user(db)
    token = admin_login(client, "leiter", "wahlleiter-passwort")
    e = planned_election(db)
    assert upload(client, token, e, doc([{"title": "T", "department": None, "candidates": [{"name": "A"}]}])).status_code == 303
    assert client.get(f"/admin/elections/{e.id}/candidates.json").status_code == 200
