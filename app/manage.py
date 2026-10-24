"""Kommandozeile für Notfälle, z. B. wenn das Admin-Passwort vergessen wurde.

Im Docker-Betrieb:
    docker compose exec app python -m app.manage list-users
    docker compose exec app python -m app.manage reset-password admin
    docker compose exec app python -m app.manage create-user lehrer1 --role wahlleiter
"""
import argparse
import sys

from sqlalchemy import select

from .accounts import set_password
from .db import SessionLocal, init_db
from .models import User
from .security import (ROLE_ADMIN, ROLE_WAHLLEITER, hash_password, normalize_username, random_password,
                       username_problem)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.manage", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-users", help="Alle Benutzer anzeigen")
    reset = sub.add_parser("reset-password", help="Neues Zufallspasswort setzen (muss beim nächsten Login geändert werden)")
    reset.add_argument("username")
    create = sub.add_parser("create-user", help="Benutzer mit Zufallspasswort anlegen")
    create.add_argument("username")
    create.add_argument("--role", choices=[ROLE_ADMIN, ROLE_WAHLLEITER], default=ROLE_WAHLLEITER)
    args = parser.parse_args(argv)

    init_db()
    with SessionLocal() as db:
        if args.cmd == "list-users":
            for u in db.scalars(select(User).order_by(User.username)):
                print(f"{u.username:20s} {u.role:12s}{'  (muss Passwort ändern)' if u.must_change else ''}")
            return 0
        name = normalize_username(args.username)
        user = db.scalar(select(User).where(User.username == name))
        password = random_password()
        if args.cmd == "reset-password":
            if user is None:
                print(f"Benutzer „{name}“ gibt es nicht.", file=sys.stderr)
                return 1
            set_password(db, user, password, must_change=True)
        else:
            problem = username_problem(name)
            if problem or user is not None:
                print(problem or f"Benutzer „{name}“ gibt es schon.", file=sys.stderr)
                return 1
            db.add(User(username=name, role=args.role, password_hash=hash_password(password), must_change=True))
        db.commit()
    print(f"Benutzer: {name}\nNeues Passwort: {password}\n(Muss beim ersten Login geändert werden.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
