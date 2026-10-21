"""Datenmodell.

WAHLGEHEIMNIS: Die Tabelle `votes` hat bewusst KEINEN Verweis auf `codes`, keinen Zeitstempel
und eine zufällige ID. Ein Code weiß nur, DASS er verwendet wurde - nicht, was gewählt wurde.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .timeutil import utcnow


class Base(DeclarativeBase):
    pass


class Election(Base):
    __tablename__ = "elections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    results_public: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    departments: Mapped[list["Department"]] = relationship(
        back_populates="election", cascade="all, delete-orphan", order_by="Department.name"
    )
    races: Mapped[list["Race"]] = relationship(
        back_populates="election", cascade="all, delete-orphan", order_by="Race.position, Race.id"
    )
    codes: Mapped[list["Code"]] = relationship(cascade="all, delete-orphan", passive_deletes=False)


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))

    election: Mapped[Election] = relationship(back_populates="departments")


class Race(Base):
    """Ein Wahlgang auf dem Stimmzettel, z. B. 'Schulsprecher/in' (department_id leer = alle)."""

    __tablename__ = "races"

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), default=None)
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)

    election: Mapped[Election] = relationship(back_populates="races")
    department: Mapped[Department | None] = relationship()
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="race", cascade="all, delete-orphan", order_by="Candidate.name"
    )
    votes: Mapped[list["Vote"]] = relationship(cascade="all, delete-orphan")


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    info: Mapped[str] = mapped_column(String(200), default="")
    image_id: Mapped[int | None] = mapped_column(ForeignKey("images.id"), default=None)

    race: Mapped[Race] = relationship(back_populates="candidates")


class Code(Base):
    __tablename__ = "codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    batch: Mapped[str] = mapped_column(String(32), default="", index=True)

    department: Mapped[Department] = relationship()


class Vote(Base):
    __tablename__ = "votes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # zufällige UUID, keine Reihenfolge
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"), default=None)
    # candidate_id = NULL bedeutet: ungültige Stimme


class Image(Base):
    """Hochgeladene, bereits verkleinerte Bilder (Kandidatenfotos, Schullogo)."""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_type: Mapped[str] = mapped_column(String(50))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), default="")
