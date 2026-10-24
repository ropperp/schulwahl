# Schulwahl

Digitale Schulsprecher- und Abteilungssprecherwahl für eine HTL (ca. 1000–1200 Schüler, Abteilungen z. B. Elektrotechnik, Mechatronik, Maschinenbau).
Selbst gehostet per Docker Compose (Linux-Server der Schule, Domain z. B. wahl.htl1-klu.at; Entwicklung/Test auch auf Raspberry Pi).
Sprache der Oberfläche, Kommentare und Doku: Deutsch (österreichisch: „Klassenvorstand“).

## Stack
- Python 3.12, FastAPI, SQLAlchemy 2.x, Jinja2 (serverseitig gerendert, fast kein JavaScript), PostgreSQL 16 (SQLite für Tests/lokal)
- reportlab (PDF, QR), Pillow (Bilder), Docker Compose: `db`, `app`, optional `caddy` (Profil `https`)
- Kein Frontend-Framework, kein Build-Schritt, kein CDN. Passwort-Hashing mit `hashlib.scrypt` (keine Extra-Abhängigkeit)

## Befehle
```bash
ADMIN_PASSWORD=test python -m pytest                                          # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db python -m pytest        # PostgreSQL - bei Abstimmung/Auswertung IMMER beide
ADMIN_PASSWORD=test uvicorn app.main:app --reload                             # Login: admin / test
./setup.sh | docker compose up -d --build | ./scripts/backup.sh | ./scripts/restore.sh <datei>
python scripts/loadtest.py --url ... --voters 1000 --spread 30                # Lasttest gegen echten Server
docker compose exec app python -m app.manage reset-password admin            # Notfall-CLI
```

## Verzeichnisse
```
app/main.py            App, Middleware (Sicherheits-Header, Body-Limit, Sessions), Fehlerseiten
app/config.py          Env-Variablen und Limits
app/db.py              Engine, init_db() (Advisory-Lock bei PostgreSQL wegen mehrerer Worker), _migrate() (neue Spalten)
app/models.py          Election, Department, Race, Candidate, Code, Vote, PendingBallot, Image, Setting, User, AuditLog
app/services.py        Status, Codes, cast_vote, flush_ballots (Urne), Auswertung (compute_results, decide), delete_election
app/security.py        Passwörter (scrypt), Rollen/Abhängigkeiten (require_staff/require_admin, staff_form/admin_form), CSRF, Limiter, audit()
app/candidate_io.py    Export/Import von Wahlgängen + Kandidaten (JSON, Fotos als Base64, strenge Prüfung vor dem Speichern)
app/accounts.py        Ersteinrichtung (Einrichtungscode, complete_setup), optional Start-Admin aus ADMIN_PASSWORD, set_password()
app/manage.py          Kommandozeile (list-users, reset-password, create-user)
app/routes/            vote.py (Schüler) | public.py (Ergebnis, Archiv, Bilder) | auth.py (Login, Ersteinrichtung /admin/setup, Konto) | users.py (Benutzer, Protokoll)
                       settings.py (Einstellungen) | elections.py (Wahlen, Dashboard, Stichwahlen, Exporte) | candidates.py | codes.py | common.py (Hilfen)
docs/ENTWICKLUNG.md    Entwickler-Handbuch; app/README.md Dateiübersicht. tests/test_docs.py erzwingt Erklärungssätze (Docstrings) für jede Funktion/Klasse/Datei
app/settings.py        Schulname/Logo/QR-Adresse aus DB, Cache nur 5 s (mehrere Worker!)
app/pdfs.py, images.py, timeutil.py, web.py
scripts/               backup.sh, restore.sh, loadtest.py   |  deploy/nginx.conf.example  |  docs/ (INSTALLATION, BETRIEB, SICHERHEIT)
tests/                 test_voting, test_ballot_box, test_accounts, test_security (automatische Prüfung ALLER Routen), test_admin, test_backoffice, test_features
```

