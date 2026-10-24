"""Entwickler-Notfallzugang (versteckt, nur als Hash hinterlegt) und zweiter Administrator."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import developer_access, manage, passwords
from app.main import app
from app.models import HIDDEN_USERNAME, AuditLog, User
from tests.conftest import admin_login, csrf, make_user

DEV_NAME = "Geheimer-Entwickler-Name-42"
DEV_PASSWORD = "ein-sehr-langes-zufaelliges-passwort-7Qz"


@pytest.fixture
def dev(monkeypatch):
    """Entwicklerzugang aktiv mit festen Test-Hashes (der Klartext steht nur hier im Test)."""
    monkeypatch.setattr(developer_access, "USERNAME_HASH", passwords.hash_password(DEV_NAME.lower()))
    monkeypatch.setattr(developer_access, "PASSWORD_HASH", passwords.hash_password(DEV_PASSWORD))


def login_dev(name=DEV_NAME, password=DEV_PASSWORD):
    c = TestClient(app, follow_redirects=False)
    c.__enter__()
    tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/admin/login").text).group(1)
    r = c.post("/admin/login", data={"username": name, "password": password, "csrf_token": tok})
    return c, r


# ------------------------------------------------------------ Hash-Kommando

def test_hash_command_creates_verifiable_hashes():
    answers = iter([DEV_NAME, DEV_NAME, DEV_PASSWORD, DEV_PASSWORD])
    lines = []
    assert passwords.main(ask=lambda _p: next(answers), out=lines.append) == 0
    text = "\n".join(lines)
    user_hash = re.search(r'USERNAME_HASH = "([^"]+)"', text).group(1)
    pw_hash = re.search(r'PASSWORD_HASH = "([^"]+)"', text).group(1)
    assert passwords.verify_password(DEV_NAME.lower(), user_hash) and passwords.verify_password(DEV_PASSWORD, pw_hash)
    assert DEV_NAME not in text and DEV_PASSWORD not in text and DEV_NAME.lower() not in text   # Klartext erscheint nie in der Ausgabe


@pytest.mark.parametrize("answers", [
    ["kurz", "kurz", DEV_PASSWORD, DEV_PASSWORD],                    # Benutzername zu kurz
    [DEV_NAME, "anderer-name-1234", DEV_PASSWORD, DEV_PASSWORD],      # Wiederholung stimmt nicht
    [DEV_NAME, DEV_NAME, "zu-kurz", "zu-kurz"],                       # Passwort zu kurz
    [DEV_NAME, DEV_NAME, "a" * 20, "a" * 20],                         # zu wenig verschiedene Zeichen
    [DEV_NAME, DEV_NAME, DEV_PASSWORD, "anderes-passwort-12345"],     # Wiederholung stimmt nicht
    ["x" * 51, "x" * 51, DEV_PASSWORD, DEV_PASSWORD],                 # Benutzername zu lang (wird beim Login auf 50 gekürzt)
])
def test_hash_command_rejects_weak_or_inconsistent_input(answers):
    it, lines = iter(answers), []
    assert passwords.main(ask=lambda _p: next(it), out=lines.append) == 1
    assert "USERNAME_HASH" not in "\n".join(lines)


def test_hash_command_runs_without_the_web_app():
    """`python -m app.passwords` darf nur die Standardbibliothek brauchen (läuft auf jedem Rechner)."""
    import subprocess
    import sys
    code = "import sys; import app.passwords; assert not any(m in sys.modules for m in ('fastapi','sqlalchemy')), 'zieht Web-Teile herein'"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).parent.parent))
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------ ohne Hinterlegung: aus

def test_inactive_by_default(client, db):
    assert developer_access.USERNAME_HASH == "" and not developer_access.enabled()   # im Repository ist (noch) nichts hinterlegt oder Test-Hashes
    c, r = login_dev("irgendwer-12345", "irgendein-passwort-12345")
    assert r.status_code == 401
    assert db.scalar(select(User).where(User.username == HIDDEN_USERNAME)) is None


def test_switch_off_in_env(dev, monkeypatch, client):
    from app import config
    monkeypatch.setattr(config, "DEVELOPER_ACCESS", "off")
    assert not developer_access.enabled()
    assert login_dev()[1].status_code == 401


# ------------------------------------------------------------ Anmeldung

def test_login_works_and_gives_admin_rights(dev, client, db):
    c, r = login_dev()
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert c.get("/admin/settings").status_code == 200 and c.get("/admin/users").status_code == 200    # Administrator-Rechte
    user = db.scalar(select(User).where(User.username == HIDDEN_USERNAME))
    assert user.role == "admin" and user.password_hash == "!" and not user.must_change
    # Groß-/Kleinschreibung des Benutzernamens ist egal
    assert login_dev(DEV_NAME.upper())[1].status_code == 303


@pytest.mark.parametrize("name,password", [(DEV_NAME, "falsches-passwort-123456"), ("anderer-name-123456", DEV_PASSWORD),
                                           (DEV_NAME + "x", DEV_PASSWORD), ("", DEV_PASSWORD)])
def test_wrong_credentials_are_refused(dev, client, db, name, password):
    c, r = login_dev(name, password)
    assert r.status_code == 401 and "falsch" in r.text
    assert db.scalar(select(User).where(User.username == HIDDEN_USERNAME)) is None


def test_internal_name_cannot_be_used_to_log_in(dev, client, db):
    """Die interne Zeile hat keinen gültigen Hash: Name "_entwickler" + irgendein Passwort kommt nie hinein."""
    login_dev()  # legt die Zeile an
    for pw in ("!", "", DEV_PASSWORD, "x" * 30):
        c, r = login_dev(HIDDEN_USERNAME, pw)
        assert r.status_code == 401


def test_dev_login_is_rate_limited_like_every_login(dev, client):
    from app.security import login_limiter
    for _ in range(5):
        assert login_dev(DEV_NAME, "falsch-falsch-falsch-1")[1].status_code == 401
    assert login_dev()[1].status_code == 429
    login_limiter.reset()


# ------------------------------------------------------------ unsichtbar, aber nicht unprotokolliert

def test_name_never_appears_anywhere(dev, client, db):
    c, _ = login_dev()
    tok = csrf(c)
    c.post("/admin/account", data={"csrf_token": tok, "current_password": DEV_PASSWORD, "new_password": "neues-passwort-123",
                                   "new_password2": "neues-passwort-123"})
    pages = [c.get(p).text for p in ("/admin", "/admin/users", "/admin/audit", "/admin/account", "/admin/settings")]
    for text in pages:
        assert DEV_NAME.lower() not in text.lower() and DEV_PASSWORD not in text
        assert HIDDEN_USERNAME not in text                       # auch der interne Name erscheint nirgends
    assert "Entwicklerzugang" in pages[0] and "Entwicklerzugang" in pages[2]   # Menü und Protokoll sagen ehrlich, wer handelt


def test_hidden_from_user_list_and_cli(dev, client, db, capsys):
    login_dev()
    admin = admin_login(client)
    html = client.get("/admin/users").text
    assert "Entwicklerzugang" not in html and HIDDEN_USERNAME not in html
    assert manage.main(["list-users"]) == 0
    out = capsys.readouterr().out
    assert HIDDEN_USERNAME not in out and "admin" in out
    assert manage.main(["reset-password", HIDDEN_USERNAME]) == 1       # nicht über die Kommandozeile angreifbar
    assert admin


def test_cannot_be_reset_or_deleted_from_the_backoffice(dev, client, db):
    login_dev()
    hidden = db.scalar(select(User).where(User.username == HIDDEN_USERNAME))
    token = admin_login(client)
    assert client.post(f"/admin/users/{hidden.id}/reset", data={"csrf_token": token}).status_code == 404
    assert client.post(f"/admin/users/{hidden.id}/delete", data={"csrf_token": token}).status_code == 404
    assert db.get(User, hidden.id).password_hash == "!"


def test_dev_cannot_change_password_in_app(dev, client):
    c, _ = login_dev()
    r = c.post("/admin/account", data={"csrf_token": csrf(c), "current_password": DEV_PASSWORD, "new_password": "neues-passwort-123",
                                       "new_password2": "neues-passwort-123"})
    assert r.status_code == 403 and "kein änderbares Passwort" in r.text


def test_actions_are_attributed_in_the_protocol(dev, client, db):
    c, _ = login_dev()
    c.post("/admin/users", data={"csrf_token": csrf(c), "username": "neuer-leiter", "role": "wahlleiter"})
    rows = {(a.username, a.action) for a in db.scalars(select(AuditLog))}
    assert (HIDDEN_USERNAME, "login") in rows and (HIDDEN_USERNAME, "user_created") in rows


def test_failed_attempt_with_right_name_does_not_leak_the_name_into_the_protocol(dev, client, db):
    login_dev(DEV_NAME, "falsch-falsch-falsch-1")
    login_dev("normaler-tippfehler", "x")
    details = [a.detail for a in db.scalars(select(AuditLog).where(AuditLog.action == "login_failed"))]
    assert not any(DEV_NAME.lower() in d.lower() for d in details)
    assert "Entwicklerzugang: falsches Passwort" in details and "Benutzername: normaler-tippfehler" in details


def test_logout_ends_dev_session(dev, client):
    c, _ = login_dev()
    assert c.post("/admin/logout", data={"csrf_token": csrf(c)}).status_code == 303
    assert c.get("/admin").status_code == 303


# ------------------------------------------------------------ zweiter Administrator

def test_admin_can_create_second_admin_who_must_change_password(client, db):
    token = admin_login(client)
    r = client.post("/admin/users", data={"csrf_token": token, "username": "zweiter", "role": "admin"})
    assert r.status_code == 200
    temp = re.search(r"Einmal-Passwort</th><td><code>([^<]+)", r.text).group(1)
    u = db.scalar(select(User).where(User.username == "zweiter"))
    assert u.role == "admin" and u.must_change
    other = TestClient(app, follow_redirects=False)
    other.__enter__()
    t2 = admin_login(other, "zweiter", temp)
    assert other.get("/admin/settings").headers["location"] == "/admin/account"      # erst Passwort wählen
    other.post("/admin/account", data={"csrf_token": t2, "current_password": temp, "new_password": "mein-eigenes-passwort-1",
                                       "new_password2": "mein-eigenes-passwort-1"})
    assert other.get("/admin/settings").status_code == 200 and other.get("/admin/users").status_code == 200


def test_unknown_role_falls_back_to_wahlleiter(client, db):
    token = admin_login(client)
    client.post("/admin/users", data={"csrf_token": token, "username": "dritter", "role": "superuser"})
    assert db.scalar(select(User.role).where(User.username == "dritter")) == "wahlleiter"


def test_admin_can_reset_and_delete_other_admin_but_not_self_or_last(client, db):
    second = make_user(db, "zweiter", "passwort-zweiter-123", role="admin")
    me = db.scalar(select(User).where(User.username == "admin"))
    token = admin_login(client)
    assert client.post(f"/admin/users/{me.id}/reset", data={"csrf_token": token}).status_code == 403     # sich selbst nie
    assert client.post(f"/admin/users/{me.id}/delete", data={"csrf_token": token}).status_code == 403
    assert client.post(f"/admin/users/{second.id}/reset", data={"csrf_token": token}).status_code == 200
    assert client.post(f"/admin/users/{second.id}/delete", data={"csrf_token": token}).status_code == 303
    assert db.scalar(select(User).where(User.username == "zweiter")) is None


def test_last_visible_admin_cannot_be_deleted_by_dev(dev, client, db):
    me = db.scalar(select(User).where(User.username == "admin"))
    c, _ = login_dev()
    assert c.post(f"/admin/users/{me.id}/delete", data={"csrf_token": csrf(c)}).status_code == 403
    assert db.get(User, me.id) is not None


def test_wahlleiter_cannot_create_admins(client, db):
    make_user(db)
    token = admin_login(client, "leiter", "wahlleiter-passwort")
    assert client.post("/admin/users", data={"csrf_token": token, "username": "x1234", "role": "admin"}).status_code == 403
