from datetime import timedelta

from sqlalchemy import func, select

from app.models import Candidate, Code, Election, Race
from app.services import decide
from app.timeutil import input_local, utcnow
from tests.conftest import admin_login, ballot_choices, decide_all, make_codes


class C:  # Mini-Kandidat für decide()
    def __init__(self, name):
        self.name = name


def rows(*votes):
    return [{"candidate": C(chr(65 + i)), "votes": v} for i, v in enumerate(votes)]


def names(cs):
    return [c.name for c in cs]


def test_decide_winner_and_deputy():
    d = decide(rows(5, 3, 1))
    assert d["winner"].name == "A" and d["deputy"].name == "B" and not d["runoff_first"] and not d["runoff_second"]


def test_decide_tie_for_first_needs_runoff_and_deputy_pending():
    d = decide(rows(4, 4, 1))
    assert d["winner"] is None and names(d["runoff_first"]) == ["A", "B"] and d["deputy_pending"]


def test_decide_tie_for_second_only():
    d = decide(rows(6, 2, 2))
    assert d["winner"].name == "A" and d["deputy"] is None and names(d["runoff_second"]) == ["B", "C"]


def test_decide_no_deputy_without_votes_and_empty():
    d = decide(rows(3, 0))
    assert d["winner"].name == "A" and d["deputy"] is None and not d["runoff_second"]
    assert decide(rows(0, 0))["winner"] is None


def test_decide_runoff_for_deputy_only_one_place():
    d = decide(rows(3, 1), "deputy")
    assert d["winner"].name == "A" and d["deputy"] is None


def vote_all(client, db, election, plans, dept=0):
    codes = make_codes(db, election, dept, len(plans))
    for code, (s, d) in zip(codes, plans):
        assert client.post("/vote/submit", data=ballot_choices(election, db, code, s, d)).status_code == 200


def test_bars_are_css_classes_not_inline_styles(client, db, election):
    token = admin_login(client)
    vote_all(client, db, election, [(0, 0), (0, 0), (0, 1)])
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    html = client.get(f"/admin/elections/{election.id}/dashboard").text
    assert "style=" not in html
    assert 'class="w100"' in html and 'class="w0"' in html  # Anna 100 %, Ben 0 %
    assert 'class="w67"' in html  # Clara 2 von 3


def test_dashboard_shows_winner_and_deputy(client, db, election):
    token = admin_login(client)
    vote_all(client, db, election, [(0, 0), (0, 0), (1, 1)])
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    html = client.get(f"/admin/elections/{election.id}/dashboard").text
    assert "Gewählt" in html and "Anna" in html and "Stellvertreter/in" in html and "Ben" in html


def test_runoff_creation_for_tie(client, db, election):
    token = admin_login(client)
    vote_all(client, db, election, [(0, 0), (1, 1)])  # Anna 1 : Ben 1 -> Stichwahl
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    dash = client.get(f"/admin/elections/{election.id}/dashboard").text
    assert "Stimmengleichheit um Platz 1" in dash and "Eine Stichwahl wird empfohlen" in dash
    school = election.races[0]
    # ohne Entscheidung "ja" lässt sich keine Stichwahl anlegen
    assert client.post(f"/admin/elections/{election.id}/runoff", data={"csrf_token": token, "race_id": school.id}).status_code == 303
    assert db.scalar(select(func.count()).select_from(Election)) == 1
    client.post(f"/admin/races/{school.id}/runoff-decision", data={"csrf_token": token, "answer": "yes"})
    assert "Stichwahl anlegen" in client.get(f"/admin/elections/{election.id}/dashboard").text
    r = client.post(f"/admin/elections/{election.id}/runoff", data={"csrf_token": token, "race_id": school.id, "kind": "speaker"})
    assert r.status_code == 303
    new = db.scalars(select(Election).where(Election.id != election.id)).one()
    assert new.name.startswith("Stichwahl") and len(new.departments) == 2
    race = db.scalars(select(Race).where(Race.election_id == new.id)).one()
    assert race.runoff_kind == "speaker"
    assert sorted(c.name for c in race.candidates) == ["Anna", "Ben"]

    # kein Gleichstand im Abteilungs-Wahlgang -> keine Stichwahl
    dept_race = election.races[2]  # Maschinenbau: keine Stimmen, kein Gleichstand
    client.post(f"/admin/races/{dept_race.id}/runoff-decision", data={"csrf_token": token, "answer": "yes"})
    client.post(f"/admin/elections/{election.id}/runoff", data={"csrf_token": token, "race_id": dept_race.id, "kind": "speaker"})
    assert db.scalar(select(func.count()).select_from(Election)) == 2


