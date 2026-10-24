"""Ersteinrichtung einer frischen Installation: Einrichtungscode, Admin-Passwort, Schulname, QR-Adresse."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import accounts, config, settings
from app.main import app
from app.models import AuditLog, Setting, User
from app.security import verify_password

GOOD = {"password": "ein-langes-passwort", "password2": "ein-langes-passwort", "school_name": "HTL 1 Lastenstraße",
        "public_url": "https://wahl.htl1-klagenfurt.at"}


@pytest.fixture
def fresh(monkeypatch):
    """Frische Installation: kein ADMIN_PASSWORD, keine Benutzer."""
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    with TestClient(app, follow_redirects=False) as c:
        yield c


def code_of(db):
    return db.get(Setting, "setup_code").value


def csrf_of(client):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/setup").text).group(1)


def submit(client, code, **over):
    data = {**GOOD, "setup_code": code, "csrf_token": csrf_of(client), **over}
    return client.post("/admin/setup", data=data)


def test_fresh_install_redirects_everything_to_setup(fresh, db):
    r = fresh.get("/admin")
    assert r.status_code == 303 and r.headers["location"] == "/admin/login"
    assert fresh.get("/admin/login").headers["location"] == "/admin/setup"
    page = fresh.get("/admin/setup")
    assert page.status_code == 200 and "Einrichtungscode" in page.text and "später" in page.text and "Einstellungen" in page.text
    assert "wahl.test" in page.text                            # Adresse ist vorbelegt (PUBLIC_URL aus der Umgebung)
    assert db.scalar(select(User)) is None
    assert re.fullmatch(r"[A-Z2-9]{8}", code_of(db))
    # auch ein Login-Versuch führt zur Einrichtung
    assert fresh.post("/admin/login", data={"username": "admin", "password": "x", "csrf_token": csrf_of(fresh)}).headers["location"] == "/admin/setup"


def test_setup_success_creates_admin_saves_settings_and_logs_in(fresh, db):
    code = code_of(db)
    r = submit(fresh, code.lower())                               # Kleinschreibung ist egal
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin.role == "admin" and not admin.must_change and verify_password(GOOD["password"], admin.password_hash)
    cfg = settings.get()
    assert cfg["school_name"] == "HTL 1 Lastenstraße" and cfg["public_url"] == "https://wahl.htl1-klagenfurt.at"
    assert db.get(Setting, "setup_code") is None                  # Code ist verbraucht
    assert fresh.get("/admin").status_code == 200                 # direkt angemeldet
    assert "setup_completed" in [a.action for a in db.scalars(select(AuditLog))]
    # danach ist die Einrichtung für alle gesperrt
    other = TestClient(app, follow_redirects=False)
    assert other.get("/admin/setup").headers["location"] == "/admin/login"
    assert other.post("/admin/setup", data={**GOOD, "setup_code": code, "csrf_token": "x"}).headers["location"] == "/admin/login"
    assert db.query(User).count() == 1


def test_code_may_be_typed_with_hyphen(fresh, db):
    code = code_of(db)
    assert submit(fresh, f"{code[:4]}-{code[4:]}").status_code == 303


def test_wrong_code_is_refused_and_limited(fresh, db):
    for _ in range(5):
        assert submit(fresh, "AAAAAAAA").status_code == 403
    assert db.scalar(select(User)) is None
    assert submit(fresh, code_of(db)).status_code == 429          # Sperre pro IP gilt auch für den richtigen Code
    assert "setup_failed" in [a.action for a in db.scalars(select(AuditLog))]


@pytest.mark.parametrize("over,message", [
    ({"password2": "anders-anders-1"}, "stimmen nicht"),
    ({"password": "kurz", "password2": "kurz"}, "mindestens 10"),
    ({"school_name": "  "}, "Schulnamen"),
    ({"public_url": "javascript:alert(1)"}, "https://"),
    ({"public_url": "https://wahl.at/pfad"}, "Pfad"),
])
def test_invalid_input_creates_nothing(fresh, db, over, message):
    r = submit(fresh, code_of(db), **over)
    assert r.status_code == 400 and message in r.text
    assert db.scalar(select(User)) is None and db.get(Setting, "setup_code") is not None


def test_setup_needs_csrf_token(fresh, db):
    r = fresh.post("/admin/setup", data={**GOOD, "setup_code": code_of(db), "csrf_token": "falsch"})
    assert r.status_code == 403 and db.scalar(select(User)) is None


def test_code_is_stable_across_restarts(fresh, db):
    first = code_of(db)
    assert accounts.ensure_setup_code() == first and accounts.ensure_setup_code() == first


def test_cli_shows_code_until_setup_is_done(fresh, db, capsys):
    from app import manage
    assert manage.main(["setup-code"]) == 0
    assert f"{code_of(db)[:4]}-{code_of(db)[4:]}" in capsys.readouterr().out
    submit(fresh, code_of(db))
    assert manage.main(["setup-code"]) == 1


def test_optional_env_password_skips_setup(client):
    """Mit ADMIN_PASSWORD (Tests, automatische Installation) gibt es keine Ersteinrichtung."""
    assert client.get("/admin/login").status_code == 200
    assert client.get("/admin/setup").headers["location"] == "/admin/login"


def test_login_page_has_no_obsolete_hint(client):
    assert "ADMIN_PASSWORD" not in client.get("/admin/login").text