## Wahlgeheimnis (wichtigste Regel!)
- `votes` hat **keinen Verweis auf `codes`, keinen Zeitstempel, zufällige UUID**. `candidate_id = NULL` = ungültig. Nie Spalten ergänzen, die Stimme und Code verbinden.
- **PostgreSQL-`xmin`-Falle:** Schreibt man Code-Verbrauch und Stimmen in derselben Transaktion in `votes`, tragen beide dieselbe Transaktions-ID → per SQL zuordenbar.
  Deshalb schreibt `cast_vote` Stimmen nur in die **Urne `pending_ballots`**; `flush_ballots()` übernimmt sie nach Wahlende **in einer Transaktion, gemischt** nach `votes`
  (aufgerufen aus `compute_results`). `votes` darf NUR dort befüllt werden. Test: `test_no_transaction_id_links_votes_to_codes` (PostgreSQL).
- `cast_vote`: Code atomar per `UPDATE codes SET used = true WHERE id = ? AND used = false`; nur bei `rowcount == 1` werden die Stimmen (Urne) in **derselben Transaktion** geschrieben.
- Beide Stimmen (Schulsprecher + Abteilung) in einem Schritt; `review` speichert nichts. Kandidatenreihenfolge pro Stimmzettel zufällig (`race.ballot_candidates`, nicht die persistierte Liste verändern!).
- Wahl-Status `planned | running | ended` aus Start/Ende/`closed_at`. Ergebnisse, CSV, Protokoll erst nach Ende. Verwendete Codes sind nie löschbar.

## Stichwahl-Entscheidung
- `Race.runoff_decision`: `""` offen, `yes`, `no` (setzt die Wahlleitung pro Wahlgang nach Wahlende, Route `/admin/races/{id}/runoff-decision`; gesperrt solange `results_public`).
- `services.runoff_advice()` liefert nur die **Empfehlung** (mehr als 2 Kandidat/innen ohne >50 % oder Gleichstand) und den `plan` (Kandidat/innen einer Stichwahl: Gleichstand Platz 1 / Top 2 inkl. Gleichplatzierte / Gleichstand Platz 2 = `deputy`). Die Entscheidung trifft nie das System.
- Veröffentlichen verlangt, dass jeder Wahlgang entschieden ist. „Stichwahl anlegen“ nur bei `yes`; `runoff-all` fasst alle `yes`-Wahlgänge in EINER neuen Wahl zusammen (`_build_runoff`; Abteilungs-Wahlgänge an ihre Abteilung gebunden, Abteilungen ohne Stichwahl entfallen). Platz 2 ist immer Stellvertretung (`decide()`).

## Design (HTL-1-Designrichtlinien)
- Farben: Dunkelblau `#002f45` (Kopf/Footer/dunkler Grund), Hellblau `#0088ad` (Akzent); Abteilungen `.dept-mb/et/me/lu` (`#004e8c`, `#d2232a`, `#007a8b`, `#19682d`) über den Jinja-Filter `dept_class` (`web.py`). Tokens als CSS-Variablen in `static/style.css`, Hell/Dunkel über `data-theme` + `prefers-color-scheme`.
- Schriften: Fließtext `Bahnschrift` (nur `local`, Microsoft-Lizenz verbietet Auslieferung) → `Barlow` (OFL, in `static/fonts/`); Überschriften/Fett `Roadgeek 2005 Mittelschrift` (Lizenztext liegt bei); Schönschrift `Morsal` nur wenn auf dem Gerät installiert (nicht lizenziert, nicht im Repo). **Keine Schriften von CDNs.**
- `static/theme.js` wird **synchron im `<head>`** geladen (kein Aufblitzen) und speichert die Wahl in `localStorage` (kein Cookie!). Kopf `base.html`, Footer `_footer.html` (Daten der HTL 1 fest eingetragen), Backoffice-Menü per `<details>` in `admin_base.html`.
- Gedruckte PDFs: **nur Schwarz auf Weiß** (Toner sparen); Logos über `pdfs.logo_for_print` (weißes Logo wird schwarze Silhouette). Test: `tests/test_design.py`.

