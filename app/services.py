"""Fachlogik: Codes, Wahlstatus, Stimmabgabe, Auswertung."""
import random
import re
import secrets
import uuid
from datetime import datetime

from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.orm import Session, selectinload

from .models import Candidate, Code, Department, Election, Image, PendingBallot, Race, Vote
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


def generate_codes(db: Session, election: Election, department: Department, count: int, group_name: str = "") -> str:
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
        Code(election_id=election.id, department_id=department.id, code=c, batch=batch, group_name=group_name)
        for c in new
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
    # Zufällige Reihenfolge pro Stimmzettel, damit niemand durch seine Listenposition bevorzugt wird.
    # Bewusst nur ein Attribut am Objekt: die gespeicherte Reihenfolge der Kandidaten bleibt unverändert.
    for race in races:
        shuffled = list(race.candidates)
        random.SystemRandom().shuffle(shuffled)
        race.ballot_candidates = shuffled
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
        # Die Stimmen kommen zunächst in die "Urne" (pending_ballots) und werden erst nach Wahlende gemischt
        # nach votes übernommen (siehe flush_ballots). So verrät die interne Transaktions-ID keine Zuordnung.
        db.add_all(PendingBallot(id=uuid.uuid4().hex, race_id=rid, candidate_id=cid) for rid, cid in choices.items())
        db.commit()
    except VoteError:
        raise
    except Exception:
        db.rollback()
        raise


def flush_ballots(db: Session, election: Election) -> int:
    """Übernimmt nach Wahlende alle Stimmen aus der Urne in `votes` - in einer Transaktion und in zufälliger Reihenfolge.

    Dadurch haben alle Stimmen dieselbe Transaktions-ID und keine erkennbare Reihenfolge; sie lassen sich weder
    einem Code noch einander (z. B. Schulsprecher- und Abteilungsstimme derselben Person) zuordnen.
    Mehrfach und gleichzeitig aufrufbar: jede Stimme wird genau einmal verschoben.
    """
    if election_status(election) != "ended":
        return 0
    race_ids = select(Race.id).where(Race.election_id == election.id)
    if db.scalar(select(PendingBallot.id).where(PendingBallot.race_id.in_(race_ids)).limit(1)) is None:
        db.rollback()  # nichts zu tun (häufigster Fall bei Ansichten der Ergebnisse): keine Schreibzugriffe
        return 0
    rows = db.execute(
        delete(PendingBallot).where(PendingBallot.race_id.in_(race_ids))
        .returning(PendingBallot.race_id, PendingBallot.candidate_id)
    ).all()
    random.SystemRandom().shuffle(rows)
    db.add_all(Vote(id=uuid.uuid4().hex, race_id=rid, candidate_id=cid) for rid, cid in rows)
    db.commit()
    if rows:
        _wipe_empty_urn(db)
    return len(rows)


def _wipe_empty_urn(db: Session) -> None:
    """PostgreSQL: Ist die Urne leer, TRUNCATE statt gelöschter Zeilen, damit keine Reste auf der Platte bleiben."""
    if db.get_bind().dialect.name != "postgresql":
        return
    try:
        db.execute(text("SET LOCAL lock_timeout = '2s'"))
        db.execute(text("LOCK TABLE pending_ballots IN ACCESS EXCLUSIVE MODE"))
        if db.scalar(select(PendingBallot.id).limit(1)) is None:
            db.execute(text("TRUNCATE pending_ballots"))
        db.commit()
    except Exception:  # noqa: BLE001 - reine Aufräumhilfe, darf die Auswertung nie verhindern
        db.rollback()


# ---------------------------------------------------------------- Auswertung

def participation(db: Session, election: Election) -> dict:
    rows = db.execute(
        select(
            Code.department_id,
            Code.group_name,
            func.count(Code.id),
            func.coalesce(func.sum(case((Code.used.is_(True), 1), else_=0)), 0),
        )
        .where(Code.election_id == election.id)
        .group_by(Code.department_id, Code.group_name)
    ).all()

    def pct(used, total):
        return round(100 * used / total, 1) if total else 0.0

    by_dept: dict[int, list[int]] = {}
    groups = []
    names = {d.id: d.name for d in election.departments}
    for dept_id, group, total, used in rows:
        t = by_dept.setdefault(dept_id, [0, 0])
        t[0] += int(total)
        t[1] += int(used)
        if group:
            groups.append({"department_id": dept_id, "department": names.get(dept_id, ""), "name": group,
                           "total": int(total), "used": int(used), "percent": pct(int(used), int(total))})
    groups.sort(key=lambda g: (g["department"], g["name"]))
    departments = []
    for dept in election.departments:
        total, used = by_dept.get(dept.id, (0, 0))
        departments.append({"id": dept.id, "name": dept.name, "total": total, "used": used, "percent": pct(used, total),
                            "groups": [g for g in groups if g["department_id"] == dept.id]})
    total = sum(d["total"] for d in departments)
    used = sum(d["used"] for d in departments)
    return {"departments": departments, "groups": groups, "total": total, "used": used, "percent": pct(used, total)}


