# Schulwahl

Digitale Schulsprecher- und Abteilungssprecherwahl zum Selberhosten (Docker Compose).
FastAPI + PostgreSQL, mobil optimiert, läuft lokal im Schulnetz.

## Funktionen

- **Wahlseite für Schüler:** 6-stelliger Code (oder QR-Code scannen) → Stimmzettel → Zusammenfassung → verbindlich abschicken.
  Schulsprecher/in für alle, Abteilungssprecher/in nur für die Abteilung, zu der der Code gehört. Ungültig wählen ist möglich.
- **Backoffice** (`/admin`, ein gemeinsames Passwort): je Wahl die Reiter *Übersicht* (Checkliste, Start/Ende, Zeitfenster), *Kandidat/innen*
  (mit Foto und Klasse, bearbeiten, Wahlgänge umbenennen/sortieren), *Codes* (erzeugen, PDF mit QR-Code, Liste mit Status, Codes löschen) und *Dashboard*.
  Unter *Einstellungen* stellst du Schulname und Schullogo ein. Die Wahl kann jederzeit vorzeitig beendet werden.
- **Dashboard:** Beteiligung live je Abteilung (aktualisiert sich alle 10 s). Ergebnisse inkl. ungültiger Stimmen erst **nach Ende** der Wahl.
  Export als Protokoll-PDF und CSV, optional öffentliche Ergebnisseite.
- Nach Ende des Zeitfensters (oder nach „Jetzt beenden“) werden keine Stimmen mehr angenommen.

## Schnellstart (Laptop oder Raspberry Pi)

```bash
git clone https://github.com/<DEIN-NAME>/schulwahl.git
cd schulwahl
./setup.sh
```

Das Skript fragt nach Schulname, Backoffice-Passwort und (optional) Datenbank-Passwort, erzeugt alle geheimen Schlüssel selbst,
schreibt die `.env` und startet danach `docker compose up -d --build`. Du kannst es jederzeit erneut ausführen
(z. B. um das Backoffice-Passwort zu ändern); das Datenbank-Passwort bleibt dabei erhalten, weil es sich nach dem ersten Start nicht mehr ändern lässt.

Drei Betriebsarten stehen zur Wahl:

1. **Nur im Schulnetz, ohne HTTPS:** `http://<IP>:8080` – am einfachsten. QR-Codes funktionieren mit der normalen Handykamera.
2. **Eigene Domain mit HTTPS:** z. B. `https://wahl.meineschule.at`. Caddy holt automatisch ein Let's-Encrypt-Zertifikat. Voraussetzung:
   DNS-Eintrag der Domain auf den Pi und Ports 80 und 443 im Router auf den Pi weiterleiten.
3. **Schulnetz mit HTTPS ohne Domain:** selbstsigniertes Zertifikat (Browser zeigt eine Warnung).

Raspberry Pi: 64-Bit-Raspberry-Pi-OS, Docker installieren (`curl -fsSL https://get.docker.com | sh`, dann `sudo usermod -aG docker $USER` und neu anmelden).
Backup: `docker compose exec db pg_dump -U schulwahl schulwahl > backup.sql`.
Update: `git pull && docker compose up -d --build` (die Datenbank wird automatisch erweitert).

Manuell ohne Skript: `cp .env.example .env`, Werte eintragen, `docker compose up -d --build`.

## Ablauf am Wahltag

1. Backoffice → *Neue Wahl*. Dort legst du fest, ob Codes **pro Abteilung** (datensparsam, empfohlen) oder **pro Klasse** verteilt werden.
2. Reiter *Kandidat/innen*: Name, Klasse und Foto eintragen (bis zum Start der Wahl jederzeit änderbar).
3. Reiter *Codes*: Pro Abteilung (bzw. Klasse) Codes erzeugen → PDF drucken, ausschneiden, verteilen. Offene Codes lassen sich einzeln oder gesammelt löschen.
4. Start/Ende laufen automatisch nach Zeitfenster; die Wahl kann jederzeit vorzeitig beendet werden. Beteiligung im Dashboard beobachten.
5. Nach Ende: Protokoll-PDF, CSV, Ergebnis veröffentlichen (erscheint unter „Vergangene Wahlen“ auf der Startseite).

**Auswertung:** Platz 1 ist Sprecher/in, Platz 2 Stellvertreter/in. Nur bei Stimmengleichheit um einen dieser Plätze wird eine Stichwahl nötig;
dann legst du im Dashboard mit einem Klick eine Stichwahl als neue Wahl mit den betroffenen Kandidat/innen an (Codes dafür neu erzeugen).
Kandidat/innen ohne Stimmen erhalten keinen Platz.

## Wahlgeheimnis & Sicherheit

- Ein Code wird nur als „verwendet“ markiert. Die Stimmen liegen in einer eigenen Tabelle **ohne Verweis auf den Code**, mit Zufalls-ID und ohne Zeitstempel.
- Stimmen und „Code verbraucht“ werden in **einer Transaktion** geschrieben; ein Code kann auch bei gleichzeitigen Anfragen nur einmal verwendet werden.
- Admin: Passwort aus `.env`, CSRF-Schutz, Login-Sperre nach 5 Fehlversuchen, Sitzungs-Cookie `SameSite=Strict`. Code-Raten wird per IP begrenzt.
- Hochgeladene Bilder werden geprüft, verkleinert und neu kodiert (JPG/PNG/WebP, kein SVG).
- Der Access-Log ist abgeschaltet (QR-Links enthalten den Code).
- Grenzen: Wer Admin-Zugang oder Datenbankzugriff hat, könnte theoretisch Codes ausdrucken und selbst wählen. Admin-Passwort und Server daher nur an die Wahlleitung geben.
  Datenschutz: Es werden keine Namen gespeichert; die Schulleitung/der Datenschutzbeauftragte sollte dennoch zustimmen.

## Entwicklung

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_PASSWORD=test python -m pytest              # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db python -m pytest   # PostgreSQL
ADMIN_PASSWORD=test uvicorn app.main:app --reload  # lokal mit SQLite
```
