# Schulwahl

Digitale Schulsprecher- und Abteilungssprecherwahl für eine HTL. Läuft im eigenen Docker Compose auf einem
Schul-Server – keine Cloud, keine fremde Firma sieht die Daten.

> **Installation Schritt für Schritt:** [`docs/INSTALLATION.md`](docs/INSTALLATION.md)
> · Ablauf am Wahltag: [`docs/BETRIEB.md`](docs/BETRIEB.md) · Sicherheit: [`docs/SICHERHEIT.md`](docs/SICHERHEIT.md)

## Was kann die App?

- **Schüler/innen** geben einen 6-stelligen Code ein (oder scannen den QR-Code), wählen Schulsprecher/in und
  Abteilungssprecher/in und schicken ab. Die Stimme ist **anonym** – nirgends in der Datenbank steht, wer wie gewählt hat.
- **Wahlleitung** (Backoffice unter `/admin`) legt Kandidat/innen an, druckt Code-Zettel mit QR-Code, startet und
  beendet die Wahl, sieht die Beteiligung live und erstellt nach Wahlende Ergebnis, Protokoll-PDF und CSV.
- Bei einem Gleichstand oder zu vielen Kandidat/innen schlägt die App eine **Stichwahl** vor; entschieden wird aber
  immer von Menschen, nie automatisch.

## Installation

```bash
git clone https://github.com/ropperp/schulwahl.git
cd schulwahl
./setup.sh
```

`./setup.sh` fragt ein paar Dinge ab (Passwort für die Datenbank, Port, ob mit eigener Domain oder nur im Schulnetz)
und startet dann alles. Am Ende zeigt es einen Einrichtungscode – damit wird im Browser unter `/admin` der erste
Administrator-Zugang angelegt.

## Wichtige Befehle

```bash
docker compose ps                                                   # läuft alles?
docker compose logs app --tail 100                                  # Logs ansehen
./scripts/backup.sh                                                 # Datenbank sichern
./scripts/restore.sh backups/<datei>.sql.gz                         # Datenbank zurückspielen
docker compose exec app python -m app.manage reset-password admin   # Admin-Passwort vergessen? Notfall-Befehl
git pull && docker compose up -d --build                            # Update einspielen (vorher Backup!)
```

## Entwicklung

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_PASSWORD=test python -m pytest                   # Tests laufen lassen
ADMIN_PASSWORD=test uvicorn app.main:app --reload       # lokal starten, Login: admin / test
```

Mehr für Entwickler/innen: [`docs/ENTWICKLUNG.md`](docs/ENTWICKLUNG.md) (Aufbau, Dateistruktur, Anleitungen) und
[`app/README.md`](app/README.md) (Dateiübersicht).