## Benutzer, Rollen, Sitzungen
- Tabelle `users` (Rollen `admin`, `wahlleiter`), `must_change` (Einmal-Passwort), `session_version` (Passwortänderung/Logout entwertet alle Sitzungen). **Ersteinrichtung:** keine Benutzer → `/admin` leitet auf `/admin/setup` (Einrichtungscode aus DB/Log/`manage setup-code` + Admin-Passwort + Schulname + QR-Adresse); danach ist die Seite gesperrt. `ADMIN_PASSWORD` ist nur optional (Tests, automatische Installation).
- **Jede neue Route unter `/admin` braucht `require_staff`/`require_admin` bzw. `staff_form`/`admin_form`.** `tests/test_security.py` prüft automatisch alle Routen (Login, CSRF, Rollen) und schlägt sonst fehl.
- Nur Admin: Einstellungen (inkl. QR-Adresse `public_url`), Benutzer, Protokoll, Wahl löschen. Wahlleiter: alles andere.
- Verwaltungsaktionen mit `audit(db, request, "aktion", "detail")` protokollieren (wird mit dem nächsten `commit` gespeichert).
- QR-Adresse: `settings.public_url(request)` (DB-Einstellung > `PUBLIC_URL` > aktuelle Adresse); nicht per Parameter überschreibbar.

## Sicherheit und bekannte Fallen
- **CSP** (`default-src 'self'; style-src 'self'; script-src 'self'`): keine Inline-Styles/-Skripte/`onclick`. Balkenbreiten über CSS-Klassen `w0`…`w100`, Bestätigungen über `data-confirm` + `static/app.js`.
- FastAPI hängt eingebundene Router lazy ein: Routen für Tests direkt an `router.routes` ablesen, nicht an `app.routes`.
- Wahlseiten setzen keine Cookies, Access-Log aus. Rate-Limits im Prozessspeicher (pro Worker). `TRUST_PROXY=1` nur hinter Proxy, der `X-Forwarded-For` **ersetzt**; `COOKIE_SECURE=1` nur mit funktionierendem HTTPS.
- Body-Limits: 32 KB Schülerseiten, 8 MB Backoffice (`BodyLimit` in `main.py`). Bilder: Whitelist, Neukodierung, max. 25 Mio. Pixel, Kandidatenfotos 240×240.
- CSV-Export entschärft Formel-Zellen. Riesige IDs/Nullbytes → 400 (Handler für `DataError`/`OverflowError`).
- Neue DB-Spalten: in `models.py` ergänzen **und** in `db._NEW_COLUMNS` eintragen (kein Alembic; neue Tabellen legt `create_all` selbst an).
- Zeit in der DB immer naive UTC (`timeutil.utcnow()`).
- `docker-compose.yml`: App mit `read_only`, `cap_drop: ALL`, `no-new-privileges`; DB ohne veröffentlichten Port.
- Backup/Restore-Skripte prüfen die Dumps auf Vollständigkeit (ein leerer Dump hat schon einmal die Datenbank geleert - nicht aufweichen).

## Deployment
- `./setup.sh`: (1) Schulnetz HTTP, (2) Domain + Caddy/Let's Encrypt, (3) Schulnetz selbstsigniert, (4) hinter vorhandenem Reverse-Proxy. Details: `docs/INSTALLATION.md`.
- `DB_PASSWORD` lässt sich nach dem ersten Start nicht mehr über die `.env` ändern (PostgreSQL setzt es nur beim ersten Start des Volumes).
- Healthcheck der DB: `start_period: 120s`, 20 Versuche (langsame Datenträger). Feste IP für den Server.

## Offene Ideen
- Kamera-QR-Scan im Browser (braucht HTTPS), Stichwahl-Verknüpfung mit Ursprungswahl, automatische Ablaufdaten/Löschfristen für Altdaten, 2-Faktor-Login für Admins.
