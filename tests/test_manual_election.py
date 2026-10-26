"""Manuell erfasste (bereits analog durchgeführte) Wahlen: Anlegen, Nachtragen der Ergebnisse, Stichwahl-Weg."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Candidate, Election, Race, Vote
from app.services import compute_results, election_status
from app.timeutil import input_local, utcnow
from tests.conftest import admin_login, csrf, decide_all


def _create_manual_election(client, token, name="Analoge Wahl"):
    start = utcnow() - timedelta(days=7)
    end = start + timedelta(hours=2)
    r = client.post("/admin/elections", data={
        "csrf_token": token, "name": name, "start_at": input_local(start), "end_at": input_local(end),
        "departments": "Elektrotechnik\nMaschinenbau", "source": "manual", "standard_races": "1",
    })
    assert r.status_code == 303
    return r.headers["location"]


def test_manual_election_is_created_as_ended_with_source_manual(client, db):
    admin_login(client)
    token = csrf(client)
    location = _create_manual_election(client, token)
    e = db.scalar(select(Election).where(Election.id == int(location.rsplit("/", 1)[-1])))
    assert e.source == "manual"
    assert election_status(e) == "ended"


def test_candidates_stay_editable_despite_ended_status(client, db):
    admin_login(client)
    token = csrf(client)
    location = _create_manual_election(client, token)
    election_id = int(location.rsplit("/", 1)[-1])
    race = db.scalar(select(Race).where(Race.election_id == election_id, Race.title == "Schulsprecher/in"))

    r = client.post(f"/admin/races/{race.id}/candidates", data={"csrf_token": token, "name": "Anna"})
    assert r.status_code == 303
    cand = db.scalar(select(Candidate).where(Candidate.race_id == race.id, Candidate.name == "Anna"))
    assert cand is not None

    r = client.post(f"/admin/candidates/{cand.id}/edit", data={"csrf_token": token, "name": "Anna Beispiel"})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Candidate, cand.id).name == "Anna Beispiel"


def test_manual_results_are_saved_and_counted(client, db):
    admin_login(client)
    token = csrf(client)
    location = _create_manual_election(client, token)
    election_id = int(location.rsplit("/", 1)[-1])
    race = db.scalar(select(Race).where(Race.election_id == election_id, Race.title == "Schulsprecher/in"))
    a = Candidate(race_id=race.id, name="Anna")
    b = Candidate(race_id=race.id, name="Ben")
    db.add_all([a, b])
    db.commit()

    r = client.post(f"/admin/elections/{election_id}/manual-results",
                    data={"csrf_token": token, f"votes_{a.id}": "80", f"votes_{b.id}": "20", f"invalid_{race.id}": "5"})
    assert r.status_code == 303

    e = db.get(Election, election_id)
    results = compute_results(db, e)
    row = next(res for res in results if res["race"].id == race.id)
    assert row["valid"] == 100
    assert row["invalid"] == 5
    assert row["consistent"] is True
    assert row["winner"].name == "Anna"

    # Erneutes Speichern ersetzt die Stimmen vollständig statt sie zu addieren.
    r = client.post(f"/admin/elections/{election_id}/manual-results",
                    data={"csrf_token": token, f"votes_{a.id}": "10", f"votes_{b.id}": "30", f"invalid_{race.id}": "0"})
    assert r.status_code == 303
    results = compute_results(db, e)
    row = next(res for res in results if res["race"].id == race.id)
    assert row["valid"] == 40
    assert row["winner"].name == "Ben"


def test_manual_election_publish_and_runoff_creates_live_election(client, db):
    admin_login(client)
    token = csrf(client)
    location = _create_manual_election(client, token)
    election_id = int(location.rsplit("/", 1)[-1])
    race = db.scalar(select(Race).where(Race.election_id == election_id, Race.title == "Schulsprecher/in"))
    a = Candidate(race_id=race.id, name="Anna")
    b = Candidate(race_id=race.id, name="Ben")
    c = Candidate(race_id=race.id, name="Clara")
    db.add_all([a, b, c])
    db.commit()
    # Gleichstand um Platz 1 -> Stichwahl nötig
    client.post(f"/admin/elections/{election_id}/manual-results",
               data={"csrf_token": token, f"votes_{a.id}": "40", f"votes_{b.id}": "40", f"votes_{c.id}": "5",
                     f"invalid_{race.id}": "0"})

    decide_all(client, db, db.get(Election, election_id), token, answer="no")  # andere Wahlgänge
    client.post(f"/admin/races/{race.id}/runoff-decision", data={"csrf_token": token, "answer": "yes"})

    r = client.post(f"/admin/elections/{election_id}/publish", data={"csrf_token": token})
    assert r.status_code == 303
    e = db.get(Election, election_id)
    assert e.results_public is True

    # Veröffentlicht -> Struktur und Ergebnisse gesperrt
    r = client.post(f"/admin/races/{race.id}/candidates", data={"csrf_token": token, "name": "Neu"})
    assert r.status_code == 303
    assert db.scalar(select(Candidate).where(Candidate.race_id == race.id, Candidate.name == "Neu")) is None

    # Veröffentlichung zurücknehmen, dann Stichwahl anlegen: wird eine normale (live) Wahl mit Codes
    client.post(f"/admin/elections/{election_id}/publish", data={"csrf_token": token})
    r = client.post(f"/admin/elections/{election_id}/runoff", data={"csrf_token": token, "race_id": str(race.id)})
    assert r.status_code == 303
    new_id = int(r.headers["location"].rsplit("/", 1)[-1])
    new_election = db.get(Election, new_id)
    assert new_election.source == "live"
