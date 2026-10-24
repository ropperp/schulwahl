import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["ADMIN_PASSWORD"] = "geheim-test"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["PUBLIC_URL"] = "http://wahl.test"
os.environ["TZ"] = "Europe/Vienna"

import re  # noqa: E402
from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Candidate, Code, Department, Election, Race  # noqa: E402
from app.security import code_limiter, login_limiter, user_limiter  # noqa: E402
from app.services import generate_codes  # noqa: E402
from app.timeutil import utcnow  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    code_limiter.reset()
    login_limiter.reset()
    user_limiter.reset()
    from app import settings
    settings.invalidate()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client():
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def election(db):
    """Laufende Wahl mit 2 Abteilungen: Schulsprecher (alle) + je ein Abteilungssprecher."""
    e = Election(name="Testwahl", start_at=utcnow() - timedelta(hours=1), end_at=utcnow() + timedelta(hours=1))
    e.departments = [Department(name="Elektrotechnik"), Department(name="Maschinenbau")]
    db.add(e)
    db.flush()
    el, ma = e.departments
    school = Race(title="Schulsprecher/in", position=0)
    r_el = Race(title="Abteilungssprecher/in", department_id=el.id, position=1)
    r_ma = Race(title="Abteilungssprecher/in", department_id=ma.id, position=2)
    e.races += [school, r_el, r_ma]
    db.flush()
    school.candidates = [Candidate(name="Anna"), Candidate(name="Ben")]
    r_el.candidates = [Candidate(name="Clara"), Candidate(name="David")]
    r_ma.candidates = [Candidate(name="Eva")]
    db.commit()
    return e


def make_codes(db, election, dept_index=0, n=3):
    generate_codes(db, election, election.departments[dept_index], n)
    return [c.code for c in db.query(Code).filter(Code.department_id == election.departments[dept_index].id)]


def ballot_choices(election, db, code, school=0, dept=0):
    """Formulardaten: Index in der Kandidatenliste oder 'invalid'."""
    from app.services import ballot_for, lookup_code

    _, races = ballot_for(db, lookup_code(db, code))
    data = {"code": code}
    for r, pick in zip(races, (school, dept)):
        data[f"race_{r.id}"] = "invalid" if pick == "invalid" else str(r.candidates[pick].id)
    return data


def admin_login(client, username="admin", password="geheim-test"):
    page = client.get("/admin/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    r = client.post("/admin/login", data={"username": username, "password": password, "csrf_token": token})
    assert r.status_code == 303
    return csrf(client)


def make_user(db, username="leiter", password="wahlleiter-passwort", role="wahlleiter", must_change=False):
    from app.models import User
    from app.security import hash_password
    u = User(username=username, role=role, password_hash=hash_password(password), must_change=must_change)
    db.add(u)
    db.commit()
    return u


def vote_count(db):
    """Alle abgegebenen Stimmen: in der Urne wartende plus bereits ausgezählte."""
    from sqlalchemy import func, select
    from app.models import PendingBallot, Vote
    return (db.scalar(select(func.count()).select_from(Vote)) or 0) + \
           (db.scalar(select(func.count()).select_from(PendingBallot)) or 0)


def csrf(client):
    page = client.get("/admin/account")  # erreichbar für jede angemeldete Person, auch vor dem ersten Passwortwechsel
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def decide_all(client, db, election, token, answer="no"):
    """Wahlleitung entscheidet bei allen Wahlgängen (Voraussetzung zum Veröffentlichen)."""
    from app.models import Race
    for race in db.query(Race).filter(Race.election_id == election.id):
        r = client.post(f"/admin/races/{race.id}/runoff-decision", data={"csrf_token": token, "answer": answer})
        assert r.status_code == 303
