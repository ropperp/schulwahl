"""Design der HTL-1-Richtlinien: Footer, Schriften, Umschalter, Abteilungsfarben, CSP-Sauberkeit, Schwarzweiß-PDFs."""
import io
import re

import pytest
from PIL import Image
from sqlalchemy import select

from app.web import dept_class
from tests.conftest import admin_login, ballot_choices, decide_all, make_codes

PAGES = ["/", "/vergangene", "/admin/login"]


def test_footer_links_and_credit(client):
    html = client.get("/").text
    for url in ("https://htl1-klagenfurt.at", "https://htl1-klagenfurt.at/kontakt/", "https://htl1-klagenfurt.at/impressum",
                "https://htl1-klagenfurt.at/datenschutz/"):
        assert f'href="{url}"' in html
    assert "Designed with" in html and "Patrick" in html
    assert "Lastenstraße 1" in html and "+43 463 56224" in html and "office@htl1-klagenfurt.at" in html
    assert 'rel="noopener"' in html
    assert re.search(r"© \d{4} HTL 1 Lastenstraße", html)


def test_theme_toggle_and_script_in_head(client):
    html = client.get("/").text
    assert 'class="themetoggle"' in html
    head = html.split("</head>")[0]
    # synchron (ohne defer/async), damit die Seite nicht aufblitzt
    assert re.search(r'<script src="/static/theme\.js"></script>', head)
    js = client.get("/static/theme.js")
    assert js.status_code == 200 and "localStorage" in js.text and "try" in js.text


@pytest.mark.parametrize("path,ctype", [
    ("/static/fonts/roadgeek-mittelschrift.woff2", "font/woff2"),
    ("/static/fonts/barlow-latin-400.woff2", "font/woff2"),
    ("/static/fonts/barlow-latin-ext-700.woff2", "font/woff2"),
    ("/static/fonts/LICENSE-Roadgeek.txt", "text/plain"),
    ("/static/fonts/LICENSE-Barlow-OFL.txt", "text/plain"),
])
def test_fonts_and_licenses_are_served(client, path, ctype):
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(ctype)
    if path.endswith(".woff2"):
        assert r.content[:4] == b"wOF2"


def test_css_uses_guideline_colors_and_does_not_ship_bahnschrift(client):
    css = client.get("/static/style.css").text
    for color in ("#002f45", "#0088ad", "#004e8c", "#d2232a", "#007a8b", "#19682d"):
        assert color in css.lower()
    assert "Bahnschrift" in css and "url(/static/fonts/bahnschrift" not in css.lower()
    assert "Roadgeek" in css and "Barlow" in css
    import os
    fonts = os.listdir(os.path.join(os.path.dirname(__file__), "..", "app", "static", "fonts"))
    assert not any("bahnschrift" in f.lower() or "morsal" in f.lower() for f in fonts)  # Lizenzen: nicht mitgeliefert
    # kein Webfont-Request ins Leere (z. B. 404 für Morsal)
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # Kommentare (z. B. Anleitung für Morsal) zählen nicht
    for url in re.findall(r"url\(([^)]+)\)", code):
        assert client.get(url).status_code == 200, url


def test_pages_have_no_inline_styles_or_scripts(client, db, election):
    code = make_codes(db, election, 0, 1)[0]
    pages = [client.get(p).text for p in PAGES]
    pages.append(client.post("/vote/start", data={"code": code}).text)
    pages.append(client.post("/vote/review", data=ballot_choices(election, db, code)).text)
    token = admin_login(client)
    for path in ["/admin", "/admin/account", "/admin/settings", "/admin/users", "/admin/audit", f"/admin/elections/{election.id}",
                 f"/admin/elections/{election.id}/candidates", f"/admin/elections/{election.id}/codes",
                 f"/admin/elections/{election.id}/dashboard", "/admin/elections/new"]:
        r = client.get(path)
        assert r.status_code == 200, path
        pages.append(r.text)
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    decide_all(client, db, election, token)
    client.post(f"/admin/elections/{election.id}/publish", data={"csrf_token": token})
    pages += [client.get(f"/admin/elections/{election.id}/dashboard").text, client.get(f"/ergebnis/{election.id}").text,
              client.get("/vergangene").text]
    for html in pages:
        assert not re.search(r'\sstyle\s*=', html), "Inline-Style (CSP!)"
        assert not re.search(r'\son[a-z]+\s*=', html), "Inline-Eventhandler (CSP!)"
        assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", html), "Inline-Skript (CSP!)"
        assert not re.search(r"<style", html)


def test_theme_toggle_present_on_every_layout(client):
    for p in PAGES:
        assert "themetoggle" in client.get(p).text


def test_user_menu_for_roles(client, db):
    from tests.conftest import make_user
    admin_login(client)
    html = client.get("/admin").text
    assert 'class="usermenu"' in html and "Abmelden" in html and "Mein Konto" in html
    assert "/admin/users" in html and "/admin/settings" in html and "/admin/audit" in html
    leiter = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient
    from app.main import app
    make_user(db)
    with leiter(app, follow_redirects=False) as c:
        admin_login(c, "leiter", "wahlleiter-passwort")
        page = c.get("/admin").text
        assert "Mein Konto" in page and "/admin/users" not in page and "/admin/settings" not in page


