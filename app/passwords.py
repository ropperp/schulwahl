"""Passwort-Hashing (scrypt) ohne Abhängigkeit von Datenbank oder Web-Teil.

Als eigenes Modul, damit sich Hashes auch auf einem Rechner ohne Installation erzeugen lassen:

    python -m app.passwords          # fragt Benutzername und Passwort (unsichtbar) und gibt zwei Hash-Zeilen aus

Diese Zeilen gehören in app/developer_access.py (Entwickler-Notfallzugang, siehe dort).
"""
import base64
import getpass
import hashlib
import hmac
import os
import sys

MAX_PASSWORD_LENGTH = 200
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1  # ca. 16 MB RAM, auch auf einem Raspberry Pi flott genug


def _b64(raw: bytes) -> str:
    """Bytes als Base64-Text."""
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    """Passwort-Hash mit scrypt und zufälligem Salz: `scrypt$N$r$p$salt$hash`."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Prüft ein Passwort gegen einen gespeicherten Hash (zeitkonstant)."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode()[:MAX_PASSWORD_LENGTH * 4], salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def normalize_username(raw: str) -> str:
    """Benutzernamen vereinheitlichen (Leerzeichen am Rand weg, Kleinschreibung)."""
    return raw.strip().lower()


MIN_DEVELOPER_USERNAME = 8
MIN_DEVELOPER_PASSWORD = 16


def main(ask=getpass.getpass, out=print) -> int:
    """Kommandozeile: erzeugt die zwei Hash-Zeilen für den Entwickler-Notfallzugang. Gibt 0 bei Erfolg zurück."""
    out("Hash-Erzeugung für den Entwickler-Notfallzugang. Eingaben bleiben unsichtbar und werden nicht gespeichert.")
    out(f"Benutzername: mind. {MIN_DEVELOPER_USERNAME} Zeichen, höchstens 50 (Groß-/Kleinschreibung egal). "
        f"Passwort: mind. {MIN_DEVELOPER_PASSWORD} Zeichen, zufällig und einmalig.\n")
    name = normalize_username(ask("Benutzername: "))
    if ask("Benutzername wiederholen: ").strip().lower() != name:
        out("Die beiden Benutzernamen stimmen nicht überein.")
        return 1
    password = ask("Passwort: ")
    if ask("Passwort wiederholen: ") != password:
        out("Die beiden Passwörter stimmen nicht überein.")
        return 1
    if not MIN_DEVELOPER_USERNAME <= len(name) <= 50:
        out(f"Der Benutzername muss {MIN_DEVELOPER_USERNAME} bis 50 Zeichen lang sein.")
        return 1
    if len(password) < MIN_DEVELOPER_PASSWORD or len(password) > MAX_PASSWORD_LENGTH or len(set(password)) < 8:
        out(f"Das Passwort muss mindestens {MIN_DEVELOPER_PASSWORD} Zeichen (mit vielen verschiedenen) haben.")
        return 1
    out("\nFertig. Gib NUR diese zwei Zeilen weiter (nie Benutzername oder Passwort selbst):\n")
    out(f'USERNAME_HASH = "{hash_password(name)}"')
    out(f'PASSWORD_HASH = "{hash_password(password)}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
