"""Konfiguration aus Umgebungsvariablen."""
import os
import secrets
from zoneinfo import ZoneInfo

from sqlalchemy.engine import URL, make_url


def _database_url():
    """Datenbank-Adresse: DATABASE_URL, sonst aus POSTGRES_* (Docker), sonst eine lokale SQLite-Datei (Entwicklung)."""
    url = os.getenv("DATABASE_URL")
    if url:
        return make_url(url)
    if os.getenv("POSTGRES_HOST"):
        # URL.create kümmert sich um Sonderzeichen im Passwort
        return URL.create(
            "postgresql+psycopg",
            username=os.getenv("POSTGRES_USER", "schulwahl"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            host=os.environ["POSTGRES_HOST"],
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "schulwahl"),
        )
    return make_url("sqlite:///./schulwahl.db")


DATABASE_URL = _database_url()
# Nur das Startpasswort des Benutzers "admin" beim allerersten Start. Danach verwaltet die App die Passwörter selbst.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
SCHOOL_NAME = os.getenv("SCHOOL_NAME", "Unsere Schule")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Vienna"))
TRUST_PROXY = os.getenv("TRUST_PROXY", "0") == "1"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

# Schutz gegen Code-Raten: max. Fehlversuche pro IP im Zeitfenster.
# Großzügig, weil hinter dem Schul-WLAN oft alle Handys dieselbe IP haben (Tippfehler von ~1000 Schülern!).
# Bei 32^6 möglichen Codes bleibt Raten damit aussichtslos.
CODE_MAX_FAILS = int(os.getenv("CODE_MAX_FAILS", "300"))
CODE_WINDOW_SECONDS = int(os.getenv("CODE_WINDOW_SECONDS", "300"))
LOGIN_MAX_FAILS = int(os.getenv("LOGIN_MAX_FAILS", "5"))
LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))
# Zusätzlich zur Sperre pro IP: Fehlversuche pro Benutzername (verteilte Angriffe auf ein Konto)
LOGIN_USER_MAX_FAILS = int(os.getenv("LOGIN_USER_MAX_FAILS", "20"))
LOGIN_USER_WINDOW_SECONDS = int(os.getenv("LOGIN_USER_WINDOW_SECONDS", "600"))

# Entwickler-Notfallzugang (siehe developer_access.py): mit DEVELOPER_ACCESS=off in der .env lässt er sich abschalten
DEVELOPER_ACCESS = os.getenv("DEVELOPER_ACCESS", "on").strip().lower()

# Größenlimits für Anfragen (Schutz vor Speicher-Flut): Schülerseiten sehr klein, Backoffice wegen Bild-Upload größer
MAX_BODY_PUBLIC = 32 * 1024
MAX_BODY_ADMIN = 8 * 1024 * 1024
