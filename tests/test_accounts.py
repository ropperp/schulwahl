"""Benutzer, Rollen, Passwörter, Zurücksetzen durch den Admin, QR-Adresse."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AuditLog, User
from app.security import hash_password, verify_password
from tests.conftest import admin_login, csrf, make_user


def new_client():
    return TestClient(app, follow_redirects=False)


def login_as(username, password):
    c = new_client()
    c.__enter__()
    return c, admin_login(c, username, password)


def password_from(html):
    return re.search(r"Einmal-Passwort</th><td><code>([^<]+)</code>", html).group(1)


# ------------------------------------------------------------ Passwörter

def test_password_hash_is_salted_and_verifies():
    a, b = hash_password("ein-langes-passwort"), hash_password("ein-langes-passwort")
    assert a != b and a.startswith("scrypt$") and "langes" not in a
    assert verify_password("ein-langes-passwort", a)
    assert not verify_password("anderes", a)
    assert not verify_password("x", "kaputt")


def test_admin_is_created_from_env_on_first_start(client, db):
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin and admin.role == "admin" and not admin.must_change
    assert admin.password_hash != "geheim-test"


# ------------------------------------------------------------ Login

def test_login_is_case_insensitive_and_error_is_generic(client, db):
    make_user(db)
    page = client.get("/admin/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    wrong_pw = client.post("/admin/login", data={"username": "leiter", "password": "falsch", "csrf_token": token})
    unknown = client.post("/admin/login", data={"username": "gibtsnicht", "password": "falsch", "csrf_token": token})
    assert wrong_pw.status_code == unknown.status_code == 401
    msg = lambda r: re.search(r'role="alert">([^<]+)<', r.text).group(1)  # noqa: E731
    assert msg(wrong_pw) == msg(unknown)  # verrät nicht, ob es den Benutzer gibt
    ok = client.post("/admin/login", data={"username": " LEITER ", "password": "wahlleiter-passwort", "csrf_token": token})
    assert ok.status_code == 303


def test_username_lockout_even_from_other_ips(client, db):
    make_user(db)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").text).group(1)
    from app.security import login_limiter
    for i in range(20):
        login_limiter.reset()  # simuliert wechselnde IP-Adressen
        client.post("/admin/login", data={"username": "leiter", "password": f"falsch{i}", "csrf_token": token})
    login_limiter.reset()
    r = client.post("/admin/login", data={"username": "leiter", "password": "wahlleiter-passwort", "csrf_token": token})
    assert r.status_code == 429


def test_login_audit_log(client, db):
    admin_login(client)
    actions = [a.action for a in db.scalars(select(AuditLog))]
    assert "login" in actions


# ------------------------------------------------------------ Rollen

ADMIN_ONLY_GET = ["/admin/settings", "/admin/users", "/admin/audit"]


def test_wahlleiter_can_run_elections_but_not_administrate(client, db, election):
    make_user(db)
    token = admin_login(client, "leiter", "wahlleiter-passwort")
    assert client.get("/admin").status_code == 200
    assert client.get(f"/admin/elections/{election.id}/codes").status_code == 200
    for path in ADMIN_ONLY_GET:
        assert client.get(path).status_code == 403, path
    assert client.post("/admin/users", data={"csrf_token": token, "username": "neu1"}).status_code == 403
    assert client.post("/admin/settings", data={"csrf_token": token, "school_name": "X"}).status_code == 403
    assert client.post(f"/admin/elections/{election.id}/delete", data={"csrf_token": token}).status_code == 403
    nav = client.get("/admin").text
    assert "/admin/users" not in nav and "/admin/settings" not in nav and "Mein Konto" in nav


def test_admin_sees_admin_menu(client):
    admin_login(client)
    nav = client.get("/admin").text
    assert "/admin/users" in nav and "/admin/settings" in nav and "/admin/audit" in nav


# ------------------------------------------------------------ Benutzer anlegen / zurücksetzen

def test_admin_creates_wahlleiter_who_must_change_password(client, db):
    token = admin_login(client)
    r = client.post("/admin/users", data={"csrf_token": token, "username": "Mueller"})
    assert r.status_code == 200
    temp = password_from(r.text)
    u = db.scalar(select(User).where(User.username == "mueller"))
    assert u.role == "wahlleiter" and u.must_change and u.password_hash != temp

    c, tok = login_as("mueller", temp)
    r = c.get("/admin")
    assert r.status_code == 303 and r.headers["location"] == "/admin/account"
    assert c.get("/admin/account").status_code == 200
    # alles andere bleibt gesperrt, bis das Passwort geändert wurde
    assert c.get("/admin/elections/new").status_code == 303

    bad = c.post("/admin/account", data={"csrf_token": tok, "current_password": temp, "new_password": "kurz",
                                         "new_password2": "kurz"})
    assert bad.status_code == 400
    ok = c.post("/admin/account", data={"csrf_token": tok, "current_password": temp,
                                        "new_password": "mein-neues-passwort", "new_password2": "mein-neues-passwort"})
    assert ok.status_code == 303
    assert c.get("/admin").status_code == 200
    c.__exit__(None, None, None)

    # Einmal-Passwort ist nun wertlos
    fresh = new_client()
    t = re.search(r'name="csrf_token" value="([^"]+)"', fresh.get("/admin/login").text).group(1)
    assert fresh.post("/admin/login", data={"username": "mueller", "password": temp, "csrf_token": t}).status_code == 401


@pytest.mark.parametrize("name", ["ab", "mit leerzeichen", "ümlaut", "x" * 31, "../etc", ""])
def test_invalid_usernames_rejected(client, name):
    token = admin_login(client)
    assert client.post("/admin/users", data={"csrf_token": token, "username": name}).status_code == 400


def test_duplicate_username_rejected(client, db):
    make_user(db, "leiter")
    token = admin_login(client)
    assert client.post("/admin/users", data={"csrf_token": token, "username": "leiter"}).status_code == 400
    assert client.post("/admin/users", data={"csrf_token": token, "username": "ADMIN"}).status_code == 400


def test_admin_resets_wahlleiter_password_and_kills_sessions(client, db):
    u = make_user(db)
    leiter, ltoken = login_as("leiter", "wahlleiter-passwort")
    assert leiter.get("/admin").status_code == 200

    token = admin_login(client)
    r = client.post(f"/admin/users/{u.id}/reset", data={"csrf_token": token})
    assert r.status_code == 200
    temp = password_from(r.text)

    assert leiter.get("/admin").status_code == 303  # laufende Sitzung beendet
    leiter.__exit__(None, None, None)
    c2, _ = login_as("leiter", temp)
    assert c2.get("/admin").headers["location"] == "/admin/account"  # muss neues Passwort wählen
    c2.__exit__(None, None, None)
    db.refresh(u)
    assert u.must_change
    assert "password_reset" in [a.action for a in db.scalars(select(AuditLog))]


def test_admin_cannot_reset_or_delete_admin_accounts(client, db):
    admin = db.scalar(select(User).where(User.username == "admin"))
    token = admin_login(client)
    assert client.post(f"/admin/users/{admin.id}/reset", data={"csrf_token": token}).status_code == 403
    assert client.post(f"/admin/users/{admin.id}/delete", data={"csrf_token": token}).status_code == 403
    assert verify_password("geheim-test", db.scalar(select(User.password_hash).where(User.username == "admin")))


def test_wahlleiter_cannot_reset_anyones_password(client, db):
    other = make_user(db, "anderer", "anderes-passwort-1")
    make_user(db)
    token = admin_login(client, "leiter", "wahlleiter-passwort")
    assert client.post(f"/admin/users/{other.id}/reset", data={"csrf_token": token}).status_code == 403


def test_admin_deletes_wahlleiter(client, db):
    u = make_user(db)
    leiter, _ = login_as("leiter", "wahlleiter-passwort")
    token = admin_login(client)
    assert client.post(f"/admin/users/{u.id}/delete", data={"csrf_token": token}).status_code == 303
    assert db.scalar(select(User).where(User.username == "leiter")) is None
    assert leiter.get("/admin").status_code == 303
    leiter.__exit__(None, None, None)


# ------------------------------------------------------------ Eigenes Passwort

def test_change_own_password_validations(client, db):
    token = admin_login(client)
    def change(**kw):
        data = {"csrf_token": token, "current_password": "geheim-test", "new_password": "neues-sicheres-pw",
                "new_password2": "neues-sicheres-pw"}
        data.update(kw)
        return client.post("/admin/account", data=data)
    assert change(current_password="falsch").status_code == 400
    assert change(new_password2="anders").status_code == 400
    assert change(new_password="kurz", new_password2="kurz").status_code == 400
    assert change(new_password="admin12345admin", new_password2="admin12345admin").status_code == 400  # enthält Benutzernamen
    assert change(new_password="geheim-test", new_password2="geheim-test").status_code == 400
    assert change(new_password="aaaaaaaaaaaa", new_password2="aaaaaaaaaaaa").status_code == 400
    assert change().status_code == 303
    assert client.get("/admin").status_code == 200  # aktuelle Sitzung bleibt gültig


def test_password_change_logs_out_other_sessions(client, db):
    token = admin_login(client)
    other, _ = login_as("admin", "geheim-test")
    assert other.get("/admin").status_code == 200
    client.post("/admin/account", data={"csrf_token": token, "current_password": "geheim-test",
                                        "new_password": "neues-sicheres-pw", "new_password2": "neues-sicheres-pw"})
    assert other.get("/admin").status_code == 303
    other.__exit__(None, None, None)
    assert client.get("/admin").status_code == 200


def test_logout_invalidates_copied_cookie(client, db):
    token = admin_login(client)
    stolen = dict(client.cookies)
    assert client.post("/admin/logout", data={"csrf_token": token}).status_code == 303
    thief = new_client()
    for k, v in stolen.items():
        thief.cookies.set(k, v)
    assert thief.get("/admin").status_code == 303


# ------------------------------------------------------------ QR-Adresse

@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x.at", "https://", "https://user:pw@wahl.at",
                                 "https://wahl.at/pfad", "https://wahl.at/?a=1", "wahl.at", "https://wa hl.at",
                                 "https://wahl.at:99999", "https://wahl<script>.at", "https://" + "a" * 300 + ".at"])
def test_invalid_qr_urls_rejected(client, url):
    from app import settings
    token = admin_login(client)
    r = client.post("/admin/settings", data={"csrf_token": token, "school_name": "S", "public_url": url})
    assert r.status_code == 303
    assert "wahl.test" in settings.get()["public_url"] or settings.get()["public_url"] == "http://wahl.test"


def test_qr_url_is_configurable_and_used_for_pdfs(client, db, election, monkeypatch):
    from app import routes_admin
    from tests.conftest import make_codes
    token = admin_login(client)
    r = client.post("/admin/settings", data={"csrf_token": token, "school_name": "S",
                                             "public_url": " https://wahl.htl1-klu.at/ "})
    assert r.status_code == 303
    assert "https://wahl.htl1-klu.at" in client.get("/admin/settings").text
    make_codes(db, election, 0, 2)
    seen = {}
    real = routes_admin.codes_pdf
    monkeypatch.setattr(routes_admin, "codes_pdf", lambda name, items, base: seen.update(base=base) or real(name, items, base))
    assert client.get(f"/admin/elections/{election.id}/codes.pdf").status_code == 200
    assert seen["base"] == "https://wahl.htl1-klu.at"
    assert "https://wahl.htl1-klu.at" in client.get(f"/admin/elections/{election.id}/codes").text


def test_wahlleiter_cannot_override_qr_url_via_query(client, db, election, monkeypatch):
    from app import routes_admin
    from tests.conftest import make_codes
    make_user(db)
    admin_login(client, "leiter", "wahlleiter-passwort")
    make_codes(db, election, 0, 1)
    seen = {}
    real = routes_admin.codes_pdf
    monkeypatch.setattr(routes_admin, "codes_pdf", lambda name, items, base: seen.update(base=base) or real(name, items, base))
    client.get(f"/admin/elections/{election.id}/codes.pdf?base_url=https://evil.example")
    assert seen["base"] == "http://wahl.test"


# ------------------------------------------------------------ Kommandozeile

def test_cli_reset_password(client, db, capsys):
    from app import manage
    assert manage.main(["reset-password", "admin"]) == 0
    out = capsys.readouterr().out
    temp = re.search(r"Neues Passwort: (\S+)", out).group(1)
    db.expire_all()
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin.must_change and verify_password(temp, admin.password_hash)
    assert manage.main(["reset-password", "gibtsnicht"]) == 1
    assert manage.main(["create-user", "zweiter", "--role", "admin"]) == 0
    assert manage.main(["create-user", "zweiter"]) == 1
    assert manage.main(["list-users"]) == 0


def test_bootstrap_without_password_leaves_backoffice_locked(client, db, monkeypatch):
    from app import accounts, config
    db.query(User).delete()
    db.commit()
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    assert accounts.bootstrap_admin() == "missing"
    assert "Es gibt noch keinen Benutzer" in client.get("/admin/login").text
