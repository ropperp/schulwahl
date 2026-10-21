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
            Base.metadata.create_all(engine)
            _migrate()
            return
        except OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(1)


def _migrate() -> None:
    """Ergänzt Spalten, die in älteren Versionen noch fehlten (create_all ändert keine bestehenden Tabellen)."""
    cols = {c["name"] for c in inspect(engine).get_columns("candidates")}
    if "image_id" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE candidates ADD COLUMN image_id INTEGER REFERENCES images(id)"))
