import threading

from sqlalchemy import func, select

from app.models import Code, Vote
from app.timeutil import utcnow
from datetime import timedelta
from tests.conftest import ballot_choices, make_codes


def test_voting_flow_and_counts(client, db, election):
    codes = make_codes(db, election, 0, 2)

    # QR-Link füllt nur das Feld aus und verbraucht nichts
    r = client.get(f"/c/{codes[0]}")
    assert r.status_code == 200 and codes[0] in r.text
    assert db.scalar(select(func.count()).select_from(Code).where(Code.used.is_(True))) == 0

    # Stimmzettel: Schulsprecher + NUR Elektrotechnik-Abteilungssprecher
    r = client.post("/vote/start", data={"code": codes[0].lower()})  # Kleinschreibung ok
    assert r.status_code == 200
    assert "Schulsprecher/in" in r.text and "Clara" in r.text and "Eva" not in r.text

    data = ballot_choices(election, db, codes[0], school=0, dept=1)
    r = client.post("/vote/review", data=data)
    assert r.status_code == 200 and "Anna" in r.text and "David" in r.text
    assert db.scalar(select(func.count()).select_from(Vote)) == 0  # Review speichert nichts

    r = client.post("/vote/submit", data=data)
    assert r.status_code == 200 and "Danke" in r.text
    assert db.scalar(select(func.count()).select_from(Vote)) == 2

    # Code ist verbraucht
    r = client.post("/vote/start", data={"code": codes[0]})
    assert r.status_code == 400 and "bereits verwendet" in r.text
    r = client.post("/vote/submit", data=data)
    assert r.status_code == 400
    assert db.scalar(select(func.count()).select_from(Vote)) == 2


def test_invalid_vote_is_stored_as_null_candidate(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    data = ballot_choices(election, db, code, school="invalid", dept=0)
    assert client.post("/vote/submit", data=data).status_code == 200
    votes = db.scalars(select(Vote)).all()
    assert sorted(v.candidate_id is None for v in votes) == [False, True]


def test_missing_choice_does_not_consume_code(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    data = ballot_choices(election, db, code)
    data.pop([k for k in data if k.startswith("race_")][0])
    r = client.post("/vote/submit", data=data)
    assert r.status_code == 400 and "Auswahl" in r.text
    assert db.scalar(select(func.count()).select_from(Vote)) == 0
    assert db.scalar(select(Code.used).where(Code.code == code)) is False


def test_candidate_of_other_department_is_rejected(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    data = ballot_choices(election, db, code)
    eva = [c for r in election.races for c in r.candidates if c.name == "Eva"][0]
    key = [k for k in data if k.startswith("race_")][1]
    data[key] = str(eva.id)
    assert client.post("/vote/submit", data=data).status_code == 400
    assert db.scalar(select(func.count()).select_from(Vote)) == 0


def test_unknown_code_and_rate_limit(client, db, election):
    assert client.post("/vote/start", data={"code": "ZZZZZZ"}).status_code == 400
    from app import config
    for _ in range(config.CODE_MAX_FAILS):
        client.post("/vote/start", data={"code": "ABCDEF"})
    r = client.post("/vote/start", data={"code": "ABCDEF"})
    assert r.status_code == 429


def test_closed_and_not_started(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    election.end_at = utcnow() - timedelta(minutes=1)
    db.commit()
    r = client.post("/vote/start", data={"code": code})
    assert r.status_code == 403 and "beendet" in r.text

    election.end_at = utcnow() + timedelta(hours=1)
    election.start_at = utcnow() + timedelta(minutes=30)
    db.commit()
    r = client.post("/vote/start", data={"code": code})
    assert r.status_code == 403 and "beginnt" in r.text

    election.start_at = utcnow() - timedelta(minutes=5)
    election.closed_at = utcnow()  # Admin beendet vorzeitig
    db.commit()
    assert client.post("/vote/start", data={"code": code}).status_code == 403


def test_concurrent_double_submit_counts_once(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    data = ballot_choices(election, db, code)
    results = []

    def go():
        results.append(client.post("/vote/submit", data=data).status_code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(200) == 1
    assert db.scalar(select(func.count()).select_from(Vote)) == 2


def test_votes_are_not_linked_to_codes():
    """Wahlgeheimnis: Strukturell darf es keine Verbindung zwischen votes und codes geben."""
    from app.models import Vote
    cols = {c.name for c in Vote.__table__.columns}
    assert cols == {"id", "race_id", "candidate_id"}
    assert not any(fk.column.table.name == "codes" for c in Vote.__table__.columns for fk in c.foreign_keys)


def test_no_cookie_for_voters(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    r = client.post("/vote/start", data={"code": code})
    assert "set-cookie" not in r.headers
