# Installationsanleitung (für die Schul-IT)

Diese Anleitung bringt die Schulwahl auf einem Server der Schule zum Laufen – erreichbar unter einer eigenen
Adresse, z. B. **https://wahl.htl1-klu.at**, **nicht nur im WLAN**, sondern auch über Mobilfunk/Internet
(damit instabiles Schul-WLAN keine Rolle spielt). Dauer: ca. 30–45 Minuten, plus Wartezeit auf DNS/Zertifikat.

**Inhalt:** [1 Voraussetzungen](#1-voraussetzungen) · [2 Server vorbereiten](#2-server-vorbereiten) · [3 Installieren](#3-installieren) ·
[4 Erreichbarkeit: Variante A oder B](#4-erreichbarkeit) · [5 Erste Einrichtung](#5-erste-einrichtung-im-browser) · [6 Testlauf](#6-testlauf-pflicht-vor-dem-echten-wahltag) ·
[7 Backup](#7-backups-einrichten) · [8 Updates](#8-updates) · [9 Probleme](#9-probleme-und-lösungen) · [10 Checkliste](#10-checkliste-vor-dem-wahltag)

---

## 1. Voraussetzungen

| Was | Empfehlung |
|---|---|
| **Server** | Linux (Ubuntu 24.04 LTS oder Debian 12/13), physisch oder VM. Windows: siehe [Hinweis](#windows-server) |
| **Hardware** | mindestens 2 CPU-Kerne, 4 GB RAM, 20 GB Platz (SSD). Ein Raspberry Pi 4/5 mit SSD genügt für Tests, für den Wahltag ist ein normaler PC/Server entspannter |
| **Software** | Docker Engine + Docker Compose v2, `git`, `curl` (Anleitung unten) |
| **Domain** | z. B. `wahl.htl1-klu.at` – ein DNS-Eintrag (A bzw. AAAA) auf die **öffentliche** IP-Adresse der Schule bzw. des Servers |
| **Ports** | **Variante A:** Ports 80 und 443 aus dem Internet zum Server. **Variante B:** die Schule hat schon einen Reverse-Proxy (NGINX u. Ä.), der HTTPS übernimmt |
| **Zugang** | `sudo`-Rechte auf dem Server |

> **Wichtig:** Der Server braucht eine **feste IP-Adresse** (DHCP-Reservierung), sonst ändert sich die Adresse im Router/Proxy.
> Es müssen **keine Namen oder Klassenlisten** auf den Server – nur anonyme Codes.

## 2. Server vorbereiten

```bash
# Docker installieren (offizielles Skript; funktioniert auf Ubuntu/Debian/Raspberry Pi OS)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER        # danach ab- und wieder anmelden!
sudo apt-get install -y git curl

# Test (nach erneutem Anmelden):
docker run --rm hello-world
docker compose version               # muss "v2.x" anzeigen
```

**Firewall (Variante A):** nur SSH, HTTP und HTTPS freigeben:

```bash
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
```

Hinweis: Docker umgeht `ufw` für veröffentlichte Ports. Das ist hier unkritisch, weil in Variante A die App nur an
`127.0.0.1` lauscht und die Datenbank gar keinen Port veröffentlicht – von außen ist nur Caddy (80/443) erreichbar.

## 3. Installieren

```bash
git clone https://github.com/ropperp/schulwahl.git
cd schulwahl
./setup.sh
```

(Ist das Repository privat, braucht `git clone` ein *Fine-grained Personal Access Token* mit Leserecht auf „Contents“.)

`setup.sh` fragt Folgendes – Empfehlungen für die Schule:

| Frage | Antwort |
|---|---|
| Datenbank-Passwort | **Enter** drücken → wird automatisch sicher erzeugt (oder eigenes eingeben) |
| Geheimer Schlüssel | **1** → automatisch mit `openssl rand -hex 32` (empfohlen) oder **2** → selbst eingeben (mind. 32 Zeichen) |
| Wie soll die Wahl erreichbar sein? | **2** (eigene Domain, Caddy kümmert sich um HTTPS) oder **4** (vorhandener Reverse-Proxy) |
| Domain / Adresse | z. B. `wahl.htl1-klagenfurt.at` |
| Port | Standard `8080` (bei Variante 2 nur intern, bei 4 der Port für den Proxy) |

Schulname, Administrator-Passwort und die Adresse für die QR-Codes fragt das Skript **nicht**: Die legst du danach im Browser fest (Kapitel 5).
Das Skript schreibt die `.env`, baut die App und startet alles. Beim ersten Mal dauert das einige Minuten.
Am Ende steht „Fertig!“ mit der Adresse und dem **Einrichtungscode**. Prüfen:

```bash
docker compose ps                    # app und db: "healthy"
curl -s http://127.0.0.1:8080/health # {"status":"ok"}   (bei Variante A ggf. über https testen)
```

## 4. Erreichbarkeit

### Variante A – Caddy mit eigener Domain (Betriebsart 2, einfachste Lösung)

1. **DNS:** Eintrag `wahl.htl1-klu.at` → öffentliche IP des Servers (bei der Schul-IT bzw. beim Domain-Anbieter).
2. **Router/Firewall:** Ports **80** und **443** auf den Server weiterleiten.
3. `./setup.sh` mit Auswahl **2** ausführen. Caddy holt beim ersten Aufruf automatisch ein Let's-Encrypt-Zertifikat (dauert ca. 1 Minute).
4. Im Browser `https://wahl.htl1-klu.at` öffnen – Schloss-Symbol, Startseite „Wahlcode eingeben“.

Zertifikat klappt nicht? `docker compose logs caddy --tail 50` – meist zeigt der DNS-Eintrag noch auf die falsche IP, oder Port 80 ist nicht erreichbar
(Let's Encrypt prüft über Port 80).

### Variante B – hinter dem vorhandenen Reverse-Proxy der Schule (Betriebsart 4)

Die Schule hat bereits einen Proxy (NGINX, Apache, Traefik …), der HTTPS-Zertifikate verwaltet. Dann:

1. `./setup.sh` mit Auswahl **4**, Domain angeben, Port (Standard 8080), und ob der Proxy auf demselben Rechner läuft.
2. Im Proxy eine Weiterleitung auf `http://<Server>:8080` einrichten. Vorlage für **NGINX**: [`deploy/nginx.conf.example`](../deploy/nginx.conf.example):

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $remote_addr;   # ersetzen, NICHT anhängen!
    proxy_set_header X-Forwarded-Proto $scheme;
}
client_max_body_size 9m;
```

3. **Zwei Punkte sind sicherheitsrelevant:**
   - `X-Forwarded-For` muss vom Proxy **gesetzt/ersetzt** werden (nicht `$proxy_add_x_forwarded_for`), sonst kann ein Angreifer die Fehlversuchs-Sperre umgehen.
   - Läuft der Proxy auf einem *anderen* Rechner, den App-Port (8080) per Firewall **nur für den Proxy** freigeben.
4. In der `.env` müssen stehen: `TRUST_PROXY=1`, `COOKIE_SECURE=1` und `PUBLIC_URL='https://wahl.htl1-klu.at'` (setzt `setup.sh` bei Auswahl 4 selbst).

### Variante C – nur Schulnetz ohne Domain (nicht empfohlen)

`./setup.sh` Auswahl **1** (HTTP, `http://<IP>:8080`) oder **3** (selbstsigniertes Zertifikat, Browser warnt). Für diese Schule ungeeignet, weil das WLAN instabil ist.

### Windows-Server

Möglich mit *Docker Desktop (WSL2)*, aber für den Dauerbetrieb nicht empfohlen. Statt `setup.sh`:
`.env.example` nach `.env` kopieren und ausfüllen (`DB_PASSWORD`, `SECRET_KEY` = `openssl rand -hex 32` bzw. lange Zufallsfolge, `PORT`, `PUBLIC_URL`, bei HTTPS hinter Proxy
`TRUST_PROXY=1` und `COOKIE_SECURE=1`), dann `docker compose up -d --build`. Einfacher: eine kleine Linux-VM (Hyper-V/VirtualBox) verwenden.

## 5. Erste Einrichtung im Browser

1. `https://wahl.htl1-klu.at/admin` öffnen. Bei einer frischen Installation führt das automatisch zur **Ersteinrichtung**.
2. Den **Einrichtungscode** eingeben (steht am Ende von `setup.sh`; später erneut anzeigen mit `docker compose exec app python -m app.manage setup-code`
   oder `docker compose logs app | grep EINRICHTUNGSCODE`). Er schützt davor, dass die Einrichtung jemand anderes vor dir abschließt – öffne die Seite deshalb gleich nach dem Start.
3. Auf derselben Seite festlegen: **Administrator-Passwort** (Benutzername `admin`, mind. 10 Zeichen; mehrere Wörter hintereinander merkt man sich leicht),
   **Name der Schule** und **Adresse der Wahlseite** (genau die Adresse, unter der die Schüler die Wahl erreichen, z. B. `https://wahl.htl1-klu.at`; kommt in die QR-Codes).
   Alles davon lässt sich später im Backoffice ändern (*Einstellungen*, *Mein Konto*). Danach ist man angemeldet, der Code ist verbraucht.
   - Unter **Einstellungen** außerdem das **Logo** hochladen (weiße Variante auf transparentem Hintergrund, passt zur dunkelblauen Kopfzeile).
4. **Benutzer:** für jede Lehrkraft, die die Wahl durchführt, einen **Wahlleiter**-Zugang anlegen. Das System erzeugt ein Einmal-Passwort, das persönlich
   weitergegeben wird; beim ersten Login muss die Person ein eigenes Passwort wählen. Vergisst jemand sein Passwort: Admin → *Benutzer → Passwort zurücksetzen*.
5. **Protokoll** zeigt, wer wann was getan hat (Anmeldungen, Codes erzeugt, Wahl gestartet/beendet …).

| | Administrator/in | Wahlleiter/in |
|---|:-:|:-:|
| Wahlen anlegen, Kandidat/innen, Codes, Start/Ende, Dashboard, Ergebnis, Stichwahl | ✔ | ✔ |
| Eigenes Passwort ändern | ✔ | ✔ |
| Einstellungen (Schulname, Logo, QR-Adresse) | ✔ | – |
| Benutzer anlegen/löschen, Wahlleiter-Passwort zurücksetzen | ✔ | – |
| Wahl löschen, Protokoll ansehen | ✔ | – |

## 6. Testlauf (Pflicht vor dem echten Wahltag)

1. **Funktionstest mit echtem Handy über Mobilfunk** (WLAN aus!): Testwahl anlegen → Kandidat/innen → 5 Codes erzeugen (PDF öffnet sich) → *Jetzt starten* →
   QR-Code scannen → wählen → *Jetzt beenden* → Dashboard/Protokoll prüfen. Anschließend die Testwahl löschen (als Administrator).
2. **Lasttest (1000 gleichzeitige Wähler)** – am besten von einem anderen Rechner im Internet/Schulnetz aus, **nicht** während einer echten Wahl:

```bash
pip install httpx pillow
python scripts/loadtest.py --url https://wahl.htl1-klu.at --user admin --voters 1000 --spread 30
```

   Der Test legt eine Wahl „Lasttest“ an, lässt 1000 virtuelle Schüler wählen, prüft die Zählung und **löscht die Wahl wieder**. Am Ende steht *BESTANDEN* oder *NICHT BESTANDEN*.
   Orientierung aus unseren Tests: bei Verteilung über ≥ 60 s antwortet der Server in unter 0,2 s; selbst 1000 Wähler in derselben Sekunde kamen alle durch
   (mit Wartezeiten von einigen Sekunden). Wird es bei euch deutlich schlechter, siehe [Probleme](#9-probleme-und-lösungen).
3. **Backup und Wiederherstellung** einmal ausprobieren (nächster Abschnitt).

## 7. Backups einrichten

```bash
./scripts/backup.sh                      # legt backups/schulwahl-<Zeit>.sql.gz an (prüft die Datei auf Vollständigkeit)
```

Automatisch jede Stunde (Cron des Benutzers, der Docker bedienen darf):

```bash
crontab -e
# Zeile einfügen (Pfad anpassen):
0 * * * *  cd /home/<benutzer>/schulwahl && ./scripts/backup.sh >/dev/null 2>&1
```

Es bleiben die letzten 48 Sicherungen erhalten. **Kopiere den Ordner `backups/` zusätzlich auf ein anderes Gerät** (USB-Stick, Netzlaufwerk) – ein Backup auf demselben Server schützt nicht vor einem Festplattenschaden.

Zurückspielen (ersetzt die komplette Datenbank, fragt vorher nach):

```bash
./scripts/restore.sh backups/schulwahl-20261105-120000.sql.gz
```

Sicherungen enthalten Codes, Benutzer-Hashes und Stimmen: **wie Wahlunterlagen behandeln** (Ordner hat Rechte 700) und nach der Aufbewahrungsfrist löschen.

## 8. Updates

```bash
cd schulwahl
./scripts/backup.sh          # immer zuerst sichern
git pull
docker compose up -d --build # die Datenbank wird automatisch erweitert
docker compose ps            # alles "healthy"?
```

**Nicht** während einer laufenden Wahl aktualisieren. Betriebssystem-Updates (`sudo apt update && sudo apt upgrade`) regelmäßig, aber ebenfalls nicht am Wahltag.

## 9. Probleme und Lösungen

| Problem | Ursache / Lösung |
|---|---|
| `permission denied` bei Docker | `sudo usermod -aG docker $USER`, abmelden und wieder anmelden |
| `db` ist „unhealthy“ beim ersten Start | Auf langsamen Datenträgern dauert die Initialisierung länger. `docker compose up -d` nach 2 Minuten wiederholen |
| App meldet Datenbankfehler nach geändertem `DB_PASSWORD` | Das DB-Passwort gilt nur beim allerersten Start. Altes Passwort wieder eintragen (nicht ändern). Nur wenn noch keine Daten existieren: `docker compose down -v` (löscht alles!) |
| Kein Zertifikat (Variante A) | `docker compose logs caddy`; DNS prüfen (`dig +short wahl.htl1-klu.at`), Ports 80/443 erreichbar? |
| „Zu viele Fehlversuche“ bei vielen Schülern | Sperre pro IP ist großzügig (300/5 min). Bei Variante B prüfen, dass der Proxy `X-Forwarded-For $remote_addr` setzt und `TRUST_PROXY=1` gesetzt ist – sonst teilen sich *alle* die Proxy-IP |
| Admin-Passwort vergessen | `docker compose exec app python -m app.manage reset-password admin` → neues Zufallspasswort (muss beim Login geändert werden) |
| Einrichtungscode verloren | `docker compose exec app python -m app.manage setup-code` (solange die Ersteinrichtung nicht abgeschlossen ist) |
| Wahlleiter-Passwort vergessen | Admin: *Benutzer → Passwort zurücksetzen* |
| Seiten langsam bei Massenzugriff | `WEB_CONCURRENCY` in der `.env` auf die Zahl der CPU-Kerne setzen (z. B. 4), `docker compose up -d`; Wähler klassenweise staffeln (siehe `docs/BETRIEB.md`) |
| Logs ansehen | `docker compose logs app --tail 100` (Zugriffe werden absichtlich nicht protokolliert) |
| Alles stoppen / starten | `docker compose stop` / `docker compose start` (Daten bleiben erhalten) |

## 10. Checkliste vor dem Wahltag

- [ ] Domain erreichbar, HTTPS-Schloss, **von außerhalb** des Schulnetzes getestet (Handy über Mobilfunk)
- [ ] Administrator-Passwort persönlich gesetzt; Wahlleiter-Zugänge angelegt und getestet
- [ ] **Einstellungen → Adresse im QR-Code** stimmt (erst danach Zettel drucken!)
- [ ] Testwahl komplett durchgespielt und gelöscht; Lasttest *BESTANDEN*
- [ ] Backup läuft (Cron), Wiederherstellung einmal getestet, Kopie auf zweitem Gerät
- [ ] Server: feste IP, Stromversorgung/USV, keine automatischen Neustarts am Wahltag geplant, Uhrzeit korrekt (`timedatectl`)
- [ ] Kandidat/innen und Wahlgänge eingetragen (nach Start nicht mehr änderbar), Zeitfenster geprüft
- [ ] Codes gedruckt, gezählt, sicher aufbewahrt (siehe `docs/BETRIEB.md`)
- [ ] Notfallplan bekannt: Wer hat Server-Zugang? Wer kann Passwörter zurücksetzen?

Weiter: [`BETRIEB.md`](BETRIEB.md) (Ablauf am Wahltag) · [`SICHERHEIT.md`](SICHERHEIT.md) (Sicherheitskonzept und Tests)
