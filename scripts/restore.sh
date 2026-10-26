#!/usr/bin/env bash
# Spielt eine Sicherung zurück. ACHTUNG: ersetzt den aktuellen Datenbankinhalt komplett!
# Aufruf:  ./scripts/restore.sh backups/schulwahl-20261105-120000.sql.gz
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?Bitte die Sicherungsdatei angeben}"
[ -f "$FILE" ] || { echo "Datei nicht gefunden: $FILE" >&2; exit 1; }
# Zuerst prüfen, ob die Sicherung vollständig ist - bevor irgendetwas gelöscht wird
if ! gunzip -c "$FILE" | tail -n 5 | grep -q "PostgreSQL database dump complete" || ! gunzip -c "$FILE" | grep -q "^COPY public.codes"; then
  echo "Die Sicherung ist beschädigt oder unvollständig - es wurde nichts verändert." >&2; exit 1
fi
read -r -p "Aktuelle Datenbank wird ÜBERSCHRIEBEN. Wirklich fortfahren? (ja/nein) " ans
[ "$ans" = "ja" ] || { echo "Abgebrochen."; exit 1; }
docker compose stop app
docker compose exec -T db psql -U schulwahl -d postgres -c "DROP DATABASE IF EXISTS schulwahl WITH (FORCE);" -c "CREATE DATABASE schulwahl OWNER schulwahl;"
gunzip -c "$FILE" | docker compose exec -T db psql -U schulwahl -d schulwahl -v ON_ERROR_STOP=1 >/dev/null
docker compose start app
echo "Fertig. Die Wahl-App läuft wieder."
