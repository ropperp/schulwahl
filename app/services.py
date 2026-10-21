"""Fachlogik: Codes, Wahlstatus, Stimmabgabe, Auswertung."""
import re
import secrets
import uuid
from datetime import datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from .models import Candidate, Code, Department, Election, Race, Vote
from .timeutil import fmt_local, utcnow

# Ohne leicht verwechselbare Zeichen (kein I, O, 0, 1): 32 Zeichen ^ 6 Stellen = ~1 Mrd. Kombinationen
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


class VoteError(Exception):
    def __init__(self, message: str, kind: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.kind = kind  # notfound | used | closed | notstarted | choice


# ---------------------------------------------------------------- Status

def election_status(e: Election, now: datetime | None = None) -> str:
    """planned | running | ended"""
    now = now or utcnow()
    if e.closed_at is not None and e.closed_at <= now:
        return "ended"
    if now < e.start_at:
        return "planned"
    if now >= e.end_at:
        return "ended"
    return "running"


STATUS_LABEL = {"planned": "Geplant", "running": "Läuft", "ended": "Beendet"}


# ---------------------------------------------------------------- Codes

def normalize_code(raw: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def generate_codes(db: Session, election: Election, department: Department, count: int) -> str:
    """Erzeugt `count` neue, eindeutige Codes. Gibt die Batch-ID zurück."""
    batch = uuid.uuid4().hex[:16]
    existing = set(db.scalars(select(Code.code)))
    new: list[str] = []
    while len(new) < count:
        c = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if c not in existing:
            existing.add(c)
            new.append(c)
    db.add_all(
        Code(election_id=election.id, department_id=department.id, code=c, batch=batch) for c in new
    )
    db.commit()
    return batch


# ---------------------------------------------------------------- Stimmabgabe

def lookup_code(db: Session, raw: str | None) -> Code:
    code = normalize_code(raw)
    found = db.scalar(select(Code).where(Code.code == code)) if len(code) == CODE_LENGTH else None
    if found is None:
        raise VoteError("Dieser Code ist ungültig. Bitte prüfe die Eingabe.", "notfound")
    return found


def ballot_for(db: Session, code: Code) -> tuple[Election, list[Race]]:
    """Prüft, ob mit dem Code jetzt gewählt werden darf, und liefert den Stimmzettel."""
    election = db.get(Election, code.election_id)
    status = election_status(election)
    if status == "planned":
        raise VoteError(f"Die Wahl beginnt erst am {fmt_local(election.start_at)} Uhr.", "notstarted")
    if status == "ended":
        raise VoteError("Die Wahl ist beendet.", "closed")
    if code.used:
        raise VoteError("Dieser Code wurde bereits verwendet.", "used")
    races = list(
        db.scalars(
            select(Race)
            .where(Race.election_id == election.id)
            .where((Race.department_id.is_(None)) | (Race.department_id == code.department_id))
            .options(selectinload(Race.candidates))
            .order_by(Race.position, Race.id)
        )
    )
    return election, races


def parse_choices(races: list[Race], form) -> dict[int, int | None]:
    """Liest die Auswahl aus dem Formular. None = ungültige Stimme."""
    out: dict[int, int | None] = {}
    for race in races:
        raw = form.get(f"race_{race.id}")
        if not raw:
            raise VoteError(f"Bitte triff eine Auswahl bei „{race.title}“.", "choice")
        if raw == "invalid":
            out[race.id] = None
            continue
        try:
            cid = int(raw)
        except ValueError:
            raise VoteError("Ungültige Auswahl.", "choice") from None
        if cid not in {c.id for c in race.candidates}:
            raise VoteError("Ungültige Auswahl.", "choice")
        out[race.id] = cid
    return out


def cast_vote(db: Session, raw_code: str, form) -> None:
    """Gibt alle Stimmen eines Codes atomar ab und verbraucht den Code."""
    code = lookup_code(db, raw_code)
    _, races = ballot_for(db, code)
    choices = parse_choices(races, form)
    try:
        # Atomar: nur der erste gleichzeitige Versuch trifft used=False und gewinnt.
        result = db.execute(
            update(Code).where(Code.id == code.id, Code.used.is_(False)).values(used=True)
        )
        if result.rowcount != 1:
            db.rollback()
            raise VoteError("Dieser Code wurde bereits verwendet.", "used")
        db.add_all(Vote(id=uuid.uuid4().hex, race_id=rid, candidate_id=cid) for rid, cid in choices.items())
        db.commit()
    except VoteError:
        raise
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------- Auswertung

def participation(db: Session, election: Election) -> dict:
    rows = db.execute(
        select(
            Code.department_id,
            func.count(Code.id),
            func.coalesce(func.sum(case((Code.used.is_(True), 1), else_=0)), 0),
        )
        .where(Code.election_id == election.id)
        .group_by(Code.department_id)
    ).all()
    by_dept = {d: (int(total), int(used)) for d, total, used in rows}
    departments = []
    for dept in election.departments:
        total, used = by_dept.get(dept.id, (0, 0))
        departments.append({"id": dept.id, "name": dept.name, "total": total, "used": used,
                            "percent": round(100 * used / total, 1) if total else 0.0})
    total = sum(d["total"] for d in departments)
    used = sum(d["used"] for d in departments)
    return {"departments": departments, "total": total, "used": used,
            "percent": round(100 * used / total, 1) if total else 0.0}


def compute_results(db: Session, election: Election) -> list[dict]:
    races = list(
        db.scalars(
            select(Race)
            .where(Race.election_id == election.id)
            .options(selectinload(Race.candidates), selectinload(Race.department))
            .order_by(Race.position, Race.id)
        )
    )
    counts: dict[tuple[int, int | None], int] = {
        (rid, cid): n
        for rid, cid, n in db.execute(
            select(Vote.race_id, Vote.candidate_id, func.count(Vote.id))
            .join(Race, Race.id == Vote.race_id)
            .where(Race.election_id == election.id)
            .group_by(Vote.race_id, Vote.candidate_id)
        )
    }
    part = participation(db, election)
    used_by_dept = {d["id"]: d["used"] for d in part["departments"]}

    results = []
    for race in races:
        rows = [{"candidate": c, "votes": counts.get((race.id, c.id), 0)} for c in race.candidates]
        rows.sort(key=lambda r: (-r["votes"], r["candidate"].name.lower()))
        valid = sum(r["votes"] for r in rows)
        invalid = counts.get((race.id, None), 0)
        for r in rows:
            r["percent"] = round(100 * r["votes"] / valid, 1) if valid else 0.0
        top = rows[0]["votes"] if rows else 0
        leaders = [r["candidate"] for r in rows if r["votes"] == top] if top > 0 else []
        expected = part["used"] if race.department_id is None else used_by_dept.get(race.department_id, 0)
        results.append({
            "race": race,
            "department": race.department.name if race.department else None,
            "rows": rows,
            "valid": valid,
            "invalid": invalid,
            "total": valid + invalid,
            "expected": expected,
            "consistent": valid + invalid == expected,
            "leaders": leaders,
            "tie": len(leaders) > 1,
        })
    return results


def delete_election(db: Session, election: Election) -> None:
    """Löscht eine Wahl samt Codes und Stimmen in FK-sicherer Reihenfolge."""
    race_ids = select(Race.id).where(Race.election_id == election.id)
    db.execute(delete(Vote).where(Vote.race_id.in_(race_ids)))
    db.execute(delete(Candidate).where(Candidate.race_id.in_(race_ids)))
    db.execute(delete(Code).where(Code.election_id == election.id))
    db.execute(delete(Race).where(Race.election_id == election.id))
    db.execute(delete(Department).where(Department.election_id == election.id))
    db.execute(delete(Election).where(Election.id == election.id))
    db.commit()
