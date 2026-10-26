#!/usr/bin/env bash
# Interaktives Setup: fragt Datenbank-Passwort, geheimen Schlüssel, Port und Betriebsart ab, schreibt die .env und startet alles.
# Schulname, Administrator-Passwort und Adresse für die QR-Codes werden danach im Browser festgelegt (Ersteinrichtung).
# Aufruf:  ./setup.sh        (läuft auf Linux, Raspberry Pi und macOS)
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
err()  { printf '\033[31mFehler: %s\033[0m\n' "$*" >&2; }

command -v docker >/dev/null 2>&1 || { err "Docker ist nicht installiert."; exit 1; }
docker compose version >/dev/null 2>&1 || { err "„docker compose“ fehlt (Docker Compose v2 nötig)."; exit 1; }
if ! DOCKER_ERR="$(docker info 2>&1 >/dev/null)"; then
  case "$DOCKER_ERR" in
    *ermission*denied*)
      err "Keine Berechtigung für Docker. Einmal ausführen:  sudo usermod -aG docker \$USER"
      echo "Danach ab- und wieder anmelden (oder  newgrp docker  ausführen) und ./setup.sh erneut starten." ;;
    *) err "Der Docker-Dienst läuft nicht. Bitte Docker (Desktop) starten und nochmal versuchen."
       echo "$DOCKER_ERR" | head -3 ;;
  esac
  exit 1
fi

rand() { # zufälliger Wert aus Buchstaben und Zahlen (|| true: head beendet die Pipe absichtlich früh)
  head -c 2048 /dev/urandom | base64 | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c "$1" || true
}

ask() { # ask "Frage" "Standardwert" -> Antwort in $REPLY_VAL
  local prompt="$1" def="${2:-}" ans
  if [ -n "$def" ]; then read -r -p "$prompt [$def]: " ans; else read -r -p "$prompt: " ans; fi
  REPLY_VAL="${ans:-$def}"
}

ask_secret() { # ask_secret "Frage" -> Antwort in $REPLY_VAL (nicht sichtbar)
  local ans
  read -r -s -p "$1: " ans; echo
  REPLY_VAL="$ans"
}

valid_secret() { # keine Hochkommas/Zeilenumbrüche wegen der .env-Datei
  case "$1" in *\'*|*$'\n'*|*$'\r'*) return 1;; esac
  return 0
}

valid_port() { # 1-65535
  case "$1" in ''|*[!0-9]*) return 1;; esac
  [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

get_env() { # get_env KEY -> Wert aus bestehender .env (ohne Hochkommas)
  [ -f .env ] || return 0
  local v
  v="$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true)"
  v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}

guess_ip() {
  local ip=""
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
  [ -n "$ip" ] || ip="$(ipconfig getifaddr en0 2>/dev/null)" || true
  [ -n "$ip" ] || ip="$(ipconfig getifaddr en1 2>/dev/null)" || true
  printf '%s' "${ip:-localhost}"
}

bold "=== Schulwahl – Einrichtung ==="
echo "Dieses Skript fragt nur die technischen Angaben ab. Schulname, Administrator-Passwort und die Adresse für die"
echo "QR-Codes legst du danach im Browser fest (Ersteinrichtung) und kannst sie später im Backoffice ändern."
echo

# ---------------------------------------------------------------- 1. Datenbank-Passwort
OLD_DB_PW="$(get_env DB_PASSWORD)"
DB_PW=""
if docker volume ls --format '{{.Name}}' | grep -q '_dbdata$'; then
  if [ -z "$OLD_DB_PW" ]; then
    err "Es existiert schon eine Datenbank, aber keine .env mit dem Datenbank-Passwort."
    echo "Entweder die alte .env wiederherstellen oder die Datenbank mit  docker compose down -v  löschen (löscht alle Daten!)."
    exit 1
  fi
  echo "Hinweis: Es gibt schon eine Datenbank. Das Datenbank-Passwort bleibt unverändert (es kann nachträglich nicht geändert werden)."
  DB_PW="$OLD_DB_PW"
else
  bold "1) Datenbank-Passwort"
  echo "Enter drücken für ein automatisch erzeugtes, sicheres Passwort (empfohlen), oder ein eigenes eingeben."
  ask_secret "Datenbank-Passwort (leer = automatisch)"
  DB_PW="$REPLY_VAL"
  if [ -z "$DB_PW" ]; then DB_PW="$(rand 32)"; echo "→ automatisch erzeugt."; fi
  valid_secret "$DB_PW" || { err "Bitte kein Hochkomma (') im Passwort verwenden."; exit 1; }
fi

# ---------------------------------------------------------------- 2. Geheimer Schlüssel
echo
bold "2) Geheimer Schlüssel (für die Anmelde-Cookies)"
OLD_SECRET="$(get_env SECRET_KEY)"
SECRET=""
if [ -n "$OLD_SECRET" ]; then
  echo "  0) bestehenden Schlüssel behalten (empfohlen)"
fi
echo "  1) automatisch erzeugen mit  openssl rand -hex 32"
echo "  2) selbst eingeben (mindestens 32 Zeichen)"
ask "Auswahl" "$([ -n "$OLD_SECRET" ] && echo 0 || echo 1)"
case "$REPLY_VAL" in
  0) [ -n "$OLD_SECRET" ] || { err "Es gibt keinen bestehenden Schlüssel."; exit 1; }; SECRET="$OLD_SECRET" ;;
  2)
    while true; do
      ask_secret "Schlüssel (mind. 32 Zeichen)"
      SECRET="$REPLY_VAL"
      if [ "${#SECRET}" -lt 32 ]; then err "Bitte mindestens 32 Zeichen."; continue; fi
      valid_secret "$SECRET" || { err "Bitte kein Hochkomma (') verwenden."; continue; }
      break
    done ;;
  *)
    if command -v openssl >/dev/null 2>&1; then SECRET="$(openssl rand -hex 32)"; else SECRET="$(rand 64)"; echo "(openssl nicht gefunden – Zufallswert anders erzeugt)"; fi
    echo "→ automatisch erzeugt." ;;
