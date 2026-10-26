# Schulwahl

Digitale Schulsprecher- und Abteilungssprecherwahl zum Selberhosten (Docker Compose): FastAPI + PostgreSQL, mobil optimiert,
datensparsam und mit geschütztem Wahlgeheimnis. Gebaut für eine HTL mit ca. 1000–1200 Schüler/innen, getestet mit **1000 gleichzeitigen Wählern**.

> **Du willst die Wahl auf einem Server installieren?** → [`docs/INSTALLATION.md`](docs/INSTALLATION.md) (Schritt für Schritt, auch für die Schul-IT)
> · Ablauf am Wahltag: [`docs/BETRIEB.md`](docs/BETRIEB.md) · Sicherheit und Tests: [`docs/SICHERHEIT.md`](docs/SICHERHEIT.md)

## Funktionen

- **Wahlseite für Schüler:** 6-stelliger Code (oder QR-Code scannen) → Stimmzettel → Zusammenfassung → verbindlich abschicken.
  Schulsprecher/in für alle, Abteilungssprecher/in nur für die eigene Abteilung. Ungültig wählen ist möglich.
  Die **Reihenfolge der Kandidat/innen wird für jeden Stimmzettel zufällig gemischt**.
- **Backoffice** (`/admin`) mit **Benutzerkonten und Rollen**:
  - **Administrator/in:** alles, zusätzlich *Einstellungen* (Schulname, Logo, **Adresse im QR-Code**), *Benutzer* (Wahlleiter anlegen, deren Passwort zurücksetzen), *Protokoll*, Wahlen löschen.
  - **Wahlleiter/in:** Wahlen durchführen – Kandidat/innen (mit Foto), Codes (PDF mit QR), Start/Ende, Dashboard, Ergebnis, Stichwahl.
  - Jede Person ändert ihr Passwort selbst (*Mein Konto*). Einmal-Passwörter erzwingen den Wechsel beim ersten Login.
- **Dashboard:** Beteiligung live je Abteilung. Ergebnisse inkl. ungültiger Stimmen erst **nach Ende**; Protokoll-PDF, CSV, optional öffentliche Ergebnisseite („Vergangene Wahlen“).
- **Auswertung:** Platz 1 Sprecher/in, Platz 2 Stellvertreter/in. Pro Wahlgang gibt das System eine **Stichwahl-Empfehlung** (mehr als 2 Kandidat/innen ohne absolute Mehrheit oder Gleichstand), **die Wahlleitung entscheidet** mit Ja/Nein. Veröffentlicht werden kann erst nach der Entscheidung; danach ist sie gesperrt (bis die Veröffentlichung zurückgenommen wird). „Ja“ legt per Klick die Stichwahl mit den zwei Bestplatzierten an.
- **Kandidaten-Export/-Import:** Wahlgänge samt Kandidat/innen und Fotos als Datei sichern und in eine neue Wahl wieder einspielen (praktisch für Testläufe und das nächste Jahr).
- **Wahlgeheimnis:** Stimmen sind nicht mit Codes verknüpft – auch nicht über Datenbank-Interna (Urne, siehe [Sicherheit](docs/SICHERHEIT.md)).

## Aussehen

Die Oberfläche folgt den Designrichtlinien der HTL 1 (Dunkelblau/Hellblau, Abteilungsfarben, Bahnschrift bzw. freie Ersatzschrift Barlow, Roadgeek für Überschriften), mit Hell/Dunkel-Umschalter und Schul-Footer.
Das **Schullogo** wird im Backoffice unter *Einstellungen* hochgeladen – am besten die weiße Variante auf transparentem Grund (die Kopfzeile ist dunkelblau); in gedruckten PDFs erscheint es automatisch schwarzweiß.
Code-Zettel und Wahlprotokoll sind zum Tonersparen reines Schwarz auf Weiß. Die Schönschrift *Morsal* wird nicht mitgeliefert (Lizenz) und nur genutzt, wenn sie auf dem Gerät installiert ist.

