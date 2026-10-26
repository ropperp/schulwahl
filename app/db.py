import time

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from . import config
from .models import Base

_connect_args = {"check_same_thread": False} if config.DATABASE_URL.get_backend_name() == "sqlite" else {}
engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries: int = 30) -> None:
    """Tabellen anlegen; wartet kurz, falls die Datenbank noch hochfährt."""
    for attempt in range(retries):
        try:
            with engine.begin() as conn:
                if conn.dialect.name == "postgresql":
                    # Mehrere Worker starten gleichzeitig: nur einer legt Tabellen an, die anderen warten.
                    conn.execute(text("SELECT pg_advisory_xact_lock(727274)"))
                Base.metadata.create_all(conn)
                _migrate(conn)
            return
        except OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(1)


_NEW_COLUMNS = [
    ("candidates", "image_id", "INTEGER REFERENCES images(id)"),
    ("elections", "code_mode", "VARCHAR(20) NOT NULL DEFAULT 'department'"),
    ("races", "runoff_kind", "VARCHAR(20) NOT NULL DEFAULT ''"),
    ("codes", "group_name", "VARCHAR(100) NOT NULL DEFAULT ''"),
]


def _migrate(conn) -> None:
    """Ergänzt Spalten, die in älteren Versionen noch fehlten (create_all ändert keine bestehenden Tabellen)."""
    insp = inspect(conn)
    for table, column, ddl in _NEW_COLUMNS:
        if column not in {c["name"] for c in insp.get_columns(table)}:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