def test_past_elections_only_after_publish(client, db, election):
    token = admin_login(client)
    assert "Vergangene Wahlen" in client.get("/").text
    vote_all(client, db, election, [(0, 0), (0, 0), (1, 0)])
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    assert "Anna" not in client.get("/vergangene").text
    decide_all(client, db, election, token)
    client.post(f"/admin/elections/{election.id}/publish", data={"csrf_token": token})
    page = client.get("/vergangene").text
    assert election.name in page and "Anna" in page and "Ben" in page and "Clara" in page


def test_class_mode_codes_and_bulk_delete(client, db):
    token = admin_login(client)
    start = utcnow() + timedelta(days=1)
    client.post("/admin/elections", data={"csrf_token": token, "name": "Klassenwahl", "code_mode": "class",
                                          "start_at": input_local(start), "end_at": input_local(start + timedelta(hours=8)),
                                          "departments": "Elektrotechnik", "standard_races": "1"})
    e = db.scalar(select(Election))
    assert e.code_mode == "class"
    dept = e.departments[0]
    page = client.get(f"/admin/elections/{e.id}/codes").text
    assert 'name="group_name"' in page

    # ohne Klasse -> Fehler, nichts erzeugt
    client.post(f"/admin/elections/{e.id}/codes", data={"csrf_token": token, "department_id": dept.id, "count": 5, "base_url": "http://x"})
    assert db.scalar(select(func.count()).select_from(Code)) == 0

    for group, n in [("5AHD", 22), ("4AHD", 18)]:
        r = client.post(f"/admin/elections/{e.id}/codes", data={"csrf_token": token, "department_id": dept.id, "count": n,
                                                                  "group_name": group, "base_url": "http://x"})
        assert r.status_code == 303 and client.get(r.headers["location"]).content.startswith(b"%PDF")
    dash = client.get(f"/admin/elections/{e.id}/dashboard").text
    assert "5AHD" in dash and "22" in dash

    # Sammel-Löschen: nur offene Codes der Klasse 5AHD
    client.post(f"/admin/elections/{e.id}/codes/delete-unused", data={"csrf_token": token, "department_id": dept.id, "group_name": "5AHD"})
    assert db.scalar(select(func.count()).select_from(Code).where(Code.group_name == "5AHD")) == 0
    assert db.scalar(select(func.count()).select_from(Code).where(Code.group_name == "4AHD")) == 18


def test_bulk_delete_keeps_used_codes(client, db, election):
    token = admin_login(client)
    codes = make_codes(db, election, 0, 5)
    client.post("/vote/submit", data=ballot_choices(election, db, codes[0]))
    client.post(f"/admin/elections/{election.id}/codes/delete-unused", data={"csrf_token": token, "department_id": election.departments[0].id})
    assert db.scalar(select(func.count()).select_from(Code)) == 1
    assert db.scalar(select(Code.used)) is True
    # neue Codes lassen sich danach erzeugen
    assert len(make_codes(db, election, 0, 2)) == 3


def test_dept_codes_have_no_group_and_mode_default(client, db, election):
    assert election.code_mode == "department"
    token = admin_login(client)
    r = client.post(f"/admin/elections/{election.id}/codes", data={"csrf_token": token, "department_id": election.departments[0].id,
                                                                     "count": 3, "group_name": "5AHD", "base_url": "http://x"})
    assert r.status_code == 303
    assert db.scalar(select(func.count()).select_from(Code).where(Code.group_name != "")) == 0
