#!/usr/bin/env bash
# Interaktives Setup: fragt Passwörter ab, erzeugt die .env und startet alles mit Docker.
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

get_env() { # get_env KEY -> Wert aus bestehender .env (ohne Hochkommas)
  [ -f .env ] || return 0
  local v
  v="$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true)"
  v="${v#\'}"; v="${v%\'}"
  printf '%s' "$v"
}

bold "=== Schulwahl – Einrichtung ==="
echo

OLD_DB_PW="$(get_env DB_PASSWORD)"
OLD_SECRET="$(get_env SECRET_KEY)"
DB_PW=""
if docker volume ls --format '{{.Name}}' | grep -q '_dbdata$'; then
  if [ -z "$OLD_DB_PW" ]; then
    err "Es existiert schon eine Datenbank, aber keine .env mit dem Datenbank-Passwort."
    echo "Entweder die alte .env wiederherstellen oder die Datenbank mit  docker compose down -v  löschen (löscht alle Daten!)."
    exit 1
  fi
  echo "Hinweis: Es gibt schon eine Datenbank. Das Datenbank-Passwort bleibt unverändert (es kann nachträglich nicht geändert werden)."
  DB_PW="$OLD_DB_PW"
fi

ask "Name der Schule" "$(get_env SCHOOL_NAME || true)"
SCHOOL="${REPLY_VAL:-Unsere Schule}"
valid_secret "$SCHOOL" || { err "Der Schulname darf kein Hochkomma enthalten."; exit 1; }

while true; do
  ask_secret "Passwort für das Backoffice (mind. 10 Zeichen)"
  ADMIN_PW="$REPLY_VAL"
  ask_secret "Passwort wiederholen"
  if [ "$ADMIN_PW" != "$REPLY_VAL" ]; then err "Die Passwörter stimmen nicht überein."; continue; fi
  if [ "${#ADMIN_PW}" -lt 10 ]; then err "Bitte mindestens 10 Zeichen."; continue; fi
  valid_secret "$ADMIN_PW" || { err "Bitte kein Hochkomma (') im Passwort verwenden."; continue; }
  break
done

if [ -z "$DB_PW" ]; then
  echo
  echo "Datenbank-Passwort: Enter drücken für ein automatisch erzeugtes, sicheres Passwort (empfohlen)."
  ask_secret "Eigenes Datenbank-Passwort (oder leer lassen)"
  DB_PW="$REPLY_VAL"
  if [ -z "$DB_PW" ]; then DB_PW="$(rand 32)"; echo "→ automatisch erzeugt."; fi
  valid_secret "$DB_PW" || { err "Bitte kein Hochkomma (') im Passwort verwenden."; exit 1; }
fi
SECRET="${OLD_SECRET:-$(rand 64)}"

echo
echo "Wie soll die Wahl erreichbar sein?"
echo "  1) Nur im Schulnetz, ohne HTTPS    (einfach: http://<IP-Adresse>:8080)"
echo "  2) Mit eigener Domain und HTTPS    (z. B. https://wahl.meineschule.at, Zertifikat automatisch)"
echo "  3) Im Schulnetz mit HTTPS, ohne Domain (selbstsigniertes Zertifikat, Browserwarnung)"
ask "Auswahl" "1"
MODE="$REPLY_VAL"

guess_ip() {
  local ip=""
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
  [ -n "$ip" ] || ip="$(ipconfig getifaddr en0 2>/dev/null)" || true
  [ -n "$ip" ] || ip="$(ipconfig getifaddr en1 2>/dev/null)" || true
  printf '%s' "${ip:-localhost}"
}

PORT=8080; TRUST=0; COOKIE=0; BIND="0.0.0.0"; PROFILES=""
case "$MODE" in
  2)
    ask "Domain (z. B. wahl.meineschule.at)" ""
    DOMAIN="$REPLY_VAL"
    [ -n "$DOMAIN" ] || { err "Bitte eine Domain angeben."; exit 1; }
    case "$DOMAIN" in *[!A-Za-z0-9.-]*) err "Ungültige Domain."; exit 1;; esac
    PUBLIC_URL="https://$DOMAIN"
    mkdir -p caddy
    printf '%s {\n\tencode gzip\n\treverse_proxy app:8000\n}\n' "$DOMAIN" > caddy/Caddyfile
    TRUST=1; COOKIE=1; BIND="127.0.0.1"; PROFILES="https"
    echo
    echo "Voraussetzungen für das Zertifikat: Die Domain zeigt per DNS auf diesen Rechner, und die Ports 80 und 443"
    echo "sind aus dem Internet auf ihn weitergeleitet (Router/Firewall)."
    ;;
  3)
    ask "Adresse/IP, unter der die Handys den Server erreichen" "$(guess_ip)"
    HOST="$REPLY_VAL"
    case "$HOST" in ''|*[!A-Za-z0-9.-]*) err "Ungültige Adresse."; exit 1;; esac
    PUBLIC_URL="https://$HOST"
    mkdir -p caddy
    printf ':443 {\n\ttls internal\n\tencode gzip\n\treverse_proxy app:8000\n}\n' > caddy/Caddyfile
    TRUST=1; COOKIE=1; BIND="127.0.0.1"; PROFILES="https"
    ;;
  *)
    ask "Adresse/IP, unter der die Handys den Server erreichen" "$(guess_ip)"
    HOST="$REPLY_VAL"
    case "$HOST" in ''|*[!A-Za-z0-9.-]*) err "Ungültige Adresse."; exit 1;; esac
    ask "Port" "8080"
    PORT="$REPLY_VAL"
    case "$PORT" in ''|*[!0-9]*) err "Ungültiger Port."; exit 1;; esac
    PUBLIC_URL="http://$HOST:$PORT"
    ;;
esac

umask 077
{
  echo "# Von setup.sh erzeugt am $(date '+%d.%m.%Y %H:%M'). Nicht ins Git einchecken!"
  echo "ADMIN_PASSWORD='$ADMIN_PW'"
  echo "DB_PASSWORD='$DB_PW'"
  echo "SECRET_KEY='$SECRET'"
  echo "PUBLIC_URL='$PUBLIC_URL'"
  echo "SCHOOL_NAME='$SCHOOL'"
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

echo
bold "Fertig!"
echo "  Wahlseite: $PUBLIC_URL"
echo "  Backoffice: $PUBLIC_URL/admin   (Passwort: das eben eingegebene)"
echo
echo "Später aktualisieren:  git pull && docker compose up -d --build"