## Schnellstart

```bash
git clone https://github.com/ropperp/schulwahl.git
cd schulwahl
./setup.sh
```

`setup.sh` fragt Datenbank-Passwort, geheimen Schlüssel (automatisch mit `openssl rand -hex 32` oder selbst eingeben), Port und die Betriebsart
(nur Schulnetz / eigene Domain mit automatischem HTTPS / selbstsigniert / hinter vorhandenem Reverse-Proxy), schreibt die `.env` und startet `docker compose up -d --build`.
Am Ende zeigt es einen **Einrichtungscode**. Danach `/admin` im Browser öffnen: Die **Ersteinrichtung** legt mit dem Code das Administrator-Passwort, den Schulnamen und die Adresse für die QR-Codes fest
(alles später im Backoffice änderbar). Es muss kein Passwort in einer Datei stehen.

Wichtige Befehle:

```bash
docker compose ps                                   # Status
docker compose logs app --tail 100                  # Logs (Zugriffe werden absichtlich nicht protokolliert)
./scripts/backup.sh                                 # Datenbank sichern (Cron-Beispiel in docs/INSTALLATION.md)
./scripts/restore.sh backups/<datei>.sql.gz         # zurückspielen
docker compose exec app python -m app.manage reset-password admin   # Notfall: Admin-Passwort vergessen
python scripts/loadtest.py --url https://wahl.meineschule.at --voters 1000 --spread 30   # Lasttest
git pull && docker compose up -d --build            # Update (vorher Backup!)
```

## Leistung

Auf einem normalen 4-Kern-Rechner (Docker, 2 App-Prozesse + PostgreSQL) im Lasttest mit 1000 virtuellen Schüler/innen
(je Stimmzettel mit 10 Kandidatenfotos): alle 1000 Stimmen korrekt gezählt, bei Verteilung über 90 s Antworten im Median unter 50 ms (95 %: unter 0,1 s);
selbst 1000 Wähler in einer einzigen Sekunde kamen alle durch (mit Wartezeiten von einigen Sekunden). Empfehlung: Klassen nacheinander wählen lassen.
Mit `scripts/loadtest.py` lässt sich das auf dem eigenen Server nachprüfen.

## Konfiguration (`.env`)

| Variable | Bedeutung |
|---|---|
| `DB_PASSWORD` | Datenbank-Passwort (nach dem ersten Start nicht mehr ändern) |
| `SECRET_KEY` | Schlüssel für Sitzungs-Cookies (mind. 32 Zeichen; `setup.sh` erzeugt ihn mit `openssl rand -hex 32` oder nimmt deine Eingabe) |
| `PORT` | Port der App auf diesem Rechner (Standard 8080) |
| `PUBLIC_URL` | Voreinstellung für die Adresse im QR-Code; im Backoffice unter *Einstellungen* änderbar |
| `TRUST_PROXY=1`, `COOKIE_SECURE=1` | Nur hinter einem HTTPS-Proxy (setzt `setup.sh`) |
| `WEB_CONCURRENCY` | Anzahl App-Prozesse (Standard 2) |
| `CODE_MAX_FAILS`, `LOGIN_MAX_FAILS` | Grenzen für Fehlversuche (Code-Eingabe pro IP / Login pro IP) |

## Entwicklung

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_PASSWORD=test python -m pytest                                            # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db python -m pytest          # PostgreSQL (vor Änderungen an Abstimmung/Auswertung immer auch testen)
ADMIN_PASSWORD=test uvicorn app.main:app --reload                               # lokal mit SQLite, Login: admin / test
```

**Für Entwickler:** Aufbau, Dateistruktur, Ablauf einer Stimmabgabe, Anleitungen für typische Änderungen und Fehlersuche stehen in [`docs/ENTWICKLUNG.md`](docs/ENTWICKLUNG.md); Dateiübersicht in [`app/README.md`](app/README.md).
`CLAUDE.md` enthält dieselben Regeln in Kurzform für KI-Programmierassistenten.
