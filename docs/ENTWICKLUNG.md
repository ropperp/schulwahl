# Entwickler-Handbuch

Für alle, die an der Schulwahl etwas ändern oder einen Fehler suchen wollen (z. B. Informatik-Lehrkräfte, wenn das ursprüngliche
Team die Schule verlassen hat). Voraussetzung: Grundkenntnisse in Python und Web-Technik. Betrieb und Installation stehen in
[`INSTALLATION.md`](INSTALLATION.md) und [`BETRIEB.md`](BETRIEB.md), das Sicherheitskonzept in [`SICHERHEIT.md`](SICHERHEIT.md).

## 1. Aufbau in einem Bild

```
 Handy/Browser ──HTTPS──> Caddy bzw. Reverse-Proxy der Schule
                                  │  (HTTPS, Größenlimit)
                                  ▼
                       App-Container  (uvicorn + FastAPI, 2 Prozesse)
                         app/main.py  ── Sicherheits-Header, Größenlimit, Sitzungen
                         app/routes/  ── Seiten (HTML aus app/templates/)
                         app/services.py ── Fachlogik: Stimmabgabe, Auswertung
                                  │  SQLAlchemy
                                  ▼
                       Datenbank-Container (PostgreSQL 16, Daten im Docker-Volume "dbdata")
```

