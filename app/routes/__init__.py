"""Alle Web-Seiten der Anwendung, nach Aufgabe getrennt (jede Datei hat einen eigenen `router`).

Schüler/innen (öffentlich, ohne Anmeldung):
    vote.py        Code eingeben, Stimmzettel, Zusammenfassung, Abgabe
    public.py      veröffentlichte Ergebnisse, Archiv "Vergangene Wahlen", Kandidatenfotos, Logo

Backoffice (alles unter /admin, nur mit Anmeldung; Rollen siehe security.py):
    auth.py        Anmeldung, Abmeldung, eigenes Konto
    users.py       Benutzer anlegen/zurücksetzen, Protokoll            (nur Administrator)
    settings.py    Schulname, Logo, Adresse im QR-Code                  (nur Administrator)
    elections.py   Wahlen anlegen/steuern, Dashboard, Stichwahlen, CSV/Protokoll
    candidates.py  Wahlgänge und Kandidat/innen, Export/Import
    codes.py       Wahlcodes erzeugen, auflisten, PDF

common.py enthält Hilfen, die mehrere dieser Dateien brauchen.
Neue Seite hinzufügen: siehe docs/ENTWICKLUNG.md.
"""
