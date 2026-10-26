"""Stichwahl-Entscheidung durch die Wahlleitung: Empfehlung, Ja/Nein pro Wahlgang, Sperre nach Veröffentlichung."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import Candidate, Department, Election, Race, Vote
from app.services import compute_results
from app.timeutil import utcnow
from tests.conftest import admin_login, make_user


def ended_election(db, counts, name="Schulsprecherwahl"):
    """Beendete Wahl mit EINEM Wahlgang; counts = {Kandidat: Stimmen}. Stimmen werden direkt in votes geschrieben."""
    e = Election(name=name, start_at=utcnow() - timedelta(days=2), end_at=utcnow() - timedelta(days=1))
    e.departments = [Department(name="Elektrotechnik")]
    db.add(e)
    db.flush()
    race = Race(election_id=e.id, title="Schulsprecher/in", position=0)
    db.add(race)
    db.flush()
    for cname, n in counts.items():
        c = Candidate(race_id=race.id, name=cname)
        db.add(c)
        db.flush()
        db.add_all(Vote(id=uuid.uuid4().hex, race_id=race.id, candidate_id=c.id) for _ in range(n))
    db.commit()
    return e, race


def result_of(db, e):
    return compute_results(db, e)[0]


# ------------------------------------------------------------ Empfehlung

def test_recommend_runoff_for_the_real_life_example(db):
    """4 Kandidat/innen mit 91, 105, 224 und 264 Stimmen: niemand hat die Mehrheit -> Stichwahl der zwei Besten."""
    e, _ = ended_election(db, {"Erster": 91, "Zweite": 105, "Dritte": 224, "Bester": 264})
    r = result_of(db, e)
    assert r["recommend"] and not r["majority"]
    assert "38.6 %" in r["reason"] and "Bester" in r["reason"]
    assert [c.name for c in r["plan"]] == ["Bester", "Dritte"] and r["plan_kind"] == "speaker"


def test_no_runoff_recommended_for_two_candidates_even_if_close(db):
    e, _ = ended_election(db, {"Anna": 130, "Ben": 128})
    r = result_of(db, e)
    assert not r["recommend"] and "Nur 2 Kandidat" in r["reason"]
    assert r["winner"].name == "Anna" and r["deputy"].name == "Ben"


def test_no_runoff_with_absolute_majority(db):
    e, _ = ended_election(db, {"A": 60, "B": 25, "C": 15})
    r = result_of(db, e)
    assert not r["recommend"] and r["majority"] and "absolute Mehrheit" in r["reason"]
    assert r["winner"].name == "A" and r["deputy"].name == "B"  # Platz 2 ist Stellvertretung


def test_exactly_half_is_not_a_majority(db):
    e, _ = ended_election(db, {"A": 50, "B": 30, "C": 20})
    assert result_of(db, e)["recommend"]


def test_tie_for_first_recommends_runoff(db):
    e, _ = ended_election(db, {"A": 40, "B": 40, "C": 20})
    r = result_of(db, e)
    assert r["recommend"] and "Stimmengleichheit um Platz 1" in r["reason"]
    assert {c.name for c in r["plan"]} == {"A", "B"}


def test_tie_for_second_with_majority_means_deputy_runoff(db):
    e, _ = ended_election(db, {"A": 60, "B": 20, "C": 20})
    r = result_of(db, e)
    assert r["recommend"] and r["plan_kind"] == "deputy" and {c.name for c in r["plan"]} == {"B", "C"}


def test_tie_for_second_place_without_majority_takes_all_tied(db):
    e, _ = ended_election(db, {"A": 40, "B": 30, "C": 30})
    r = result_of(db, e)
    assert [c.name for c in r["plan"]] == ["A", "B", "C"]


def test_no_votes_no_recommendation(db):
    e, _ = ended_election(db, {"A": 0, "B": 0, "C": 0})
    r = result_of(db, e)
    assert not r["recommend"] and r["plan"] == []


# ------------------------------------------------------------ Entscheidung per Weboberfläche

def decide(client, token, race, answer):
    return client.post(f"/admin/races/{race.id}/runoff-decision", data={"csrf_token": token, "answer": answer})


def test_dashboard_asks_and_shows_recommendation(client, db):
    e, race = ended_election(db, {"Erster": 91, "Zweite": 105, "Dritte": 224, "Bester": 264})
    admin_login(client)
    html = client.get(f"/admin/elections/{e.id}/dashboard").text
    assert "Empfehlung: Stichwahl durchführen." in html
    assert "Entscheidung offen" in html
    assert "Ja, Stichwahl durchführen (empfohlen)" in html and "Nein, keine Stichwahl" in html


def test_cannot_publish_before_every_race_is_decided(client, db):
    e, race = ended_election(db, {"A": 60, "B": 40})
    token = admin_login(client)
    client.post(f"/admin/elections/{e.id}/publish", data={"csrf_token": token})
    db.refresh(e)
    assert not e.results_public
    assert decide(client, token, race, "no").status_code == 303
    client.post(f"/admin/elections/{e.id}/publish", data={"csrf_token": token})
    db.refresh(e)
    assert e.results_public


def test_decision_is_locked_while_published_and_reversible_after_unpublishing(client, db):
    e, race = ended_election(db, {"A": 40, "B": 35, "C": 25})
    token = admin_login(client)
    decide(client, token, race, "no")
    client.post(f"/admin/elections/{e.id}/publish", data={"csrf_token": token})
    db.refresh(race); db.refresh(e)
    assert e.results_public and race.runoff_decision == "no"

    decide(client, token, race, "yes")           # gesperrt
    db.refresh(race)
    assert race.runoff_decision == "no"
    assert "gesperrt" in client.get(f"/admin/elections/{e.id}/dashboard").text

    client.post(f"/admin/elections/{e.id}/publish", data={"csrf_token": token})   # Veröffentlichung zurücknehmen
    db.refresh(e)
    assert not e.results_public
    decide(client, token, race, "yes")
    db.refresh(race)
    assert race.runoff_decision == "yes"
    decide(client, token, race, "")              # Entscheidung zurücknehmen
    db.refresh(race)
    assert race.runoff_decision == ""
    client.post(f"/admin/elections/{e.id}/publish", data={"csrf_token": token})   # wieder blockiert
    db.refresh(e)
    assert not e.results_public


def test_decision_only_after_election_ended(client, db, election):
    token = admin_login(client)
    race = election.races[0]
    decide(client, token, race, "yes")
    db.refresh(race)
    assert race.runoff_decision == ""


def test_invalid_answer_rejected(client, db):
    e, race = ended_election(db, {"A": 5, "B": 3})
    token = admin_login(client)
    assert decide(client, token, race, "vielleicht").status_code == 400
    assert client.post(f"/admin/races/{race.id}/runoff-decision", data={"csrf_token": token}).status_code == 303  # leer = zurücknehmen


def test_wahlleiter_may_decide(client, db):
    e, race = ended_election(db, {"A": 5, "B": 3})
    make_user(db)
    token = admin_login(client, "leiter", "wahlleiter-passwort")
    decide(client, token, race, "no")
    db.refresh(race)
    assert race.runoff_decision == "no"


# ------------------------------------------------------------ Anzeige nach der Entscheidung

def publish(client, db, e, token):
    client.post(f"/admin/elections/{e.id}/publish", data={"csrf_token": token})
    db.refresh(e)
    assert e.results_public


def test_public_page_after_no_shows_winner_and_deputy(client, db):
    e, race = ended_election(db, {"Erster": 91, "Zweite": 105, "Dritte": 224, "Bester": 264})
    token = admin_login(client)
    decide(client, token, race, "no")
    publish(client, db, e, token)
    page = client.get(f"/ergebnis/{e.id}").text
    assert "Gewählt" in page and "Bester" in page and "Stellvertreter/in" in page and "Dritte" in page
    assert "Stichwahl nötig" not in page
    assert "Empfehlung" not in page and "Eure Entscheidung" not in page  # Entscheidungsfeld ist nur fürs Backoffice


def test_public_page_after_yes_announces_runoff_without_winner(client, db):
    e, race = ended_election(db, {"Erster": 91, "Zweite": 105, "Dritte": 224, "Bester": 264})
    token = admin_login(client)
    decide(client, token, race, "yes")
    publish(client, db, e, token)
    page = client.get(f"/ergebnis/{e.id}").text
    assert "Stichwahl" in page and "Bester, Dritte" in page
    assert "Gewählt:" not in page and "Stellvertreter/in:" not in page
    assert "Stichwahl anlegen" not in page   # Button nur im Backoffice
    assert "Stichwahl: Bester, Dritte" in client.get("/vergangene").text
    assert client.get(f"/admin/elections/{e.id}/protocol.pdf").status_code == 200


def test_runoff_is_created_with_the_two_best(client, db):
    e, race = ended_election(db, {"Erster": 91, "Zweite": 105, "Dritte": 224, "Bester": 264})
    token = admin_login(client)
    assert "Stichwahl anlegen" not in client.get(f"/admin/elections/{e.id}/dashboard").text
    decide(client, token, race, "yes")
    assert "Stichwahl anlegen" in client.get(f"/admin/elections/{e.id}/dashboard").text
    r = client.post(f"/admin/elections/{e.id}/runoff", data={"csrf_token": token, "race_id": race.id})
    assert r.status_code == 303
    new = db.scalars(select(Election).where(Election.id != e.id)).one()
    new_race = db.scalars(select(Race).where(Race.election_id == new.id)).one()
    assert sorted(c.name for c in new_race.candidates) == ["Bester", "Dritte"] and new_race.runoff_kind == "speaker"


def test_runoff_after_no_decision_is_refused(client, db):
    e, race = ended_election(db, {"A": 40, "B": 35, "C": 25})
    token = admin_login(client)
    decide(client, token, race, "no")
    client.post(f"/admin/elections/{e.id}/runoff", data={"csrf_token": token, "race_id": race.id})
    assert db.scalar(select(func.count()).select_from(Election)) == 1


def test_loser_of_runoff_becomes_deputy(client, db):
    """Zweiter ist immer Stellvertreter: in der Stichwahl zwischen zwei Kandidat/innen ergibt sich beides."""
    e, _ = ended_election(db, {"Gewinner": 120, "Verlierer": 100}, name="Stichwahl")
    r = result_of(db, e)
    assert r["winner"].name == "Gewinner" and r["deputy"].name == "Verlierer"


def test_existing_database_gets_the_new_column(db):
    """Update einer bestehenden Installation: die Spalte wird beim Start ergänzt, alte Wahlgänge gelten als 'offen'."""
    from sqlalchemy import inspect, text
    from app import db as dbmod
    e, race = ended_election(db, {"A": 3, "B": 2})
    db.close()
    with dbmod.engine.begin() as conn:
        conn.execute(text("ALTER TABLE races DROP COLUMN runoff_decision"))
    assert "runoff_decision" not in {c["name"] for c in inspect(dbmod.engine).get_columns("races")}
    dbmod.init_db()
    assert "runoff_decision" in {c["name"] for c in inspect(dbmod.engine).get_columns("races")}
    with dbmod.SessionLocal() as s:
        assert s.get(Race, race.id).runoff_decision == ""


# ------------------------------------------------------------ gemeinsame Stichwahl

def multi_election(db):
    """Beendete Wahl mit 3 Abteilungen: Schulsprecher (4 Kandidaten, keine Mehrheit) + je ein Abteilungssprecher (je 3 Kandidaten, keine Mehrheit)."""
    e = Election(name="Hauptwahl", start_at=utcnow() - timedelta(days=2), end_at=utcnow() - timedelta(days=1))
    e.departments = [Department(name="ET"), Department(name="ME"), Department(name="MB")]
    db.add(e)
    db.flush()
    races = {}

    def race(key, title, dept, counts):
        r = Race(election_id=e.id, title=title, department_id=dept.id if dept else None, position=len(races))
        db.add(r)
        db.flush()
        for n, v in counts.items():
            c = Candidate(race_id=r.id, name=n)
            db.add(c)
            db.flush()
            db.add_all(Vote(id=uuid.uuid4().hex, race_id=r.id, candidate_id=c.id) for _ in range(v))
        races[key] = r

    by = {d.name: d for d in e.departments}
    race("school", "Schulsprecher/in", None, {"S1": 90, "S2": 100, "S3": 220, "S4": 260})
    for key in ("ET", "ME", "MB"):
        race(key, "Abteilungssprecher/in", by[key], {f"{key}-A": 50, f"{key}-B": 40, f"{key}-C": 30})
    db.commit()
    return e, races


def ballots(db, new, dept_name):
    """Welche Wahlgänge sieht jemand mit einem Code dieser Abteilung?"""
    from app.services import ballot_for, generate_codes
    new.start_at = utcnow() - timedelta(hours=1)
    db.commit()
    dept = next(d for d in new.departments if d.name == dept_name)
    generate_codes(db, new, dept, 1)
    from app.models import Code
    code = db.scalar(select(Code).where(Code.department_id == dept.id))
    _, races = ballot_for(db, code)
    return [(r.title, [c.name for c in r.candidates]) for r in races]


def run_all(client, db, races, token, yes):
    for key in races:
        decide(client, token, races[key], "yes" if key in yes else "no")


def test_combined_runoff_school_plus_one_department(client, db):
    e, races = multi_election(db)
    token = admin_login(client)
    run_all(client, db, races, token, yes={"school", "MB"})
    page = client.get(f"/admin/elections/{e.id}/dashboard").text
    assert "Alle Stichwahlen in einer Wahl anlegen" in page and "ET, MB, ME" in page
    assert client.post(f"/admin/elections/{e.id}/runoff-all", data={"csrf_token": token}).status_code == 303
    new = db.scalars(select(Election).where(Election.id != e.id)).one()
    assert [d.name for d in new.departments] == ["ET", "MB", "ME"]
    assert db.scalar(select(func.count()).select_from(Race).where(Race.election_id == new.id)) == 2
    # ET und ME wählen nur den Schulsprecher, MB zusätzlich den Abteilungssprecher
    assert ballots(db, new, "ET") == [("Stichwahl Schulsprecher/in", ["S3", "S4"])]
    assert ballots(db, new, "ME") == [("Stichwahl Schulsprecher/in", ["S3", "S4"])]
    mb = ballots(db, new, "MB")
    assert [t for t, _ in mb] == ["Stichwahl Schulsprecher/in", "Stichwahl Abteilungssprecher/in"]
    assert mb[1][1] == ["MB-A", "MB-B"]


def test_combined_runoff_two_departments_leaves_third_out(client, db):
    e, races = multi_election(db)
    token = admin_login(client)
    run_all(client, db, races, token, yes={"ET", "ME"})
    assert "ET, ME" in client.get(f"/admin/elections/{e.id}/dashboard").text
    client.post(f"/admin/elections/{e.id}/runoff-all", data={"csrf_token": token})
    new = db.scalars(select(Election).where(Election.id != e.id)).one()
    assert sorted(d.name for d in new.departments) == ["ET", "ME"]   # MB gibt es gar nicht
    assert ballots(db, new, "ET") == [("Stichwahl Abteilungssprecher/in", ["ET-A", "ET-B"])]
    assert ballots(db, new, "ME") == [("Stichwahl Abteilungssprecher/in", ["ME-A", "ME-B"])]


def test_combined_runoff_needs_a_yes_and_ended_election(client, db, election):
    token = admin_login(client)
    # laufende Wahl
    client.post(f"/admin/elections/{election.id}/runoff-all", data={"csrf_token": token})
    assert db.scalar(select(func.count()).select_from(Election)) == 1
    e, races = multi_election(db)
    run_all(client, db, races, token, yes=set())
    client.post(f"/admin/elections/{e.id}/runoff-all", data={"csrf_token": token})
    assert db.scalar(select(func.count()).select_from(Election)) == 2   # nur "election" und "Hauptwahl"
    assert "Alle Stichwahlen in einer Wahl anlegen" not in client.get(f"/admin/elections/{e.id}/dashboard").text


def test_single_department_runoff_is_bound_to_its_department(client, db):
    e, races = multi_election(db)
    token = admin_login(client)
    run_all(client, db, races, token, yes={"ME"})
    client.post(f"/admin/elections/{e.id}/runoff", data={"csrf_token": token, "race_id": races["ME"].id})
    new = db.scalars(select(Election).where(Election.id != e.id)).one()
    assert [d.name for d in new.departments] == ["ME"]
    assert ballots(db, new, "ME") == [("Stichwahl Abteilungssprecher/in", ["ME-A", "ME-B"])]


def test_combined_runoff_ties_for_second_place_and_photos(client, db):
    e, races = multi_election(db)
    # Foto am Kandidaten S4 muss in die Stichwahl mitkommen
    from app.models import Image
    img = Image(data=b"x", content_type="image/jpeg")
    db.add(img)
    db.flush()
    db.scalar(select(Candidate).where(Candidate.name == "S4")).image_id = img.id
    db.commit()
    token = admin_login(client)
    run_all(client, db, races, token, yes={"school"})
    client.post(f"/admin/elections/{e.id}/runoff-all", data={"csrf_token": token})
    new = db.scalars(select(Election).where(Election.id != e.id)).one()
    s4 = db.scalar(select(Candidate).join(Race).where(Race.election_id == new.id, Candidate.name == "S4"))
    assert s4.image_id and s4.image_id != img.id
