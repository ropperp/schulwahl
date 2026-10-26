import re
from datetime import timedelta

from sqlalchemy import func, select

from app.models import Candidate, Code, Election
from app.timeutil import input_local, utcnow
from tests.conftest import admin_login, ballot_choices, csrf, decide_all, make_codes, vote_count


def test_admin_requires_login(client, election):
    for path in ["/admin", f"/admin/elections/{election.id}", f"/admin/elections/{election.id}/dashboard",
                 f"/admin/elections/{election.id}/codes.pdf"]:
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login", path
    assert client.post(f"/admin/elections/{election.id}/close").status_code == 303


def test_wrong_password_and_lockout(client):
    page = client.get("/admin/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    for _ in range(5):
        assert client.post("/admin/login", data={"username": "admin", "password": "falsch", "csrf_token": token}).status_code == 401
    r = client.post("/admin/login", data={"username": "admin", "password": "geheim-test", "csrf_token": token})
    assert r.status_code == 429  # auch das richtige Passwort wird nun gesperrt


def test_csrf_required(client, election):
    admin_login(client)
    r = client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": "falsch"})
    assert r.status_code == 403


def test_create_election_with_standard_races(client, db):
    token = admin_login(client)
    start = utcnow() + timedelta(days=1)
    r = client.post("/admin/elections", data={
        "csrf_token": token, "name": "Wahl 2026", "start_at": input_local(start),
        "end_at": input_local(start + timedelta(hours=8)), "standard_races": "1",
        "departments": "Elektrotechnik\nMechatronik\nMaschinenbau"})
    assert r.status_code == 303
    e = db.scalar(select(Election))
    assert [d.name for d in e.departments] == ["Elektrotechnik", "Maschinenbau", "Mechatronik"]
    assert len(e.races) == 4  # 1 Schulsprecher + 3 Abteilungen

    # Kandidat anlegen (vor Start erlaubt)
    race = e.races[0]
    r = client.post(f"/admin/races/{race.id}/candidates", data={"csrf_token": token, "name": "Anna", "info": "4AHET"})
    assert r.status_code == 303
    assert db.scalar(select(func.count()).select_from(Candidate)) == 1


def test_invalid_time_window_rejected(client):
    token = admin_login(client)
    now = utcnow()
    r = client.post("/admin/elections", data={
        "csrf_token": token, "name": "X", "start_at": input_local(now + timedelta(hours=2)),
        "end_at": input_local(now + timedelta(hours=1)), "departments": "A"})
    assert r.status_code == 400


def test_structure_locked_after_start(client, db, election):
    token = admin_login(client)
    race = election.races[0]
    client.post(f"/admin/races/{race.id}/candidates", data={"csrf_token": token, "name": "Neu"})
    assert db.scalar(select(func.count()).select_from(Candidate).where(Candidate.name == "Neu")) == 0


def test_codes_pdf_generation(client, db, election):
    token = admin_login(client)
    dept = election.departments[0]
    r = client.post(f"/admin/elections/{election.id}/codes",
                    data={"csrf_token": token, "department_id": dept.id, "count": 30, "base_url": "http://192.168.1.50:8080"})
    assert r.status_code == 303
    pdf = client.get(r.headers["location"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert db.scalar(select(func.count()).select_from(Code)) == 30
    codes = db.scalars(select(Code.code)).all()
    assert len(set(codes)) == 30 and all(len(c) == 6 and not set(c) & set("IO01") for c in codes)


def test_results_hidden_until_end_then_shown(client, db, election):
    token = admin_login(client)
    codes = make_codes(db, election, 0, 3)
    make_codes(db, election, 1, 1)
    # 3 Stimmen EL: Anna, Anna, ungültig(Schulsprecher); Abteilung: Clara, David, Clara
    plans = [(0, 0), (0, 1), ("invalid", 0)]
    for code, (s, d) in zip(codes, plans):
        assert client.post("/vote/submit", data=ballot_choices(election, db, code, s, d)).status_code == 200

    dash = client.get(f"/admin/elections/{election.id}/dashboard")
    assert "3</span> von 4" in dash.text and "Anna" not in dash.text  # Beteiligung ja, Ergebnis nein
    assert client.get(f"/admin/elections/{election.id}/results.csv").status_code == 303
    assert client.get(f"/ergebnis/{election.id}").status_code == 404

    # Admin beendet vorzeitig
    assert client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token}).status_code == 303
    dash = client.get(f"/admin/elections/{election.id}/dashboard")
    assert "Anna" in dash.text and "Gewählt" in dash.text and "Ungültig" in dash.text

    csv_ = client.get(f"/admin/elections/{election.id}/results.csv")
    text = csv_.content.decode("utf-8-sig")
    assert "Schulsprecher/in;alle;Anna;2" in text
    assert "Schulsprecher/in;alle;Ungültig;1" in text
    assert "Abteilungssprecher/in;Elektrotechnik;Clara;2" in text

    pdf = client.get(f"/admin/elections/{election.id}/protocol.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # Veröffentlichen
    assert client.get(f"/ergebnis/{election.id}").status_code == 404
    decide_all(client, db, election, token)
    client.post(f"/admin/elections/{election.id}/publish", data={"csrf_token": token})
    pub = client.get(f"/ergebnis/{election.id}")
    assert pub.status_code == 200 and "Anna" in pub.text

    # Nach dem Ende keine Stimmen mehr, auch kein neuer Code
    unused = db.scalars(select(Code.code).where(Code.used.is_(False))).first()
    assert client.post("/vote/start", data={"code": unused}).status_code == 403


def test_delete_election_removes_everything(client, db, election):
    token = admin_login(client)
    code = make_codes(db, election, 0, 1)[0]
    client.post("/vote/submit", data=ballot_choices(election, db, code))
    r = client.post(f"/admin/elections/{election.id}/delete", data={"csrf_token": token})
    assert r.status_code == 303
    from app.models import Vote
    assert db.scalar(select(func.count()).select_from(Election)) == 0
    assert db.scalar(select(func.count()).select_from(Code)) == 0
    assert vote_count(db) == 0


def test_security_headers_and_health(client):
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/health").json() == {"status": "ok"}
