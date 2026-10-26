"""Dokumentation bleibt vollständig: jede Datei, Klasse und Funktion im Programmcode ist erklärt, jede Datei in app/README.md aufgeführt."""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"
SOURCES = sorted(list(APP.glob("*.py")) + list((APP / "routes").glob("*.py")))
# Kleine innere Hilfsfunktionen der Middleware und Konstruktoren brauchen keinen eigenen Text
SKIP = {"__init__", "__call__", "too_large", "limited_receive", "tracking_send", "send_with_headers"}


def test_every_module_class_and_function_has_an_explanation():
    missing = []
    for f in SOURCES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        if not ast.get_docstring(tree):
            missing.append(f"{f.relative_to(ROOT)} (Datei)")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name not in SKIP and not ast.get_docstring(node):
                missing.append(f"{f.relative_to(ROOT)}: {node.name}")
    assert not missing, "Bitte einen kurzen Erklärungssatz (Docstring) ergänzen:\n" + "\n".join(missing)


def test_every_source_file_is_listed_in_the_overview():
    overview = (APP / "README.md").read_text(encoding="utf-8")
    unlisted = [f.name for f in APP.glob("*.py") if f.name != "__init__.py" and f"`{f.name}`" not in overview]
    assert not unlisted, f"In app/README.md fehlen: {unlisted}"
    for f in (APP / "routes").glob("*.py"):
        if f.name != "__init__.py":
            assert f.stem in (APP / "routes" / "__init__.py").read_text(encoding="utf-8"), f"{f.name} fehlt in routes/__init__.py"


def test_developer_docs_exist_and_mention_key_topics():
    text = (ROOT / "docs" / "ENTWICKLUNG.md").read_text(encoding="utf-8")
    for topic in ("Dateistruktur", "Urne", "_NEW_COLUMNS", "Ersteinrichtung", "require_staff", "Fehler suchen"):
        assert topic in text, topic