def decide(rows: list[dict], runoff_kind: str = "") -> dict:
    """Platz 1 = Sprecher/in, Platz 2 = Stellvertreter/in. Eine Stichwahl ist nur bei Gleichstand nötig.

    Nur Kandidat/innen mit mindestens einer Stimme können einen Platz bekommen.
    """
    by_votes: dict[int, list] = {}
    for r in rows:
        if r["votes"] > 0:
            by_votes.setdefault(r["votes"], []).append(r["candidate"])
    ordered = [by_votes[v] for v in sorted(by_votes, reverse=True)]
    out = {"winner": None, "deputy": None, "runoff_first": [], "runoff_second": [], "deputy_pending": False,
           "leaders": ordered[0] if ordered else []}
    if not ordered:
        return out
    first = ordered[0]
    if len(first) > 1:
        out["runoff_first"] = first
        out["deputy_pending"] = runoff_kind != "deputy"  # Stellvertretung ergibt sich nach der Stichwahl
        return out
    out["winner"] = first[0]
    if runoff_kind == "deputy":
        return out  # bei einer Stichwahl um die Stellvertretung wird nur ein Platz vergeben
    if len(ordered) > 1:
        second = ordered[1]
        if len(second) == 1:
            out["deputy"] = second[0]
        else:
            out["runoff_second"] = second
    return out


def runoff_advice(rows: list[dict], decision: dict, valid: int, runoff_kind: str = "") -> dict:
    """Empfehlung zur Stichwahl (die Wahlleitung entscheidet) und die Kandidat/innen, die in einer Stichwahl antreten würden.

    Empfohlen wird eine Stichwahl bei Stimmengleichheit und bei mehr als zwei Kandidat/innen, wenn niemand mehr als die
    Hälfte der gültigen Stimmen hat. Bei nur zwei Kandidat/innen bringt eine Stichwahl nichts.
    """
    n = len(rows)
    top = rows[0] if rows else None
    majority = bool(valid) and top["votes"] * 2 > valid
    if valid == 0:
        recommend, reason = False, "Es gibt keine gültigen Stimmen."
    elif decision["runoff_first"]:
        recommend, reason = True, "Stimmengleichheit um Platz 1 - ohne Stichwahl gibt es keine Entscheidung."
    elif runoff_kind == "deputy":
        recommend, reason = False, "Das war bereits die Stichwahl um die Stellvertretung."
    elif n <= 2:
        recommend, reason = False, (f"Nur {n} Kandidat/innen - eine Stichwahl bringt hier nichts "
                                    f"({top['candidate'].name}: {top['percent']:.1f} % der gültigen Stimmen).")
    elif majority and decision["runoff_second"]:
        recommend, reason = True, "Stimmengleichheit um Platz 2 (Stellvertretung)."
    elif majority:
        recommend, reason = False, f"{top['candidate'].name} hat die absolute Mehrheit ({top['percent']:.1f} % der gültigen Stimmen)."
    else:
        recommend = True
        reason = (f"{top['candidate'].name} hat nur {top['percent']:.1f} % der gültigen Stimmen - keine absolute Mehrheit "
                  f"(mehr als 50 %) bei {n} Kandidat/innen.")

    voted = [r for r in rows if r["votes"] > 0]
    if decision["runoff_first"]:
        plan_kind, plan = "speaker", list(decision["runoff_first"])
    elif majority and decision["runoff_second"] and runoff_kind != "deputy":
        plan_kind, plan = "deputy", list(decision["runoff_second"])
    elif voted:
        second_votes = next((r["votes"] for r in voted if r["votes"] < voted[0]["votes"]), None)
        group = [r["candidate"] for r in voted if second_votes is not None and r["votes"] == second_votes]
        plan_kind, plan = "speaker", ([voted[0]["candidate"]] + group if group else [])
    else:
        plan_kind, plan = "speaker", []
    if runoff_kind == "deputy" and plan_kind == "speaker":
        plan_kind = "deputy"
    return {"recommend": recommend, "reason": reason, "majority": majority, "plan_kind": plan_kind, "plan": plan}


def compute_results(db: Session, election: Election) -> list[dict]:
    flush_ballots(db, election)
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
        decision = decide(rows, race.runoff_kind)
        expected = part["used"] if race.department_id is None else used_by_dept.get(race.department_id, 0)
        advice = runoff_advice(rows, decision, valid, race.runoff_kind)
        results.append({
            "race": race,
            "department": race.department.name if race.department else None,
            "rows": rows,
            "valid": valid,
            "invalid": invalid,
            "total": valid + invalid,
            "expected": expected,
            "consistent": valid + invalid == expected,
            "tie": len(decision["leaders"]) > 1,
            "decision": race.runoff_decision,
            **advice,
            **decision,
        })
    return results


def delete_election(db: Session, election: Election) -> None:
    """Löscht eine Wahl samt Codes und Stimmen in FK-sicherer Reihenfolge."""
    race_ids = select(Race.id).where(Race.election_id == election.id)
    image_ids = list(db.scalars(select(Candidate.image_id).where(Candidate.race_id.in_(race_ids),
                                                                   Candidate.image_id.is_not(None))))
    db.execute(delete(PendingBallot).where(PendingBallot.race_id.in_(race_ids)))
    db.execute(delete(Vote).where(Vote.race_id.in_(race_ids)))
    db.execute(delete(Candidate).where(Candidate.race_id.in_(race_ids)))
    if image_ids:
        db.execute(delete(Image).where(Image.id.in_(image_ids)))
    db.execute(delete(Code).where(Code.election_id == election.id))
    db.execute(delete(Race).where(Race.election_id == election.id))
    db.execute(delete(Department).where(Department.election_id == election.id))
    db.execute(delete(Election).where(Election.id == election.id))
    db.commit()
