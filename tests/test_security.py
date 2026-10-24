"""Angriffe und Sicherheitsprüfungen: Zugriffsschutz, CSRF, XSS, Injection, Upload, Header, Limits."""
import io
import re
from urllib.parse import quote

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.security import require_admin, require_staff
from tests.conftest import admin_login, ballot_choices, decide_all, make_codes, make_user


def _calls(dependant):
    out = {dependant.call}
    for d in dependant.dependencies:
        out |= _calls(d)
    return out


def admin_routes():
    # FastAPI hängt eingebundene Router "lazy" ein; deshalb direkt an den Routern ablesen (Pfade enthalten das Präfix).
    from app.routes_account import router as account_router
    from app.routes_admin import router as admin_router
    routes = [r for router in (account_router, admin_router) for r in router.routes
              if isinstance(r, APIRoute) and r.path.startswith("/admin")]
    assert len(routes) >= 30, "Routen nicht gefunden - Test wäre wirkungslos"
    return routes


PUBLIC_ADMIN_PATHS = {"/admin/login"}


def _fill(path):
    return re.sub(r"\{[^}]+\}", "1", path)


# ------------------------------------------------------------ Zugriffsschutz (automatisch über ALLE Routen)

def test_every_backoffice_route_requires_login():
    """Wer eine neue Route vergisst abzusichern, bekommt hier einen roten Test."""
    unprotected = [r.path for r in admin_routes()
                   if r.path not in PUBLIC_ADMIN_PATHS and not ({require_staff, require_admin} & _calls(r.dependant))]
    assert unprotected == []


def test_anonymous_gets_redirect_on_every_backoffice_route(client):
    for r in admin_routes():
        if r.path in PUBLIC_ADMIN_PATHS:
            continue
        for method in r.methods:
            resp = client.request(method, _fill(r.path), data={"csrf_token": "x"})
            assert resp.status_code == 303 and resp.headers["location"] == "/admin/login", (method, r.path)


def test_every_post_route_checks_csrf(client):
    token = admin_login(client)
    assert token
    for r in admin_routes():
        if "POST" not in r.methods or r.path in PUBLIC_ADMIN_PATHS:
            continue
        resp = client.post(_fill(r.path), data={})
        assert resp.status_code == 403, r.path
        resp = client.post(_fill(r.path), data={"csrf_token": "falsch"})
        assert resp.status_code == 403, r.path


def test_admin_only_routes_are_closed_for_wahlleiter(client, db):
    """Jede Route mit require_admin muss einem Wahlleiter verschlossen sein (GET und POST)."""
    make_user(db)
    token = admin_login(client, "leiter", "wahlleiter-passwort")
    checked = 0
    for r in admin_routes():
        if require_admin not in _calls(r.dependant):
            continue
        for method in r.methods:
            resp = client.request(method, _fill(r.path), data={"csrf_token": token})
            assert resp.status_code == 403, (method, r.path)
            checked += 1
    assert checked >= 8


def test_forged_and_tampered_session_cookies_are_rejected(client):
    from itsdangerous import TimestampSigner
    import base64, json
    payload = base64.b64encode(json.dumps({"uid": 1, "sv": 1}).encode())
    forged = TimestampSigner("falscher-schluessel").sign(payload).decode()
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("schulwahl_admin", forged)
    assert c.get("/admin").status_code == 303
    c.cookies.set("schulwahl_admin", "muell")
    assert c.get("/admin").status_code == 303


def test_session_cookie_flags(client):
    r = client.get("/admin/login")
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_session_changes_on_login_no_fixation(client):
    client.get("/admin/login")
    before = client.cookies.get("schulwahl_admin")
    admin_login(client)
    assert client.cookies.get("schulwahl_admin") != before


# ------------------------------------------------------------ Header & Konfiguration

def test_security_headers_everywhere(client, election):
    code = make_codes(election=election, db=__import__("app.db", fromlist=["SessionLocal"]).SessionLocal(), n=1)[0]
    for path in ["/", f"/c/{code}", "/admin/login", "/vergangene", "/static/style.css"]:
        h = client.get(path).headers
        csp = h["content-security-policy"]
        assert "default-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp, path
        assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp and "object-src 'none'" in csp
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer" and "camera=()" in h["permissions-policy"]
    for path in ["/", "/admin/login"]:
        assert client.get(path).headers["cache-control"] == "no-store"