esac

# ---------------------------------------------------------------- 3. Betriebsart und Port
echo
bold "3) Wie soll die Wahl erreichbar sein?"
echo "  1) Nur im Schulnetz, ohne HTTPS    (einfach: http://<IP-Adresse>:<Port>)"
echo "  2) Mit eigener Domain und HTTPS    (z. B. https://wahl.meineschule.at, Zertifikat automatisch)"
echo "  3) Im Schulnetz mit HTTPS, ohne Domain (selbstsigniertes Zertifikat, Browserwarnung)"
echo "  4) Hinter einem vorhandenen Reverse-Proxy der Schule (NGINX, Apache, Traefik ... übernimmt HTTPS)"
ask "Auswahl" "1"
MODE="$REPLY_VAL"

TRUST=0; COOKIE=0; BIND="0.0.0.0"; PROFILES=""
OLD_PORT="$(get_env PORT)"
case "$MODE" in
  2)
    ask "Domain (z. B. wahl.meineschule.at)" ""
    DOMAIN="$REPLY_VAL"
    [ -n "$DOMAIN" ] || { err "Bitte eine Domain angeben."; exit 1; }
    case "$DOMAIN" in *[!A-Za-z0-9.-]*) err "Ungültige Domain."; exit 1;; esac
    echo "Die Wahl ist von außen über Port 80/443 erreichbar. Der interne App-Port dient nur Prüfungen auf diesem Rechner."
    ask "Interner App-Port (nur lokal)" "${OLD_PORT:-8080}"
    PORT="$REPLY_VAL"; valid_port "$PORT" || { err "Ungültiger Port."; exit 1; }
    PUBLIC_URL="https://$DOMAIN"
    mkdir -p caddy
    printf '%s {\n\tencode gzip zstd\n\theader -Server\n\trequest_body {\n\t\tmax_size 9MB\n\t}\n\treverse_proxy app:8000\n}\n' "$DOMAIN" > caddy/Caddyfile
    TRUST=1; COOKIE=1; BIND="127.0.0.1"; PROFILES="https"
    echo
    echo "Voraussetzungen für das Zertifikat: Die Domain zeigt per DNS auf diesen Rechner, und die Ports 80 und 443"
    echo "sind aus dem Internet auf ihn weitergeleitet (Router/Firewall)."
    ;;
  3)
    ask "Adresse/IP, unter der die Handys den Server erreichen" "$(guess_ip)"
    HOST="$REPLY_VAL"
    case "$HOST" in ''|*[!A-Za-z0-9.-]*) err "Ungültige Adresse."; exit 1;; esac
    ask "Interner App-Port (nur lokal)" "${OLD_PORT:-8080}"
    PORT="$REPLY_VAL"; valid_port "$PORT" || { err "Ungültiger Port."; exit 1; }
    PUBLIC_URL="https://$HOST"
    mkdir -p caddy
    printf ':443 {\n\ttls internal\n\tencode gzip zstd\n\theader -Server\n\trequest_body {\n\t\tmax_size 9MB\n\t}\n\treverse_proxy app:8000\n}\n' > caddy/Caddyfile
    TRUST=1; COOKIE=1; BIND="127.0.0.1"; PROFILES="https"
    ;;
  4)
    ask "Öffentliche Domain, unter der der Proxy die Wahl anbietet (z. B. wahl.meineschule.at)" ""
    DOMAIN="$REPLY_VAL"
    [ -n "$DOMAIN" ] || { err "Bitte eine Domain angeben."; exit 1; }
    case "$DOMAIN" in *[!A-Za-z0-9.-]*) err "Ungültige Domain."; exit 1;; esac
    ask "Port, auf dem die App für den Proxy erreichbar sein soll" "${OLD_PORT:-8080}"
    PORT="$REPLY_VAL"; valid_port "$PORT" || { err "Ungültiger Port."; exit 1; }
    echo "Läuft der Proxy auf DEMSELBEN Rechner? Dann bleibt die App nur lokal erreichbar (sicherer)."
    ask "Proxy auf demselben Rechner? (j/n)" "j"
    case "$REPLY_VAL" in j|J|y|Y) BIND="127.0.0.1";; *) BIND="0.0.0.0";; esac
    PUBLIC_URL="https://$DOMAIN"
    TRUST=1; COOKIE=1
    echo
    echo "Wichtig für den Proxy: HTTPS terminieren, an http://<dieser-Rechner>:$PORT weiterreichen und die Header"
    echo "Host sowie  X-Forwarded-For \$remote_addr  setzen (nicht anhängen!). Vorlage: docs/INSTALLATION.md"
    [ "$BIND" = "0.0.0.0" ] && echo "Achtung: Port $PORT dann per Firewall NUR für den Proxy freigeben, nicht für alle."
    ;;
  *)
    ask "Adresse/IP, unter der die Handys den Server erreichen" "$(guess_ip)"
    HOST="$REPLY_VAL"
    case "$HOST" in ''|*[!A-Za-z0-9.-]*) err "Ungültige Adresse."; exit 1;; esac
    ask "Port" "${OLD_PORT:-8080}"
    PORT="$REPLY_VAL"; valid_port "$PORT" || { err "Ungültiger Port."; exit 1; }
    PUBLIC_URL="http://$HOST:$PORT"
    ;;