@pytest.mark.parametrize("name,css", [
    ("Maschinenbau", "dept-mb"), ("Elektrotechnik", "dept-et"), ("Mechatronik", "dept-me"),
    ("Land- & Umwelttechnik", "dept-lu"), ("Land- und Umwelttechnik", "dept-lu"), ("  maschinenbau ", "dept-mb"),
    ("ET", "dept-et"), ("MB", "dept-mb"), ("ME", "dept-me"), ("Bautechnik", ""), ("", ""), (None, "")])
def test_department_colors(name, css):
    assert dept_class(name) == css


def test_department_chip_on_ballot_and_results(client, db, election):
    from app.models import Department
    db.scalar(select(Department).where(Department.name == "Maschinenbau")).name = "Maschinenbau"
    election.departments[0].name = "Elektrotechnik"
    db.commit()
    code = make_codes(db, election, 0, 1)[0]
    assert 'class="tag dept-et"' in client.post("/vote/start", data={"code": code}).text


# ------------------------------------------------------------ PDFs: nur Schwarz/Weiß

def test_pdf_sources_use_no_colors_besides_black_white():
    src = open(__import__("os").path.join(__import__("os").path.dirname(__file__), "..", "app", "pdfs.py"), encoding="utf-8").read()
    assert not re.search(r"colors\.(red|blue|green|grey|gray|HexColor|orange|yellow)", src)
    assert "HexColor" not in src and "color='red'" not in src and 'color="red"' not in src


def test_codes_pdf_is_pure_black_and_white():
    from app.pdfs import codes_pdf
    pdf = codes_pdf("Wahl", [("Elektrotechnik", "ABC234"), ("Maschinenbau", "XYZ789")], "https://wahl.htl1-klu.at")
    assert pdf.startswith(b"%PDF")
    from reportlab.pdfbase import pdfdoc  # Inhaltsströme sind komprimiert: über die Seitenbeschreibung prüfen
    import zlib
    streams = b"".join(zlib.decompress(m.group(1)) for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S)
                       if m.group(1)[:1] in (b"x", b"H") and _try(m.group(1)))
    text = streams.decode("latin-1")
    # Farboperatoren (rg/RG/k/K mit Farbwerten) dürfen nur 0 oder 1 Gleichwerte enthalten (schwarz/weiß); g/G nur 0 oder 1
    for m in re.finditer(r"([\d.]+) ([\d.]+) ([\d.]+) (rg|RG)", text):
        r, g, b = (float(x) for x in m.groups()[:3])
        assert r == g == b and r in (0.0, 1.0), m.group(0)
    for m in re.finditer(r"([\d.]+) (g|G)\b", text):
        assert float(m.group(1)) in (0.0, 1.0), m.group(0)


def _try(data):
    import zlib
    try:
        zlib.decompress(data)
        return True
    except Exception:  # noqa: BLE001
        return False


def test_protocol_pdf_renders_with_logo_and_warning(client, db, election):
    from app.models import Image, Setting
    white = io.BytesIO()
    Image.__mro__  # sicherstellen, dass das ORM-Modell importiert ist
    img = __import__("PIL.Image", fromlist=["new"]).new("RGBA", (200, 80), (0, 0, 0, 0))
    for x in range(20, 180):
        for y in range(30, 50):
            img.putpixel((x, y), (255, 255, 255, 255))
    img.save(white, "PNG")
    token = admin_login(client)
    client.post("/admin/settings", data={"csrf_token": token, "school_name": "Test"}, files={"logo": ("l.png", white.getvalue(), "image/png")})
    client.post(f"/admin/elections/{election.id}/close", data={"csrf_token": token})
    r = client.get(f"/admin/elections/{election.id}/protocol.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_white_logo_becomes_black_silhouette_for_print():
    from app.pdfs import logo_for_print
    src = Image.new("RGBA", (100, 40), (0, 0, 0, 0))
    for x in range(10, 90):
        for y in range(10, 30):
            src.putpixel((x, y), (255, 255, 255, 255))
    buf = io.BytesIO()
    src.save(buf, "PNG")
    out = Image.open(io.BytesIO(logo_for_print(buf.getvalue())))
    assert out.mode == "L" and out.getextrema() == (0, 255)   # schwarzes Logo auf weißem Grund, nicht unsichtbar


def test_colored_logo_becomes_grayscale_for_print():
    from app.pdfs import logo_for_print
    src = Image.new("RGB", (60, 30), (0, 136, 173))
    buf = io.BytesIO()
    src.save(buf, "PNG")
    out = Image.open(io.BytesIO(logo_for_print(buf.getvalue())))
    assert out.mode == "L" and 0 < out.getextrema()[0] < 200


def test_woff2_has_correct_mimetype_even_on_minimal_systems():
    import mimetypes
    import app.main  # noqa: F401 - registriert den Typ (schlanke Docker-Images kennen .woff2 nicht)
    assert mimetypes.guess_type("x.woff2")[0] == "font/woff2"