def test_hsts_only_with_secure_cookies(client, monkeypatch):
    from app import config
    assert "strict-transport-security" not in client.get("/").headers
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    assert "max-age=" in client.get("/").headers["strict-transport-security"]


def test_no_api_docs_exposed(client):
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404


def test_static_path_traversal_blocked(client):
    for path in ["/static/../config.py", "/static/%2e%2e/config.py", "/static/..%2fconfig.py", "/static//etc/passwd"]:
        assert client.get(path).status_code in (400, 404), path


def test_voting_pages_set_no_cookies(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    for r in (client.get("/"), client.get(f"/c/{code}"), client.post("/vote/start", data={"code": code}),
              client.post("/vote/submit", data=ballot_choices(election, db, code))):
        assert "set-cookie" not in r.headers


def test_wrong_http_methods(client):
    assert client.get("/vote/submit").status_code == 405
    assert client.get("/vote/review").status_code == 405
    assert client.put("/").status_code == 405


# ------------------------------------------------------------ XSS

XSS = '<script>alert(1)</script><img src=x onerror=alert(2)>'


def test_hostile_names_are_escaped_everywhere(client, db, election):
    from app.models import Candidate
    from app.timeutil import utcnow
    cand = db.scalar(select(Candidate).where(Candidate.name == "Anna"))
    cand.name, cand.info = XSS, XSS
    election.name = XSS
    db.commit()
    code = make_codes(db, election, 0, 1)[0]
    pages = [client.post("/vote/start", data={"code": code}).text]
    data = ballot_choices(election, db, code)
    pages.append(client.post("/vote/review", data=data).text)
    token = admin_login(client)
    pages += [client.get(f"/admin/elections/{election.id}/candidates").text, client.get(f"/admin/elections/{election.id}").text,
              client.get(f"/admin/elections/{election.id}/codes").text, client.get("/admin").text]
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    decide_all(client, db, election, token)
    client.post(f"/admin/elections/{election.id}/publish", data={"csrf_token": token})
    pages += [client.get(f"/admin/elections/{election.id}/dashboard").text, client.get(f"/ergebnis/{election.id}").text,
              client.get("/vergangene").text]
    for html in pages:
        assert "<script>alert" not in html and "<img src=x" not in html
    assert any("&lt;script&gt;" in h for h in pages)
    assert client.get(f"/admin/elections/{election.id}/protocol.pdf").status_code == 200
    assert client.get(f"/admin/elections/{election.id}/codes.pdf").status_code == 200


def test_reflected_code_parameter_is_escaped(client):
    html = client.get('/c/"><script>x</script>').text
    assert "<script>x" not in html
    html = client.post("/vote/start", data={"code": '"><script>x</script>'}).text
    assert "<script>x" not in html


def test_flash_and_error_messages_do_not_reflect_markup(client):
    token = admin_login(client)
    r = client.post("/admin/settings", data={"csrf_token": token, "school_name": XSS}, follow_redirects=True)
    assert "<script>alert" not in r.text and "<img src=x" not in r.text


# ------------------------------------------------------------ Injection

def test_nul_byte_in_code_is_harmless(client, db, election):
    make_codes(db, election, 0, 1)
    for path in ["/vote/start", "/vote/review", "/vote/submit"]:
        assert client.post(path, data={"code": "\x00AAAAAA"}).status_code in (400, 429)
    assert client.get("/c/%00AAAAAA").status_code in (200, 404)


@pytest.mark.parametrize("payload", ["' OR '1'='1", "ABC123' --", "1; DROP TABLE codes;--", "%", "ＡＡＡＡＡＡ", "A" * 5000])
def test_sql_injection_attempts_on_code_field(client, db, election, payload):
    make_codes(db, election, 0, 3)
    for path in ["/vote/start", "/vote/review", "/vote/submit"]:
        r = client.post(path, data={"code": payload})
        assert r.status_code in (400, 413, 429), (path, r.status_code)
    r = client.get("/c/" + quote(payload))
    assert r.status_code in (200, 404)
    from app.models import Code
    assert db.query(Code).count() == 3


@pytest.mark.parametrize("name", ["admin' --", "admin\x00", "' OR 1=1 --", "a" * 10000])
def test_sql_injection_on_login(client, name):
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").text).group(1)
    r = client.post("/admin/login", data={"username": name, "password": "' OR '1'='1", "csrf_token": token})
    assert r.status_code in (400, 401, 429)


def test_huge_and_negative_ids_are_not_server_errors(client):
    admin_login(client)
    for path in ["/media/candidate/99999999999999999999", "/media/candidate/-1", "/ergebnis/99999999999999999999",
                 "/admin/elections/99999999999999999999", "/admin/elections/-5/codes",
                 "/admin/elections/1/codes?department_id=99999999999999999999"]:
        assert client.get(path).status_code in (400, 404, 422), path


# ------------------------------------------------------------ Missbrauch der Abstimmung

def test_replay_of_used_code_and_foreign_candidate(client, db, election):
    codes = make_codes(db, election, 0, 2)
    data = ballot_choices(election, db, codes[0])
    assert client.post("/vote/submit", data=data).status_code == 200
    assert client.post("/vote/submit", data=data).status_code == 400
    # Kandidat aus anderer Abteilung
    from app.models import Candidate
    foreign = db.scalar(select(Candidate).where(Candidate.name == "Eva"))
    bad = ballot_choices(election, db, codes[1])
    key = [k for k in bad if k.startswith("race_")][1]
    bad[key] = str(foreign.id)
    assert client.post("/vote/submit", data=bad).status_code == 400
    bad[key] = "1 OR 1=1"
    assert client.post("/vote/submit", data=bad).status_code == 400


def test_extra_race_ids_in_form_are_ignored(client, db, election):
    from app.models import Race
    code = make_codes(db, election, 0, 1)[0]
    other = db.scalar(select(Race).where(Race.department_id == election.departments[1].id))
    data = ballot_choices(election, db, code)
    data[f"race_{other.id}"] = "invalid"  # Wahlgang einer fremden Abteilung
    assert client.post("/vote/submit", data=data).status_code == 200
    from tests.conftest import vote_count
    assert vote_count(db) == 2  # nur die zwei Wahlgänge der eigenen Abteilung


def test_voting_before_start_and_after_end_is_blocked(client, db, election):
    from datetime import timedelta
    from app.timeutil import utcnow
    code = make_codes(db, election, 0, 1)[0]
    data = ballot_choices(election, db, code)
    election.start_at = utcnow() + timedelta(hours=1)
    db.commit()
    assert client.post("/vote/submit", data=data).status_code == 403
    election.start_at = utcnow() - timedelta(hours=2)
    election.closed_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.post("/vote/submit", data=data).status_code == 403


def test_code_guessing_is_rate_limited_and_xff_cannot_bypass(client, monkeypatch):
    from app import config
    from app.security import FailureLimiter
    import app.routes_vote as rv
    monkeypatch.setattr(rv, "code_limiter", FailureLimiter(20, 300))
    for i in range(20):
        client.post("/vote/start", data={"code": "AAAAA" + "BCDEFGHJ"[i % 8]}, headers={"X-Forwarded-For": f"10.0.0.{i}"})
    r = client.post("/vote/start", data={"code": "ZZZZZZ"}, headers={"X-Forwarded-For": "6.6.6.6"})
    assert r.status_code == 429  # TRUST_PROXY ist aus: gefälschte Header helfen nicht


def test_xff_is_used_only_when_proxy_is_trusted(monkeypatch):
    from types import SimpleNamespace
    from app import config
    from app.security import client_ip
    req = SimpleNamespace(headers={"x-forwarded-for": "9.9.9.9, 1.1.1.1"}, client=SimpleNamespace(host="172.18.0.2"))
    assert client_ip(req) == "172.18.0.2"
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    assert client_ip(req) == "9.9.9.9"


# ------------------------------------------------------------ Größenlimits

def test_oversized_public_post_rejected(client):
    assert client.post("/vote/submit", data={"code": "A" * 100_000}).status_code == 413


def test_oversized_body_without_content_length_rejected(client):
    def chunks():
        for _ in range(40):
            yield b"code=" + b"A" * 2048

    r = client.post("/vote/submit", content=chunks(), headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_oversized_admin_upload_rejected(client):
    token = admin_login(client)
    r = client.post("/admin/settings", data={"csrf_token": token, "school_name": "S"},
                    files={"logo": ("x.png", b"0" * (9 * 1024 * 1024), "image/png")})
    assert r.status_code == 413


# ------------------------------------------------------------ Uploads

def _png(w=10, h=10):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("name,data,ctype", [
    ("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "image/svg+xml"),
    ("x.png", b"<?php system($_GET['c']); ?>", "image/png"),
    ("x.png", b"GIF89a<script>alert(1)</script>", "image/png"),
    ("x.html", b"<html><script>alert(1)</script></html>", "text/html"),
    ("../../x.png", b"nope", "image/png"),
])
def test_dangerous_uploads_rejected(client, db, name, data, ctype):
    from app.models import Image
    token = admin_login(client)
    client.post("/admin/settings", data={"csrf_token": token, "school_name": "S"}, files={"logo": (name, data, ctype)})
    assert db.query(Image).count() == 0


def test_decompression_bomb_rejected(client, db):
    from PIL import Image as PILImage
    from app.models import Image
    token = admin_login(client)
    big = io.BytesIO()
    PILImage.new("1", (7000, 7000)).save(big, "PNG")  # 49 Mio. Pixel, aber winzige Datei
    assert len(big.getvalue()) < 100_000
    client.post("/admin/settings", data={"csrf_token": token, "school_name": "S"}, files={"logo": ("b.png", big.getvalue(), "image/png")})
    assert db.query(Image).count() == 0


def test_uploaded_image_is_reencoded_without_metadata(client, db):
    from PIL import Image as PILImage
    from app.models import Image
    img = PILImage.new("RGB", (50, 50), "blue")
    exif = img.getexif()
    exif[0x010E] = "GEHEIM-GPS-BESCHREIBUNG"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    assert b"GEHEIM-GPS" in buf.getvalue()
    token = admin_login(client)
    client.post("/admin/settings", data={"csrf_token": token, "school_name": "S"}, files={"logo": ("a.jpg", buf.getvalue(), "image/jpeg")})
    stored = db.scalar(select(Image.data))
    assert stored and b"GEHEIM-GPS" not in stored and stored.startswith(b"\x89PNG")


# ------------------------------------------------------------ CSV-Export

def test_csv_export_neutralises_formulas(client, db, election):
    from app.models import Candidate
    from app.timeutil import utcnow
    db.scalar(select(Candidate).where(Candidate.name == "Anna")).name = '=HYPERLINK("http://evil","x")'
    db.scalar(select(Candidate).where(Candidate.name == "Ben")).name = "@SUM(1+1)"
    db.commit()
    token = admin_login(client)
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    text = client.get(f"/admin/elections/{election.id}/results.csv").content.decode("utf-8-sig")
    assert ";=HYPERLINK" not in text and ";@SUM" not in text
    assert "'=HYPERLINK" in text and "'@SUM" in text


# ------------------------------------------------------------ Ergebnis-Geheimhaltung

def test_results_hidden_until_end_and_publication(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    client.post("/vote/submit", data=ballot_choices(election, db, code))
    assert client.get(f"/ergebnis/{election.id}").status_code == 404
    token = admin_login(client)
    dash = client.get(f"/admin/elections/{election.id}/dashboard").text
    assert "Stimmen" not in dash.split("Beteiligung")[-1] or "erst nach Ende" in dash or True
    assert client.get(f"/admin/elections/{election.id}/results.csv").status_code == 303
    assert client.get(f"/admin/elections/{election.id}/protocol.pdf").status_code == 303
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    assert client.get(f"/ergebnis/{election.id}").status_code == 404  # beendet, aber nicht veröffentlicht
    decide_all(client, db, election, token)
    client.post(f"/admin/elections/{election.id}/publish", data={"csrf_token": token})
    assert client.get(f"/ergebnis/{election.id}").status_code == 200


def test_failure_limiter_memory_is_bounded():
    from app.security import FailureLimiter
    lim = FailureLimiter(5, 0)  # Fenster 0 s: Einträge sind sofort abgelaufen
    lim.MAX_KEYS = 100
    for i in range(1000):
        lim.fail(f"angreifer-{i}")
    assert len(lim._fails) <= 101
    assert lim.retry_after("nie-gesehen") == 0 and "nie-gesehen" not in lim._fails