esac

# ---------------------------------------------------------------- .env schreiben und starten
umask 077
{
  echo "# Von setup.sh erzeugt am $(date '+%d.%m.%Y %H:%M'). Nicht ins Git einchecken!"
  echo "# Schulname, Admin-Passwort und QR-Adresse stehen NICHT hier: sie werden in der App festgelegt (Ersteinrichtung)."
  echo "DB_PASSWORD='$DB_PW'"
  echo "SECRET_KEY='$SECRET'"
  echo "PUBLIC_URL='$PUBLIC_URL'"
  echo "PORT=$PORT"
  echo "APP_BIND=$BIND"
  echo "TRUST_PROXY=$TRUST"
  echo "COOKIE_SECURE=$COOKIE"
  echo "TZ=Europe/Vienna"
  [ -n "$PROFILES" ] && echo "COMPOSE_PROFILES=$PROFILES"
} > .env
chmod 600 .env
echo
echo ".env wurde geschrieben."

if [ -z "$PROFILES" ]; then docker compose rm -sf caddy >/dev/null 2>&1 || true; fi

bold "Baue und starte die Wahl (beim ersten Mal dauert das einige Minuten) ..."
docker compose up -d --build

printf 'Warte auf den Start'
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then OK=1; break; fi
  printf '.'; sleep 2
done
echo
if [ "${OK:-0}" != "1" ]; then
  err "Die App antwortet noch nicht. Logs ansehen mit:  docker compose logs app --tail 50"
  exit 1
fi

SETUP_CODE="$(docker compose exec -T app python -m app.manage setup-code 2>/dev/null | sed -n 's/^Einrichtungscode: //p' || true)"

echo
bold "Fertig! Jetzt kommt die Ersteinrichtung im Browser:"
echo "  1. Öffne:  $PUBLIC_URL/admin"
if [ -n "$SETUP_CODE" ]; then
  echo "  2. Gib diesen Einrichtungscode ein:  $(printf '\033[1m%s\033[0m' "$SETUP_CODE")"
else
  echo "  2. Die Ersteinrichtung ist schon abgeschlossen – melde dich einfach an (Benutzer: admin)."
fi
echo "  3. Lege Administrator-Passwort, Schulname und die Adresse für die QR-Codes fest."
echo "     (Alles davon kannst du später im Backoffice unter „Einstellungen“ bzw. „Mein Konto“ ändern.)"
echo
echo "Wichtig: Öffne die Seite am besten gleich, bis die Einrichtung abgeschlossen ist. Der Code steht auch im Log:"
echo "  docker compose logs app | grep EINRICHTUNGSCODE"
echo
echo "Später aktualisieren:  git pull && docker compose up -d --build"
