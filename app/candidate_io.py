"""Export und Import von Wahlgängen samt Kandidat/innen (JSON, mit Fotos).

Zweck: Eine Wahl einmal vorbereiten, sichern und für Testläufe, Wiederholungen oder das nächste Jahr wieder einspielen.
Die Datei enthält nur Wahlgänge, Abteilungsnamen und Kandidat/innen - keine Codes, keine Stimmen, keine Benutzer.
"""
import base64
import binascii
import json
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from . import images
from .models import Candidate, Department, Election, Image, Race

FORMAT = "schulwahl-kandidaten"
VERSION = 1
MAX_FILE_BYTES = 6 * 1024 * 1024
MAX_RACES = 50
MAX_CANDIDATES = 500
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ImportProblem(Exception):
    """Fehler in der Importdatei (Meldung ist für Anwender/innen gedacht)."""


@dataclass
class ParsedCandidate:
    """Ein geprüfter Kandidat aus der Importdatei (Foto ist bereits kontrolliert und neu kodiert)."""
    name: str
    info: str = ""
    photo: tuple[bytes, str] | None = None  # bereits geprüft und neu kodiert


@dataclass
class ParsedRace:
    """Ein geprüfter Wahlgang aus der Importdatei mit seinen Kandidat/innen."""
    title: str
    department: str | None
    candidates: list[ParsedCandidate] = field(default_factory=list)


@dataclass
class Parsed:
    """Ergebnis von `parse`: alle Wahlgänge plus Anzahl der verworfenen Fotos."""
    races: list[ParsedRace]
    photo_problems: int = 0


def export_election(db: Session, election: Election) -> bytes:
    """Erzeugt die Export-Datei (JSON, UTF-8) mit allen Wahlgängen, Kandidat/innen und Fotos einer Wahl."""
    races = []
    for race in sorted(election.races, key=lambda r: (r.position, r.id)):
        cands = []
        for c in race.candidates:
            item = {"name": c.name, "info": c.info}
            img = db.get(Image, c.image_id) if c.image_id else None
            if img is not None:
                item["photo"] = base64.b64encode(img.data).decode()
                item["photo_type"] = img.content_type
            cands.append(item)
        dept = next((d.name for d in election.departments if d.id == race.department_id), None)
        races.append({"title": race.title, "department": dept, "candidates": cands})
    doc = {"format": FORMAT, "version": VERSION, "election": election.name, "races": races}
    return json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")


def _text(value, limit: int, what: str, required: bool = False) -> str:
    """Prüft ein Textfeld der Importdatei: Typ, Steuerzeichen entfernen, kürzen; mit `required` darf es nicht leer sein."""
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ImportProblem(f"Ungültige Datei: „{what}“ muss Text sein.")
    value = _CONTROL.sub("", value).strip()[:limit]
    if required and not value:
        raise ImportProblem(f"Ungültige Datei: „{what}“ darf nicht leer sein.")
    return value


