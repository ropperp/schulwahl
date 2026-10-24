#!/usr/bin/env bash
# Sichert die Datenbank (alle Wahlen, Codes, Benutzer, Einstellungen) in eine komprimierte Datei.
# Aufruf:  ./scripts/backup.sh [Zielordner]      (Standard: ./backups)
# Automatisch stündlich per Cron (crontab -e):
#   0 * * * *  cd /pfad/zu/schulwahl && ./scripts/backup.sh >/dev/null 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${1:-./backups}"
KEEP="${KEEP:-48}"   # so viele Sicherungen bleiben erhalten
mkdir -p "$DIR"
chmod 700 "$DIR"
FILE="$DIR/schulwahl-$(date +%Y%m%d-%H%M%S).sql.gz"
umask 077
TMP="$FILE.part"
trap 'rm -f "$TMP"' EXIT
if ! docker compose exec -T db pg_dump -U schulwahl --no-owner schulwahl | gzip > "$TMP"; then
  echo "Backup fehlgeschlagen (läuft die Datenbank?)." >&2; exit 1
fi
# Nur eine vollständige Sicherung wird abgelegt (pg_dump schreibt am Ende eine Abschlusszeile)
if ! gunzip -c "$TMP" | tail -n 5 | grep -q "PostgreSQL database dump complete"; then
  echo "Backup unvollständig - verworfen." >&2; exit 1
fi
mv "$TMP" "$FILE"
# alte Sicherungen aufräumen
ls -1t "$DIR"/schulwahl-*.sql.gz | tail -n +"$((KEEP + 1))" | xargs -r rm -f
echo "Gesichert: $FILE ($(du -h "$FILE" | cut -f1))"
