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
    r = client.post("/admin/settings/protocol", data={"csrf_token": token},
                    files={"protocol_logo": ("logo.svg", SVG, "image/svg+xml")})
    assert r.status_code == 303
    settings_page = client.get("/admin/settings/protocol")
    assert "/admin/settings/protocol-logo?v=" in settings_page.text
    preview = client.get("/admin/settings/protocol-logo")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/svg+xml"
    assert preview.content == SVG

    client.post("/admin/settings/protocol", data={"csrf_token": token, "remove_protocol_logo": "1"})
    assert client.get("/admin/settings/protocol-logo").status_code == 404
    assert db.scalar(select(func.count()).select_from(Image)) == 0


def test_protocol_logo_rejects_non_svg_and_xxe(client, db):
    token = admin_login(client)
    bad_payloads = [
        ("x.png", b"das ist kein bild"),
        ("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></notsvg>'),
        ("xxe.svg", b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                    b'<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'),
        ("ssrf.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><image href="http://169.254.169.254/" width="1" height="1"/></svg>'),
        ("ssrf2.svg", b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
                      b'<image xlink:href="file:///etc/passwd" width="1" height="1"/></svg>'),
        # DOCTYPE/ENTITY in anderer Kodierung (UTF-16): ein reiner Byte-Treffer auf "<!DOCTYPE" würde das übersehen
        ("xxe-utf16.svg", '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                          '<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'.encode("utf-16")),
    ]
    for name, data in bad_payloads:
        r = client.post("/admin/settings/protocol", data={"csrf_token": token},
                        files={"protocol_logo": (name, data, "image/svg+xml")})
        assert r.status_code == 303
        assert client.get("/admin/settings/protocol-logo").status_code == 404
    assert db.scalar(select(func.count()).select_from(Image)) == 0


def test_protocol_location_and_fonts_persist_and_apply(client, db, election):
    token = admin_login(client)
    r = client.post("/admin/settings/protocol", data={
        "csrf_token": token, "protocol_location": "Klagenfurt", "protocol_font_header": "20",
        "protocol_font_title": "18", "protocol_font_table": "14", "protocol_font_caption": "11"})
    assert r.status_code == 303
    page = client.get("/admin/settings/protocol")
    assert 'value="Klagenfurt"' in page.text and 'value="20.0"' in page.text

    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    text = _pdf_text(r.content)
    if text is not None:
        assert "Klagenfurt," in text


def test_protocol_preview_renders_without_saving(client, db):
    token = admin_login(client)
    r = client.post("/admin/settings/protocol/preview",
                    data={"csrf_token": token, "protocol_location": "Testort", "protocol_font_table": "99999"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    # nichts wurde gespeichert (ungültige Schriftgröße hätte sonst auch einen Fehler auslösen müssen)
    page = client.get("/admin/settings/protocol")
    assert 'value="Testort"' not in page.text


def test_protocol_form_requires_ended_election(client, db, election):
    admin_login(client)
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    assert r.status_code == 303  # noch nicht beendet -> Weiterleitung zum Dashboard


def test_protocol_form_pdf_renders_with_and_without_logo(client, db, election):
    token = admin_login(client)
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})

    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")

    client.post("/admin/settings/protocol", data={"csrf_token": token},
               files={"protocol_logo": ("logo.svg", SVG, "image/svg+xml")})
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf?witnesses=Adnan+Afticosovic&witnesses=Erna+Barutsic")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")

    text = _pdf_text(r.content)
    if text is None:
        return
    assert "Adnan Afticosovic" in text and "Erna Barutsic" in text


def test_protocol_title_wording_and_single_race_filter(client, db, election):
    token = admin_login(client)
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    school_race = next(r for r in election.races if r.department_id is None)
    dept_race = next(r for r in election.races if r.department_id is not None)

    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf")
    text = _pdf_text(r.content)
    if text is not None:
        assert "Ergebnis der Schulsprecherwahl" in text and "SchulsprecherIn" in text
        assert "Ergebnis der Abteilungssprecherwahl" in text

    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf?race_id={school_race.id}")
    assert r.status_code == 200
    text = _pdf_text(r.content)
    if text is not None:
        assert "Ergebnis der Schulsprecherwahl" in text
        assert "Ergebnis der Abteilungssprecherwahl" not in text

    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf?race_id=999999")
    assert r.status_code == 404


def test_placements_follow_the_actual_number_of_candidates(client, db):
    """So viele Platzierungen wie Kandidat/innen mit Stimmen da sind - nichts wird durchgestrichen oder leer gezeigt."""
    admin_login(client)
    from app.models import Department

    def race_with(n_candidates, name_suffix):
        e = Election(name=f"Wahl {name_suffix}", start_at=utcnow() - timedelta(hours=2),
                     end_at=utcnow() - timedelta(hours=1), closed_at=utcnow() - timedelta(hours=1))
        e.departments = [Department(name="Elektrotechnik")]
        db.add(e)
        db.flush()
        race = Race(election_id=e.id, title="Schulsprecher/in", position=0)
        db.add(race)
        db.flush()
        names = ["Anna", "Ben", "Clara", "David"][:n_candidates]
        cands = [Candidate(race_id=race.id, name=n) for n in names]
        db.add_all(cands)
        db.flush()
        for i, c in enumerate(cands):
            for _ in range(n_candidates - i):  # absteigende, eindeutige Stimmenzahl je Platz
                db.add(Vote(id=uuid.uuid4().hex, race_id=race.id, candidate_id=c.id))
        db.commit()
        return e

    # 2 Kandidat/innen: nur "Gewählt" + "1. Stellvertreter/in"
    e2 = race_with(2, "zwei")
    r = client.get(f"/admin/elections/{e2.id}/protocol-form.pdf")
    text = _pdf_text(r.content)
    if text is not None:
        assert "1. Stellvertreter/in" in text
        assert "2. Stellvertreter/in" not in text and "Weitere Ergebnisse" not in text

    # 3 Kandidat/innen: zusätzlich "2. Stellvertreter/in", aber keine "Weitere Ergebnisse"
    e3 = race_with(3, "drei")
    r = client.get(f"/admin/elections/{e3.id}/protocol-form.pdf")
    text = _pdf_text(r.content)
    if text is not None:
        assert "2. Stellvertreter/in" in text and "Clara" in text
        assert "Weitere Ergebnisse" not in text

    # 4 Kandidat/innen: die 4. geht unter "Weitere Ergebnisse"
    e4 = race_with(4, "vier")
    r = client.get(f"/admin/elections/{e4.id}/protocol-form.pdf")
    text = _pdf_text(r.content)
    if text is not None:
        assert "Weitere Ergebnisse" in text and "David" in text.split("Weitere Ergebnisse")[1]


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
    assert "1. Stellvertreter/in" in text
    # nur 2 Antretende in der Stichwahl: keine "2. Stellvertreter/in"-Zeile (nichts durchgestrichen, einfach weggelassen)
    assert "2. Stellvertreter/in" not in text
    # trotz nur 1 Stimme hätte "Clara" (ursprünglich 20 Stimmen) hier nichts verloren - keine Vermischung der Wahlen
    assert "Clara" not in text and "David" not in text


def test_protocol_form_accepts_empty_race_id(client, db, election):
    """Das Dashboard-Formular schickt bei "einzeln" kein race_id mit; eine leere race_id darf nicht 422 auslösen."""
    token = admin_login(client)
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf?race_id=")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf?race_id=abc")
    assert r.status_code == 400


def test_protocol_form_compact_layout_has_one_shared_signature_block(client, db, election):
    token = admin_login(client)
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    r = client.get(f"/admin/elections/{election.id}/protocol-form.pdf?layout=gesammelt&witnesses=Max+Beispiel")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    text = _pdf_text(r.content)
    if text is None:
        return
    assert text.count("Wahlleitung:") == 1  # nur EIN gemeinsamer Unterschriftenblock, nicht je Wahlgang
    assert "Ergebnis der Schulsprecherwahl" in text and "Ergebnis der Abteilungssprecherwahl" in text