def parse(raw: bytes | None) -> Parsed:
    """Prüft die Datei vollständig, ohne etwas zu speichern."""
    if not raw:
        raise ImportProblem("Bitte eine Datei auswählen.")
    if len(raw) > MAX_FILE_BYTES:
        raise ImportProblem("Die Datei ist zu groß (max. 6 MB).")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ImportProblem("Das ist keine gültige Export-Datei der Schulwahl (JSON erwartet).") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ImportProblem("Das ist keine Kandidaten-Export-Datei der Schulwahl.")
    if doc.get("version") != VERSION:
        raise ImportProblem("Diese Datei stammt aus einer nicht unterstützten Version.")
    races_raw = doc.get("races")
    if not isinstance(races_raw, list) or not races_raw:
        raise ImportProblem("Die Datei enthält keine Wahlgänge.")
    if len(races_raw) > MAX_RACES:
        raise ImportProblem(f"Zu viele Wahlgänge (max. {MAX_RACES}).")

    out, total, bad_photos = [], 0, 0
    for r in races_raw:
        if not isinstance(r, dict) or not isinstance(r.get("candidates", []), list):
            raise ImportProblem("Ungültige Datei: Aufbau eines Wahlgangs stimmt nicht.")
        dept = _text(r.get("department"), 100, "Abteilung") or None
        race = ParsedRace(_text(r.get("title"), 200, "Titel", required=True), dept)
        for c in r.get("candidates", []):
            if not isinstance(c, dict):
                raise ImportProblem("Ungültige Datei: Aufbau eines Kandidaten stimmt nicht.")
            total += 1
            if total > MAX_CANDIDATES:
                raise ImportProblem(f"Zu viele Kandidat/innen (max. {MAX_CANDIDATES}).")
            cand = ParsedCandidate(_text(c.get("name"), 200, "Name", required=True), _text(c.get("info"), 200, "Info"))
            photo = c.get("photo")
            if photo:
                try:
                    if not isinstance(photo, str) or len(photo) > 8_000_000:
                        raise ValueError
                    cand.photo = images.process(base64.b64decode(photo, validate=True), "candidate")
                except (ValueError, binascii.Error, images.ImageError):
                    bad_photos += 1  # Kandidat wird ohne Foto übernommen
            race.candidates.append(cand)
        out.append(race)
    return Parsed(out, bad_photos)


def apply(db: Session, election: Election, parsed: Parsed) -> dict:
    """Spielt die geprüfte Datei ein (ohne commit). Vorhandene gleichnamige Wahlgänge werden ergänzt, Doppelte übersprungen."""
    db.flush()  # frisch angelegte Wahlgänge (z. B. Standard-Wahlgänge) brauchen ihre IDs
    stats = {"races": 0, "candidates": 0, "skipped": 0, "departments": 0, "photo_problems": parsed.photo_problems}
    depts = {d.name.lower(): d for d in election.departments}
    races = {(r.title.lower(), r.department_id): r for r in election.races}
    position = max((r.position for r in election.races), default=-1) + 1
    for pr in parsed.races:
        dept_id = None
        if pr.department:
            dept = depts.get(pr.department.lower())
            if dept is None:
                dept = Department(election_id=election.id, name=pr.department)
                db.add(dept)
                db.flush()
                depts[pr.department.lower()] = dept
                stats["departments"] += 1
            dept_id = dept.id
        race = races.get((pr.title.lower(), dept_id))
        if race is None:
            race = Race(election_id=election.id, department_id=dept_id, title=pr.title, position=position)
            position += 1
            db.add(race)
            db.flush()
            races[(pr.title.lower(), dept_id)] = race
            stats["races"] += 1
        known = {c.name.lower() for c in race.candidates}
        for pc in pr.candidates:
            if pc.name.lower() in known:
                stats["skipped"] += 1
                continue
            cand = Candidate(race_id=race.id, name=pc.name, info=pc.info)
            if pc.photo:
                img = Image(data=pc.photo[0], content_type=pc.photo[1])
                db.add(img)
                db.flush()
                cand.image_id = img.id
            db.add(cand)
            known.add(pc.name.lower())
            stats["candidates"] += 1
        db.flush()
        db.refresh(race)
    return stats


def summary(stats: dict) -> str:
    """Fasst das Ergebnis eines Imports in einem deutschen Satz für die Erfolgsmeldung zusammen."""
    text = f"{stats['candidates']} Kandidat/innen importiert ({stats['races']} neue Wahlgänge"
    if stats["departments"]:
        text += f", {stats['departments']} neue Abteilungen"
    text += ")"
    if stats["skipped"]:
        text += f"; {stats['skipped']} schon vorhandene übersprungen"
    if stats["photo_problems"]:
        text += f"; {stats['photo_problems']} Foto(s) waren ungültig und wurden weggelassen"
    return text + "."
