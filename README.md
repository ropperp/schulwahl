# Schulwahl

Digitale Schulsprecher- und Abteilungssprecherwahl zum Selberhosten (Docker Compose).
FastAPI + PostgreSQL, mobil optimiert, läuft lokal im Schulnetz.

## Funktionen

- **Wahlseite für Schüler:** 6-stelliger Code (oder QR-Code scannen) → Stimmzettel → Zusammenfassung → verbindlich abschicken.
  Schulsprecher/in für alle, Abteilungssprecher/in nur für die Abteilung, zu der der Code gehört. Ungültig wählen ist möglich.
- **Backoffice** (`/admin`, ein gemeinsames Passwort): Wahlen anlegen, Zeitfenster (Start/Ende), Kandidat/innen, Codes pro Abteilung erzeugen
  und als druckfertiges PDF (24 Zettel pro A4-Seite, mit QR-Code), Wahl jederzeit vorzeitig beenden.
- **Dashboard:** Beteiligung live je Abteilung (aktualisiert sich alle 10 s). Ergebnisse inkl. ungültiger Stimmen erst **nach Ende** der Wahl.
  Export als Protokoll-PDF und CSV, optional öffentliche Ergebnisseite.
- Nach Ende des Zeitfensters (oder nach „Jetzt beenden“) werden keine Stimmen mehr angenommen.

## Schnellstart

```bash
git clone https://github.com/<DEIN-NAME>/schulwahl.git
cd schulwahl
cp .env.example .env        # dann ADMIN_PASSWORD, DB_PASSWORD, SECRET_KEY, PUBLIC_URL anpassen
docker compose up -d --build
```

Dann `http://localhost:8080` (Wahl) und `http://localhost:8080/admin` (Backoffice).

`PUBLIC_URL` ist die Adresse, unter der die **Handys** den Server erreichen, z. B. `http://192.168.1.50:8080`.
Sie wird in die QR-Codes geschrieben (im Backoffice beim Erzeugen auch änderbar). `localhost` funktioniert nur am selben Rechner!

Raspberry Pi: gleiche Schritte (am besten 64-Bit-Raspberry-Pi-OS mit Docker). Backup: `docker compose exec db pg_dump -U schulwahl schulwahl > backup.sql`.

## Ablauf am Wahltag

1. Backoffice → *Neue Wahl* (Abteilungen sind vorbelegt) → Kandidat/innen eintragen.
2. Pro Abteilung Codes erzeugen (Anzahl = Schüler plus ein paar Reserve) → PDF drucken, ausschneiden, an Klassenvorstände geben.
3. Start/Ende laufen automatisch nach Zeitfenster. Beteiligung im Dashboard beobachten.
4. Nach Ende: Dashboard → Protokoll-PDF, CSV, optional Ergebnis veröffentlichen.

Verlorene Codes: einfach weitere Codes für die Abteilung erzeugen. Bis zum Start der Wahl sind Wahlgänge/Kandidat/innen änderbar, danach gesperrt.

## Wahlgeheimnis & Sicherheit

- Ein Code wird nur als „verwendet“ markiert. Die Stimmen liegen in einer eigenen Tabelle **ohne Verweis auf den Code**, mit Zufalls-ID und ohne Zeitstempel.
- Stimmen und „Code verbraucht“ werden in **einer Transaktion** geschrieben; ein Code kann auch bei gleichzeitigen Anfragen nur einmal verwendet werden.
- Admin: Passwort aus `.env`, CSRF-Schutz, Login-Sperre nach 5 Fehlversuchen, Sitzungs-Cookie `SameSite=Strict`. Code-Raten wird per IP begrenzt.
- Der Access-Log ist abgeschaltet (QR-Links enthalten den Code).
- Grenzen: Wer Admin-Zugang oder Datenbankzugriff hat, könnte theoretisch Codes ausdrucken und selbst wählen. Admin-Passwort und Server daher nur an die Wahlleitung geben.
  Datenschutz: Es werden keine Namen gespeichert; die Schulleitung/der Datenschutzbeauftragte sollte dennoch zustimmen.

## HTTPS (optional)

Der QR-Code funktioniert mit der normalen Handykamera auch über http. HTTPS brauchst du nur, wenn du **im Browser** scannen willst:
`docker compose --profile https up -d` (Caddy, siehe `Caddyfile`; in `.env` `TRUST_PROXY=1`, `COOKIE_SECURE=1`, `SITE_ADDRESS` setzen).
Ohne öffentliche Domain gibt es ein selbstsigniertes Zertifikat mit Browserwarnung.

## Entwicklung

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_PASSWORD=test python -m pytest              # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db python -m pytest   # PostgreSQL
ADMIN_PASSWORD=test uvicorn app.main:app --reload  # lokal mit SQLite
```
