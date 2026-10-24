"""Urne und Wahlgeheimnis: Stimmen werden erst nach Wahlende gemischt in `votes` übernommen."""
import os
import threading

import pytest
from sqlalchemy import func, select, text

from app.db import SessionLocal, engine
from app.models import Code, PendingBallot, Vote
from app.services import compute_results, flush_ballots
from app.timeutil import utcnow
from tests.conftest import ballot_choices, make_codes, vote_count


def _vote_n(client, db, election, n):
    codes = make_codes(db, election, 0, n)
    for code in codes:
        assert client.post("/vote/submit", data=ballot_choices(election, db, code)).status_code == 200
    return codes


def _end(db, election):
    election.closed_at = utcnow()
    db.commit()


def test_votes_wait_in_ballot_box_until_election_ends(client, db, election):
    _vote_n(client, db, election, 3)
    assert db.scalar(select(func.count()).select_from(Vote)) == 0
    assert db.scalar(select(func.count()).select_from(PendingBallot)) == 6
    assert flush_ballots(db, election) == 0  # läuft noch -> nichts wird übernommen
    assert db.scalar(select(func.count()).select_from(Vote)) == 0


def test_flush_moves_everything_exactly_once(client, db, election):
    _vote_n(client, db, election, 5)
    _end(db, election)
    assert flush_ballots(db, election) == 10
    assert flush_ballots(db, election) == 0
    assert db.scalar(select(func.count()).select_from(Vote)) == 10
    assert db.scalar(select(func.count()).select_from(PendingBallot)) == 0


def test_results_are_complete_and_consistent_after_end(client, db, election):
    _vote_n(client, db, election, 4)
    _end(db, election)
    results = compute_results(db, election)
    assert all(r["consistent"] for r in results if r["total"] or r["expected"])
    school = next(r for r in results if r["department"] is None)
    assert school["total"] == 4


def test_concurrent_flush_does_not_duplicate_or_lose_votes(client, db, election):
    _vote_n(client, db, election, 20)
    _end(db, election)
    moved = []

    def run():
        with SessionLocal() as s:
            from app.models import Election
            moved.append(flush_ballots(s, s.get(Election, election.id)))

    threads = [threading.Thread(target=run) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(moved) == 40
    assert db.scalar(select(func.count()).select_from(Vote)) == 40


def test_flush_shuffles_order(client, db, election):
    """Die Reihenfolge in `votes` darf nicht der Abgabereihenfolge entsprechen (sonst wäre sie ein Zeitstempel)."""
    from app.models import Candidate, Race
    codes = make_codes(db, election, 0, 30)
    anna, ben = db.scalars(select(Candidate).where(Candidate.name.in_(["Anna", "Ben"])).order_by(Candidate.name)).all()
    for i, code in enumerate(codes):  # erst 15 Stimmen für Anna, dann 15 für Ben
        data = ballot_choices(election, db, code, school=0 if i < 15 else 1)
        assert client.post("/vote/submit", data=data).status_code == 200
    _end(db, election)
    flush_ballots(db, election)
    school = db.scalar(select(Race).where(Race.title == "Schulsprecher/in"))
    seq = [v.candidate_id for v in db.scalars(select(Vote).where(Vote.race_id == school.id))]
    assert sorted(seq) == [anna.id] * 15 + [ben.id] * 15
    assert seq != [anna.id] * 15 + [ben.id] * 15  # ungemischt wäre genau das (Wahrscheinlichkeit zufällig: 1 zu 155 Mio.)


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="xmin gibt es nur in PostgreSQL")
def test_no_transaction_id_links_votes_to_codes(client, db, election):
    codes = _vote_n(client, db, election, 8)
    _end(db, election)
    flush_ballots(db, election)
    code_xids = {r[0] for r in db.execute(text("SELECT xmin::text FROM codes WHERE used"))}
    vote_xids = {r[0] for r in db.execute(text("SELECT xmin::text FROM votes"))}
    assert len(vote_xids) == 1                      # alle Stimmen: dieselbe Transaktion
    assert not (code_xids & vote_xids)              # und keine davon gehört zu einem Code


def test_pending_rows_have_no_code_reference():
    cols = {c.name for c in PendingBallot.__table__.columns}
    assert cols == {"id", "race_id", "candidate_id"}


def test_delete_election_removes_pending_ballots(client, db, election):
    from app.services import delete_election
    _vote_n(client, db, election, 2)
    delete_election(db, election)
    assert vote_count(db) == 0


def test_ballot_order_is_random_per_ballot(client, db, election):
    from app.services import ballot_for, lookup_code
    code = make_codes(db, election, 0, 1)[0]
    school_race_orders = set()
    for _ in range(40):
        _, races = ballot_for(db, lookup_code(db, code))
        school_race_orders.add(tuple(c.name for c in races[0].ballot_candidates))
    assert len(school_race_orders) == 2  # beide Reihenfolgen von Anna/Ben kommen vor
    # gespeicherte Reihenfolge bleibt alphabetisch
    assert [c.name for c in races[0].candidates] == ["Anna", "Ben"]


def test_ballot_page_shows_all_candidates_and_invalid_last(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    html = client.post("/vote/start", data={"code": code}).text
    for name in ("Anna", "Ben", "Clara", "David"):
        assert name in html
    assert html.count("Ungültig wählen") == 2
