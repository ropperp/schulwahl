"""Mehrere Administratoren: Rolle beim Anlegen wählbar, Selbst- und Letzter-Admin-Schutz."""
import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import User
from tests.conftest import admin_login, make_user


def test_admin_can_create_second_admin_who_must_change_password(client, db):
    token = admin_login(client)
    r = client.post("/admin/users", data={"csrf_token": token, "username": "zweiter", "role": "admin"})
    assert r.status_code == 200
    temp = re.search(r"Einmal-Passwort</th><td><code>([^<]+)", r.text).group(1)
    u = db.scalar(select(User).where(User.username == "zweiter"))
    assert u.role == "admin" and u.must_change
    with TestClient(app, follow_redirects=False) as other:
        t2 = admin_login(other, "zweiter", temp)
        assert other.get("/admin/settings").headers["location"] == "/admin/account"  # erst Passwort wählen
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


def test_last_admin_cannot_be_deleted(client, db):
    me = db.scalar(select(User).where(User.username == "admin"))
    token = admin_login(client)
    assert client.post(f"/admin/users/{me.id}/delete", data={"csrf_token": token}).status_code == 403
    assert db.get(User, me.id) is not None


def test_wahlleiter_cannot_create_admins(client, db):
    make_user(db)
    token = admin_login(client, "leiter", "wahlleiter-passwort")
    assert client.post("/admin/users", data={"csrf_token": token, "username": "x1234", "role": "admin"}).status_code == 403
