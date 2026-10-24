# Übersicht über den Programmcode

Ausführliche Erklärung: [`docs/ENTWICKLUNG.md`](../docs/ENTWICKLUNG.md). Jede Datei beginnt mit einer Beschreibung, jede Funktion hat einen Erklärungssatz.

| Datei / Ordner | Aufgabe |
|---|---|
| `main.py` | App zusammenbauen: Middleware (Sicherheits-Header, Größenlimit, Sitzungen), Fehlerseiten, Routen einhängen |
| `config.py` | Einstellungen aus Umgebungsvariablen (`.env`) und Grenzwerte |
| `db.py` | Datenbank-Verbindung, Tabellen anlegen, neue Spalten nachziehen (`_NEW_COLUMNS`) |
| `models.py` | Tabellen als Python-Klassen (Wahl, Wahlgang, Kandidat, Code, Stimme, Urne, Benutzer, Protokoll …) |
| `services.py` | Fachlogik: Code prüfen, Stimme abgeben, Urne leeren, auszählen, Stichwahl-Empfehlung |
| `security.py` | Passwörter, Anmeldung/Rollen, CSRF-Schutz, Sperre bei Fehlversuchen, Protokoll |
| `passwords.py` | Passwort-Hashing (scrypt), nur Standardbibliothek; `python -m app.passwords` erzeugt die Hash-Zeilen für den Entwicklerzugang |
| `developer_access.py` | Entwickler-Notfallzugang (nur als Hash hinterlegt, versteckt in Listen, im Protokoll sichtbar, per `.env` abschaltbar) |
| `accounts.py` | Benutzer anlegen, Passwort setzen, Ersteinrichtung (Einrichtungscode) |
| `settings.py` | Einstellungen aus der Datenbank: Schulname, Logo, Adresse im QR-Code |
| `candidate_io.py` | Kandidaten exportieren/importieren (Datei, mit strenger Prüfung) |
| `pdfs.py` | Code-Zettel und Wahlprotokoll als schwarz-weiße PDFs |
| `images.py` | Bilder prüfen, verkleinern, neu kodieren |
| `timeutil.py` | Zeit: in der Datenbank immer UTC, Anzeige in der Schulzeitzone |
| `web.py` | Gemeinsame Hilfen für die HTML-Seiten (Vorlagen-Rahmen, Meldungen, Abteilungsfarben) |
| `manage.py` | Kommandozeile für Notfälle (Passwort zurücksetzen, Einrichtungscode anzeigen) |
| `routes/` | Alle Web-Seiten, eine Datei pro Aufgabe (Übersicht in `routes/__init__.py`) |
| `templates/` | HTML-Vorlagen (`base.html` = Rahmen, `admin_*.html` = Backoffice) |
| `static/` | `style.css`, `theme.js`, `app.js`, `fonts/` |
