"""Entwickler-Notfallzugang: ein fest hinterlegter Administrator-Zugang für den Fall, dass keine andere Anmeldung mehr klappt.

Wofür: Sind alle Passwörter verloren und ist niemand mit Server-Zugang erreichbar, kann sich der/die ursprüngliche Entwickler/in
damit anmelden und helfen.

Sicherheits-Eckpunkte (nachzulesen auch in docs/SICHERHEIT.md):
  * Benutzername UND Passwort stehen nicht im Klartext im Programm, sondern nur als scrypt-Hash (`USERNAME_HASH`, `PASSWORD_HASH`).
    Erzeugt werden sie mit  `python -m app.passwords`  (Eingaben unsichtbar). Ohne beide Hashes ist der Zugang AUS.
  * Der Zugang erscheint in keiner Benutzerliste und hat kein im Backoffice änderbares Passwort.
  * Er ist **nicht unsichtbar im Protokoll**: jede Anmeldung und Aktion steht dort als „Entwicklerzugang“ (Administratoren sehen das).
  * Die Schule kann ihn jederzeit abschalten: `DEVELOPER_ACCESS=off` in der .env, dann `docker compose up -d`.
  * Wie jede Anmeldung unterliegt er der Sperre bei Fehlversuchen.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .models import DISPLAY_NAME, HIDDEN_USERNAME, User
from .passwords import normalize_username, verify_password
from .security import ROLE_ADMIN

# Von `python -m app.passwords` erzeugte Zeilen hier eintragen (leer = Zugang aus):
USERNAME_HASH = ""
PASSWORD_HASH = ""

# Die zugehörige Zeile in der Tabelle `users` heißt intern models.HIDDEN_USERNAME (beginnt mit "_": lässt sich nie über das Backoffice
# anlegen oder anmelden). Die App zeigt überall nur models.DISPLAY_NAME. Der echte Benutzername ist nirgends gespeichert.


def enabled() -> bool:
    """True, wenn Hashes hinterlegt sind und die Schule den Zugang nicht abgeschaltet hat."""
    return bool(USERNAME_HASH and PASSWORD_HASH) and config.DEVELOPER_ACCESS != "off"


def check(username: str, password: str) -> tuple[bool, bool]:
    """Prüft die Anmeldedaten. Gibt (Benutzername stimmt, alles stimmt) zurück.

    Beides wird immer vollständig berechnet, damit die Antwortzeit nichts verrät. Der erste Wert dient nur dazu, im Protokoll
    ein falsches Passwort vom Entwicklerzugang zu erkennen, ohne den eingegebenen Namen zu speichern.
    """
    if not enabled():
        return False, False
    name_ok = verify_password(normalize_username(username), USERNAME_HASH)
    password_ok = verify_password(password, PASSWORD_HASH)
    return name_ok, name_ok and password_ok


def get_or_create_user(db: Session) -> User:
    """Die zugehörige Benutzerzeile (Rolle Administrator, ohne verwendbares Passwort); wird beim ersten Login angelegt."""
    user = db.scalar(select(User).where(User.username == HIDDEN_USERNAME))
    if user is None:
        # "!" ist kein gültiger Hash: über die normale Anmeldung (Name + Passwort) kommt man in diese Zeile nie hinein
        db.add(User(username=HIDDEN_USERNAME, role=ROLE_ADMIN, password_hash="!"))
        try:
            db.commit()
        except IntegrityError:  # zwei gleichzeitige Anmeldungen
            db.rollback()
        user = db.scalar(select(User).where(User.username == HIDDEN_USERNAME))
    return user


def is_hidden(user_or_name) -> bool:
    """True für den internen Entwickler-Benutzer (Objekt oder Name)."""
    name = getattr(user_or_name, "username", user_or_name)
    return name == HIDDEN_USERNAME


__all__ = ["DISPLAY_NAME", "HIDDEN_USERNAME", "check", "enabled", "get_or_create_user", "is_hidden"]