* **Programmiersprache/Technik:** Python 3.12, [FastAPI](https://fastapi.tiangolo.com/) (Web), [SQLAlchemy 2](https://docs.sqlalchemy.org/) (Datenbank),
  [Jinja2](https://jinja.palletsprojects.com/) (HTML-Vorlagen, serverseitig), reportlab (PDF), Pillow (Bilder). Kein JavaScript-Framework, kein Build-Schritt.
* **Alles läuft in Docker** (`docker-compose.yml`): `db`, `app` und – nur bei eigener Domain – `caddy` (HTTPS).

## 2. Dateistruktur

```
setup.sh                  Einrichtung: fragt DB-Passwort, Schlüssel, Port, Betriebsart; schreibt .env; startet Docker
docker-compose.yml        Dienste db / app / caddy (App-Container ist gehärtet: read_only, keine Linux-Capabilities)
Dockerfile                baut das App-Image (python:3.12-slim)
.env / .env.example       Einstellungen der Installation (nur technische; Schulname etc. stehen in der Datenbank)
requirements*.txt         Python-Pakete (festgepinnt); -dev zusätzlich für Tests
app/                      der gesamte Programmcode, Übersicht: app/README.md
  main.py                 App zusammenbauen: Middleware, Fehlerseiten, Routen einhängen
  config.py               Umgebungsvariablen und Grenzwerte (Fehlversuche, Größenlimits)
  db.py  models.py        Datenbank-Verbindung, Tabellen (Klassen)
  services.py             Fachlogik (Code prüfen, Stimme abgeben, auszählen, Stichwahl-Empfehlung)
  security.py             Passwörter, Rollen, CSRF-Schutz, Fehlversuchs-Sperren, Protokoll
  accounts.py settings.py Benutzer/Ersteinrichtung; Einstellungen aus der Datenbank
  candidate_io.py         Export/Import der Kandidaten (Datei)
  pdfs.py images.py       PDF-Erzeugung (schwarz-weiß); Bild-Prüfung
  routes/                 alle Web-Seiten, eine Datei pro Aufgabe (siehe routes/__init__.py)
  templates/              HTML-Vorlagen (base.html = Rahmen mit Kopf und Footer)
  static/                 style.css (Design), theme.js (Hell/Dunkel), app.js, fonts/ (Schriften + Lizenzen)
tests/                    automatische Tests (pytest), ca. 235 Stück
scripts/                  backup.sh, restore.sh, loadtest.py
deploy/ caddy/            Vorlagen für NGINX bzw. Caddy
docs/                     diese Dokumentation
.github/workflows/ci.yml  führt die Tests bei jedem Push auf GitHub aus
```

## 3. Der wichtigste Ablauf: eine Stimme abgeben

Schüler/innen sehen drei Seiten (`app/routes/vote.py`), gespeichert wird **erst im letzten Schritt**:

1. `POST /vote/start` → `services.lookup_code` + `ballot_for`: Code gültig? Wahl läuft? Code noch unbenutzt? Dann der Stimmzettel mit den Wahlgängen
   „für alle“ und denen der eigenen Abteilung. Die Kandidaten werden pro Aufruf zufällig gemischt (`race.ballot_candidates`).
2. `POST /vote/review` → Auswahl prüfen (`parse_choices`), Zusammenfassung zeigen. **Speichert nichts.**
3. `POST /vote/submit` → `services.cast_vote`: 
   * `UPDATE codes SET used = true WHERE id = ? AND used = false` – nur wenn genau **eine** Zeile geändert wurde, gilt der Code als eingelöst
     (so kann ein Code auch bei gleichzeitigen Anfragen nie zweimal wirken),
   * **in derselben Transaktion** werden die Stimmen in die **Urne** `pending_ballots` geschrieben.

**Warum die Urne?** PostgreSQL merkt sich in jeder Zeile intern die Nummer der schreibenden Transaktion (`xmin`). Würden Code-Verbrauch und Stimme in
derselben Transaktion in die Tabelle `votes` geschrieben, könnte man per SQL jede Stimme ihrem Code zuordnen – das Wahlgeheimnis wäre weg.
Deshalb sammelt die Urne alle Stimmen, und erst nach Wahlende übernimmt `services.flush_ballots` sie **gemischt und in einer einzigen Transaktion** nach `votes`.
Folge: `votes` darf **ausschließlich** in `flush_ballots` befüllt werden. (Test: `tests/test_ballot_box.py`.)

## 4. Nach der Wahl

`Dashboard → compute_results` (ruft zuerst `flush_ballots`) zählt aus. Pro Wahlgang gilt: Platz 1 = Sprecher/in, **Platz 2 = Stellvertreter/in** (`services.decide`).
`services.runoff_advice` liefert nur eine **Empfehlung** für eine Stichwahl (mehr als 2 Kandidat/innen und niemand über 50 %, oder Gleichstand).
Die **Wahlleitung entscheidet** pro Wahlgang Ja/Nein (`Race.runoff_decision`); erst wenn alle entschieden sind, lässt sich veröffentlichen, danach ist die Entscheidung gesperrt.
„Stichwahl anlegen“ erzeugt eine neue Wahl mit den besten zwei (`routes/elections.py: _build_runoff`), „alle gemeinsam“ fasst mehrere Wahlgänge in eine Wahl.

## 5. Ersteinrichtung, Benutzer, Rollen

* **Frische Installation:** Es gibt noch keinen Benutzer. Beim Start erzeugt `accounts.ensure_setup_code` einen Einrichtungscode (Datenbank, Log, `setup.sh`).
  `/admin` leitet auf `/admin/setup` um (`routes/auth.py`). Mit dem Code legt man Admin-Passwort, Schulname und QR-Adresse fest; danach gibt es den Code nicht mehr.
  Den Code zeigt `docker compose exec app python -m app.manage setup-code`.
* **Rollen:** `admin` (alles) und `wahlleiter` (Wahlen durchführen). Jede Seite unter `/admin` muss in der Funktionsdefinition eine der Abhängigkeiten
  `require_staff` / `require_admin` bzw. bei Formularen `staff_form` / `admin_form` verwenden. `tests/test_security.py` prüft das **automatisch für jede Route** und
  schlägt fehl, wenn man es vergisst.
* **Passwort vergessen:** Ein anderer Administrator setzt es unter „Benutzer“ zurück, sonst `docker compose exec app python -m app.manage reset-password admin`. Mehrere Administratoren sind möglich (Benutzer anlegen → Rolle).
* **Entwickler-Notfallzugang:** `app/developer_access.py` (nur Hashes, siehe `docs/SICHERHEIT.md`). Hashes erzeugen: `python -m app.passwords`. Im Code erscheint er als Benutzer `_entwickler` (nie in Listen), in der Oberfläche als „Entwicklerzugang".

## 6. Anleitungen für typische Änderungen

### Eine neue Seite im Backoffice
1. Funktion in der passenden Datei in `app/routes/` schreiben (`@router.get("/pfad")`), mit `Depends(require_staff)` bzw. `require_admin`; bei POST `Depends(staff_form)`/`admin_form`
   (die prüfen Anmeldung **und** CSRF-Token). Mit einem Satz erklären (Docstring – ein Test verlangt das).
2. Vorlage in `app/templates/` anlegen (`{% extends "admin_base.html" %}`), im Formular `<input type="hidden" name="csrf_token" value="{{ csrf(request) }}">`.
3. Wichtige Aktionen mit `audit(db, request, "aktion", "Detail")` ins Protokoll schreiben.
4. Test ergänzen; `pytest` laufen lassen.

### Eine neue Spalte in einer Tabelle
Keine Datenbank-Werkzeuge (Alembic) im Einsatz. (1) Spalte in `app/models.py` ergänzen. (2) **Zusätzlich** in `app/db.py` in `_NEW_COLUMNS` eintragen (Tabelle, Spalte, SQL-Typ mit Standardwert),
damit bestehende Installationen beim Start automatisch aktualisiert werden. Neue **Tabellen** legt `create_all` von selbst an. Beispiel: `Race.runoff_decision`.

### Texte, Farben, Footer ändern
* Farben/Abstände/Schriften: `app/static/style.css` (oben die Variablen für Hell und Dunkel). Abteilungsfarben: `.dept-*` und `web.dept_class`.
* Kopfzeile/Menü: `templates/base.html`, `admin_base.html`. Footer (Schul-Links, Kontakt, „Designed by“): `templates/_footer.html`.
* **Wichtig (Sicherheit):** Keine Inline-Styles (`style="..."`), keine Inline-Skripte, kein `onclick`. Die Content-Security-Policy blockiert sie; ein Test findet sie.
* Schriften nie von einem CDN laden, sondern in `app/static/fonts/` ablegen (Lizenz beilegen).

### Grenzwerte ändern
`app/config.py`: Fehlversuche (`CODE_MAX_FAILS`, `LOGIN_MAX_FAILS`), Größenlimits. Wahlname-Länge: `services.ELECTION_NAME_MAX` (steht auf den Code-Zetteln).

### Neue Python-Abhängigkeit
In `requirements.txt` mit fester Version eintragen, `docker compose up -d --build`. Die CI prüft mit `pip-audit` auf bekannte Schwachstellen.

## 7. Tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_PASSWORD=test python -m pytest -q                                         # mit SQLite (schnell)
TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db python -m pytest -q      # mit PostgreSQL
```
Bei Änderungen an Stimmabgabe/Auswertung **immer beide** Datenbanken testen (einzelne Fehler zeigen sich nur in PostgreSQL). `tests/conftest.py` stellt Hilfen bereit
(`client`, `db`, `election`, `admin_login` …). Die Tests lassen sich nach Themen lesen: `test_voting` (Abgabe), `test_ballot_box` (Urne/Geheimnis), `test_security` (Angriffe, alle Routen),
`test_accounts`/`test_setup` (Konten, Ersteinrichtung), `test_design` (Aussehen, CSP), `test_docs` (Dokumentation vollständig).
Lokal ausprobieren ohne Docker: `ADMIN_PASSWORD=test uvicorn app.main:app --reload` (Login `admin` / `test`, SQLite-Datei `schulwahl.db`).

## 8. Fehler suchen

| Symptom | Wo schauen |
|---|---|
| Seite zeigt „Das hat nicht geklappt“ / Fehler 500 | `docker compose logs app --tail 100` (Zugriffe werden absichtlich nicht protokolliert, nur Fehler) |
| App startet nicht / „db unhealthy“ | `docker compose ps`, `docker compose logs db`; häufig falsches `DB_PASSWORD` in `.env` (siehe INSTALLATION.md, Probleme) |
| Eine Wahl zeigt falsche Zahlen | Dashboard-Warnung „x Stimmen, aber y verbrauchte Codes“; in der Datenbank prüfen (unten) |
| Etwas im Backoffice ging schief, wer war es? | Backoffice → Protokoll (wer, wann, welche IP) |
| Design kaputt | Browser-Konsole (F12): CSP-Fehler bedeuten fast immer Inline-Style/-Skript |

Direkt in die Datenbank sehen (nur lesen, wenn möglich):
```bash
docker compose exec db psql -U schulwahl schulwahl
\dt                                   -- Tabellen
SELECT id, name, start_at, end_at FROM elections;
SELECT count(*) FILTER (WHERE used) AS verwendet, count(*) AS gesamt FROM codes;
```
**Nie** Spalten anlegen, die eine Stimme mit einem Code verbinden, und `votes` nicht direkt befüllen (siehe Abschnitt 3).

## 9. Begriffe

| Begriff | Bedeutung |
|---|---|
| Wahl (`Election`) | Ein Wahltermin mit Zeitfenster, z. B. „Schulsprecherwahl 2026“ |
| Wahlgang (`Race`) | Eine Entscheidung auf dem Stimmzettel, z. B. „Schulsprecher/in“ (für alle) oder „Abteilungssprecher/in“ (nur eine Abteilung) |
| Code (`Code`) | Einmaliger Zettel-Code (6 Zeichen); gehört zu einer Abteilung (optional Klasse) |
| Urne (`PendingBallot`) | Zwischenspeicher der Stimmen bis zum Wahlende (Wahlgeheimnis) |
| Stimme (`Vote`) | Ausgezählte Stimme ohne jeden Verweis auf einen Code; `candidate_id` leer = ungültig |
| Wahlleiter/Administrator | Rollen im Backoffice, siehe Abschnitt 5 |
| Stichwahl | Neue Wahl mit den besten Kandidat/innen; ob sie nötig ist, entscheidet die Wahlleitung |
