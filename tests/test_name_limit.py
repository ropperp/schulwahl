"""Begrenzung des Wahlnamens (steht auf den Code-Zetteln) und Schriftgrößen auf den Zetteln."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Election
from app.pdfs import _fit_size, codes_pdf
from app.services import ELECTION_NAME_MAX, name_problem, short_name
from app.timeutil import input_local, utcnow
from tests.conftest import admin_login
from tests.test_runoff_decision import decide, ended_election


def create(client, token, name):
    start = utcnow() + timedelta(days=1)
    return client.post("/admin/elections", data={"csrf_token": token, "name": name, "start_at": input_local(start),
                                                 "end_at": input_local(start + timedelta(hours=8)), "departments": "A"})


def test_limit_value_and_messages():
    assert ELECTION_NAME_MAX == 40
    assert name_problem("x" * 40) is None
    assert "41 Zeichen" in name_problem("x" * 41)


def test_create_election_enforces_limit(client, db):
    token = admin_login(client)
    assert create(client, token, "x" * 41).status_code == 400
    assert db.scalar(select(Election).where(Election.name == "x" * 41)) is None
    r = create(client, token, "Schul- und Abteilungssprecherwahl 2026")  # 38 Zeichen
    assert r.status_code == 303


def test_rename_enforces_limit(client, db, election):
    token = admin_login(client)
    start, end = input_local(utcnow() + timedelta(hours=1)), input_local(utcnow() + timedelta(hours=9))
    client.post(f"/admin/elections/{election.id}/settings", data={"csrf_token": token, "name": "y" * 50, "start_at": start, "end_at": end})
    db.refresh(election)
    assert election.name == "Testwahl"
    client.post(f"/admin/elections/{election.id}/settings", data={"csrf_token": token, "name": "Neuer Name", "start_at": start, "end_at": end})
    db.refresh(election)
    assert election.name == "Neuer Name"


def test_form_has_maxlength(client, election):
    admin_login(client)
    assert 'name="name" placeholder="Schulsprecherwahl 2026" maxlength="40"' in client.get("/admin/elections/new").text
    assert 'maxlength="40"' in client.get(f"/admin/elections/{election.id}").text


def test_short_name_helper():
    assert short_name("Stichwahl Schulsprecher/in", "(Schulsprecherwahl 2026/27)") == "Stichwahl Schulsprecher/in"
    assert short_name("Stichwahl", "(Wahl 26)") == "Stichwahl (Wahl 26)"
    assert len(short_name("A" * 60, "(x)")) <= 40


def test_runoff_names_respect_limit(client, db):
    e, race = ended_election(db, {"A": 40, "B": 35, "C": 25}, name="Schulsprecherwahl 2026/27 der HTL")
    token = admin_login(client)
    decide(client, token, race, "yes")
    client.post(f"/admin/elections/{e.id}/runoff", data={"csrf_token": token, "race_id": race.id})
    new = db.scalars(select(Election).where(Election.id != e.id)).one()
    assert len(new.name) <= ELECTION_NAME_MAX and new.name.startswith("Stichwahl Schulsprecher/in")


def test_title_shrinks_before_it_is_cut():
    text, size = _fit_size("Schul- und Abteilungssprecherwahl 2026", "Helvetica", 7, 160, 5.5)
    assert text == "Schul- und Abteilungssprecherwahl 2026" and 5.5 <= size <= 7   # kein "…"
    text, size = _fit_size("Z" * 90, "Helvetica", 7, 160, 5.5)
    assert text.endswith("…") and size == 5.5


def test_department_is_larger_on_slip_but_still_fits():
    text, size = _fit_size("Elektrotechnik", "Helvetica-Bold", 11.5, 84, 7)
    assert text == "Elektrotechnik" and size > 9          # größer als bisher (9 pt)
    text, size = _fit_size("Land- & Umwelttechnik", "Helvetica-Bold", 11.5, 84, 7)
    assert size >= 7                                       # lange Namen schrumpfen statt zu überlappen


def test_codes_pdf_with_long_values_renders():
    pdf = codes_pdf("Schul- und Abteilungssprecherwahl 2026", [("Land- & Umwelttechnik", "ABC234")] * 3, "https://wahl.htl1-klagenfurt.at")
    assert pdf.startswith(b"%PDF")
