"""Protokoll-Logo (farbiges SVG) und das Wahlprotokoll im Papierform-Stil."""
import shutil
import subprocess
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.models import Candidate, Election, Image, Race, Vote
from app.timeutil import utcnow
from tests.conftest import admin_login


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="#0088ad"/></svg>'


def _pdf_text(pdf_bytes: bytes) -> str | None:
    """Text eines PDF per `pdftotext` lesen; None, wenn das Tool nicht installiert ist (Test überspringt dann)."""
    if not shutil.which("pdftotext"):
        return None
    return subprocess.run(["pdftotext", "-", "-"], input=pdf_bytes, capture_output=True, check=True).stdout.decode("utf-8", "replace")


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
    admin_login(client)
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    assert r.status_code == 303  # noch nicht beendet -> Weiterleitung zum Dashboard


def test_protocol_form_pdf_renders_with_and_without_logo(client, db, election):
    token = admin_login(client)
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})

    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")

    client.post("/admin/settings", data={"csrf_token": token, "school_name": "HTL Beispielstadt"},
               files={"protocol_logo": ("logo.svg", SVG, "image/svg+xml")})
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf?witnesses=Adnan+Afticosovic&witnesses=Erna+Barutsic")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")

    text = _pdf_text(r.content)
    if text is None:
        return
    assert "Adnan Afticosovic" in text and "Erna Barutsic" in text


def test_full_name_used_as_wahlleitung_on_protocol(client, db, election):
    token = admin_login(client)
    client.post("/admin/account/name", data={"csrf_token": token, "full_name": "Patrick Ropper"})
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    assert r.status_code == 200
    text = _pdf_text(r.content)
    if text is None:
        return
    assert "Wahlleitung: Patrick Ropper" in text
    assert "Wahlleitung: admin" not in text


def test_pending_runoff_not_shown_as_winner(client, db, election):
    token = admin_login(client)
    race = election.races[0]
    a, b = race.candidates
    for _ in range(40):
        db.add(Vote(id=uuid.uuid4().hex, race_id=race.id, candidate_id=a.id))
    for _ in range(38):
        db.add(Vote(id=uuid.uuid4().hex, race_id=race.id, candidate_id=b.id))
    db.commit()
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    client.post(f"/admin/races/{race.id}/runoff-decision", data={"csrf_token": token, "answer": "yes"})
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    text = _pdf_text(r.content)
    if text is None:
        return
    assert "Stichwahl erforderlich" in text
    assert f"Als {race.title} wurde gewählt:\n{a.name}" not in text  # kein Gewinner, solange die Stichwahl offen ist


def test_runoff_protocol_lists_only_the_two_runoff_candidates(client, db):
    """Stimmenzahlen zweier verschiedener Wahlen (Ursprungswahl/Stichwahl) sind nicht vergleichbar: kein automatischer
    2. Stellvertreter aus der Ursprungswahl. Die Stichwahl vermerkt nur die zwei Kandidat/innen, zwischen denen sie
    stattfindet; 3./4. Platz der Ursprungswahl stehen dort unter "Weitere Ergebnisse", nicht auf dem Stichwahl-Protokoll."""
    token = admin_login(client)
    e = Election(name="Schulsprecherwahl", start_at=utcnow() - timedelta(hours=2), end_at=utcnow() - timedelta(hours=1),
                closed_at=utcnow() - timedelta(hours=1))
    from app.models import Department
    e.departments = [Department(name="Elektrotechnik")]
    db.add(e)
    db.flush()
    race = Race(election_id=e.id, title="Schulsprecher/in", position=0)
    db.add(race)
    db.flush()
    cands = [Candidate(race_id=race.id, name=n) for n in ("Anna", "Ben", "Clara", "David")]
    db.add_all(cands)
    db.flush()
    anna, ben, clara, david = cands
    votes = {anna.id: 40, ben.id: 38, clara.id: 20, david.id: 5}
    for cid, n in votes.items():
        for _ in range(n):
            db.add(Vote(id=uuid.uuid4().hex, race_id=race.id, candidate_id=cid))
    db.commit()

    client.post(f"/admin/races/{race.id}/runoff-decision", data={"csrf_token": token, "answer": "yes"})
    r = client.post(f"/admin/elections/{e.id}/runoff", data={"csrf_token": token, "race_id": str(race.id)})
    assert r.status_code == 303
    runoff_id = int(r.headers["location"].rsplit("/", 1)[-1])
    db.expire_all()
    runoff_race = db.scalar(select(Race).where(Race.election_id == runoff_id))
    runoff_cands = {c.name: c for c in runoff_race.candidates}
    assert set(runoff_cands) == {"Anna", "Ben"}  # Clara/David (3./4. Platz) sind nicht in der Stichwahl

    runoff_election = db.get(Election, runoff_id)
    runoff_election.start_at = utcnow() - timedelta(hours=2)
    runoff_election.end_at = utcnow() - timedelta(hours=1)
    runoff_election.closed_at = utcnow() - timedelta(hours=1)
    db.add(Vote(id=uuid.uuid4().hex, race_id=runoff_race.id, candidate_id=runoff_cands["Anna"].id))
    for _ in range(600):
        db.add(Vote(id=uuid.uuid4().hex, race_id=runoff_race.id, candidate_id=runoff_cands["Ben"].id))
    db.commit()

    # Auf dem Ursprungsprotokoll sind Clara/David schon "Weitere Ergebnisse" (Stichwahl noch offen)
    r = client.get(f"/admin/elections/{e.id}/protocol-form.pdf")
    text = _pdf_text(r.content)
    if text is not None:
        assert "Weitere Ergebnisse" in text and "Clara" in text and "David" in text

    r = client.get(f"/admin/elections/{runoff_id}/protocol-form.pdf")
    assert r.status_code == 200
    text = _pdf_text(r.content)
    if text is None:
        return
    assert "2. Stellvertreter/in" in text
    # trotz nur 1 Stimme hätte "Clara" (ursprünglich 20 Stimmen) hier nichts verloren - keine Vermischung der Wahlen
    assert "Clara" not in text and "David" not in text
