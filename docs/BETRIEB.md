# Betrieb: Ablauf der Wahl

Für die Wahlleitung (Lehrkräfte) und die Administration. Die technische Einrichtung steht in [`INSTALLATION.md`](INSTALLATION.md).

## Rollen

| | Administrator/in | Wahlleiter/in |
|---|:-:|:-:|
| Wahlen anlegen, Kandidat/innen, Codes, Start/Ende, Ergebnis, Stichwahl | ✔ | ✔ |
| Einstellungen (Schulname, Logo, **QR-Adresse**) | ✔ | – |
| Benutzer anlegen/löschen, Wahlleiter-Passwort zurücksetzen | ✔ | – |
| Wahl löschen, Protokoll ansehen | ✔ | – |

Jede Person hat einen **eigenen Zugang** und ändert ihr Passwort selbst unter *Mein Konto*. Nach dem Abmelden oder einer Passwortänderung
sind alle bisherigen Anmeldungen dieses Kontos beendet. Empfehlung: **Vier-Augen-Prinzip** – Codes nie allein ausdrucken und verwalten (siehe unten).

## Zeitplan

### Wochen vorher
1. Kandidat/innen sammeln (Name, Klasse, Foto – **Foto nur mit Einverständnis**, bei Minderjährigen der Erziehungsberechtigten).
2. Testlauf und Lasttest durchführen (siehe `INSTALLATION.md`, Kapitel 6). Wahlleiter-Zugänge anlegen.

### Einige Tage vorher
3. Backoffice → **Neue Wahl**. Zeitfenster festlegen und die Betriebsart der Codes wählen:
   - **pro Abteilung** (datensparsam, empfohlen) – die Beteiligung ist je Abteilung sichtbar,
   - **pro Klasse** – zusätzlich Beteiligung je Klasse (z. B. für einen Klassenwettbewerb).
4. Reiter **Kandidat/innen** füllen – oder aus einer früheren Wahl **importieren** (siehe unten). Ab Wahlbeginn ist nichts mehr änderbar. Die Reihenfolge der Kandidat/innen auf dem Stimmzettel wird **für jeden Wähler zufällig** gemischt.
5. Unter **Einstellungen** die QR-Adresse prüfen. **Erst danach** Codes erzeugen und drucken (die Adresse steckt im QR-Code).

### Codes drucken und verteilen
6. Reiter **Codes** → Abteilung (bzw. Klasse) und Anzahl wählen (Anzahl der Schüler/innen **plus ca. 5 % Reserve**) → das PDF öffnet sich (24 Zettel pro A4-Seite).
7. Ausdrucken, ausschneiden, **abzählen und sicher verwahren**. Verteilung am Wahltag durch die Klassenvorstände, je Klasse ein Umschlag mit exakter Stückzahl.
8. Jeder Code gilt **einmal**. Wer den Zettel hat, kann wählen – also nicht weitergeben und nicht fotografieren.

### Am Wahltag
9. Die Wahl startet und endet automatisch nach dem Zeitfenster (oder per *Jetzt starten* / *Jetzt beenden*; das Zeitfenster lässt sich auch verlängern).
10. **Beteiligung live** im Dashboard (aktualisiert sich alle 10 Sekunden). Ergebnisse sind **bis zum Ende gesperrt** – auch für Administratoren.
11. **Nicht alle in derselben Sekunde wählen lassen.** Das System schafft 1000 Wähler auf einmal, aber mit Wartezeiten von einigen Sekunden. Besser: ein Zeitfenster von
    1–2 Stunden und die Klassen **nacheinander** (z. B. in der jeweiligen Unterrichtsstunde im Klassenraum) wählen lassen. Gewählt wird mit dem eigenen Handy über Mobilfunk oder WLAN.

### Nach der Wahl
12. **Dashboard** → Ergebnis prüfen. Die Konsistenzprüfung vergleicht abgegebene Stimmen mit verbrauchten Codes; eine rote Warnung bedeutet „Wahlleitung und Administration verständigen, nichts löschen“.
13. **Stichwahl entscheiden – bei jedem Wahlgang.** Das System gibt nur eine **Empfehlung**, die Entscheidung trifft die Wahlleitung:
    - *Stichwahl empfohlen*, wenn es mehr als zwei Kandidat/innen gab und niemand mehr als 50 % der gültigen Stimmen hat, oder bei Stimmengleichheit.
    - *Keine Stichwahl empfohlen* bei absoluter Mehrheit oder nur zwei Kandidat/innen (da bringt eine Stichwahl nichts).
    - Mit **„Ja, Stichwahl durchführen“** erscheint der Button *Stichwahl anlegen*: Er legt eine neue Wahl mit den **zwei Bestplatzierten** an (bei Gleichstand um Platz 2 alle Gleichplatzierten). In der Stichwahl ist der/die Unterlegene automatisch **Stellvertreter/in**. Neue Codes erzeugen und drucken.
    - Mit **„Nein, keine Stichwahl“** gilt das Ergebnis: Platz 1 gewählt, **Platz 2 ist Stellvertreter/in**.
    - Solange bei einem Wahlgang nichts entschieden ist, lässt sich das Ergebnis **nicht veröffentlichen**.
    - Nach dem Veröffentlichen ist die Entscheidung **gesperrt**. Mit *Veröffentlichung zurücknehmen* lässt sie sich ändern (auch wieder „offen“ setzen).
14. **Protokoll-PDF** (enthält die Entscheidung der Wahlleitung) und **CSV** herunterladen, vom Wahlleiter und einer zweiten Person unterschreiben lassen, ablegen.
15. Optional **Ergebnis veröffentlichen** (erscheint auf der Startseite unter „Vergangene Wahlen“; bei „Stichwahl“ steht dort, wer antritt).
16. **Nicht verbrauchte Zettel einsammeln und vernichten.**

## Kandidat/innen sichern und wieder einspielen (Export/Import)

Für Testläufe, Wiederholungen oder das nächste Jahr: Auf der Seite **Kandidat/innen** lädt *Kandidat/innen exportieren* eine Datei (JSON) mit allen Wahlgängen,
Kandidat/innen, Klassen und Fotos herunter. Sie enthält **keine Codes, Stimmen oder Benutzer**.
Eingespielt wird sie mit *Importieren* auf der Kandidaten-Seite einer **noch nicht gestarteten** Wahl oder direkt beim Anlegen einer **neuen Wahl** (Feld „Kandidat/innen aus einer Export-Datei übernehmen“).
Gleichnamige Wahlgänge werden ergänzt, schon vorhandene Kandidat/innen übersprungen, fehlende Abteilungen angelegt. Typischer Testablauf: Wahl anlegen und Kandidat/innen eintragen → exportieren → testen und beenden →
neue Wahl anlegen und die Datei importieren.

## Häufige Fragen am Wahltag

| Situation | Was tun |
|---|---|
| „Dieser Code ist ungültig“ | Tippfehler? (Der Code hat keine Zeichen wie 0/O oder 1/I.) Sonst Zettel prüfen: stammt er aus einer anderen Wahl? |
| „Dieser Code wurde bereits verwendet“ | Der Code wurde schon benutzt (weitergegeben/doppelt ausgegeben?). Wahlleitung verständigt; ggf. eine/n Reservecode ausgeben. Die Stimme zum alten Code bleibt gültig |
| Zettel verloren/zerstört | Reiter *Codes* → den Code **löschen** (er ist dann ungültig) und einen neuen erzeugen. Verwendete Codes kann man nie löschen |
| Wahl muss verlängert werden | *Übersicht* → Zeitfenster ändern → *Speichern* (nur solange die Wahl nicht beendet ist) |
| Wahl sofort beenden | *Jetzt beenden* – danach werden keine Stimmen mehr angenommen. **Nicht rückgängig zu machen** |
| Server nicht erreichbar | Auf dem Server: `docker compose ps`, ggf. `docker compose up -d`. Die abgegebenen Stimmen sind in der Datenbank gesichert. Siehe `INSTALLATION.md` → Probleme |
| Passwort vergessen | Wahlleiter: der Admin setzt es zurück (*Benutzer*). Admin: `docker compose exec app python -m app.manage reset-password admin` |
| Schüler/in meldet „Zu viele Fehlversuche“ | Viele Fehleingaben vom selben Netz aus – 5 Minuten warten. Tritt es massenhaft auf, `INSTALLATION.md` → Probleme |

## Wahlgeheimnis in der Praxis

- Auf dem Server wird **nicht gespeichert, wer was gewählt hat**: Der Code wird nur als „benutzt“ markiert; die Stimmen liegen ohne Verweis auf den Code
  in der Urne und werden nach Wahlende **gemischt** ausgezählt. Auch die Wahlleitung sieht nur *ob*, nie *wie* gewählt wurde.
- **Restrisiko Mensch:** Wer unbenutzte Codes sieht (Liste im Backoffice oder PDF), könnte sie selbst benutzen. Deshalb: Codes nur zu zweit erzeugen und drucken,
  Stückzahlen dokumentieren, Überzählige vernichten. Das **Protokoll** (nur Administratoren) zeigt, wer wann Codes erzeugt oder PDFs geöffnet hat.

## Datenschutz und Aufräumen

- Gespeichert werden: anonyme Codes (+ Klasse, wenn „pro Klasse“), Stimmen ohne Personenbezug, Kandidatendaten (Name, Klasse, Foto), Benutzerkonten der Wahlleitung, ein Verwaltungsprotokoll mit IP-Adressen der Backoffice-Anmeldungen.
- Nach Ablauf der Aufbewahrungsfrist (mit Schulleitung/Datenschutzbeauftragten klären, z. B. bis zur nächsten Wahl):
  1. Ergebnis-PDF/CSV archivieren,
  2. im Backoffice die **Wahl löschen** (Administrator) – entfernt Codes, Stimmen und Kandidatenfotos,
  3. alte Dateien in `backups/` löschen (`rm backups/*.sql.gz`), da sie die Wahl noch enthalten.
- Die QR-Codes und der Server enthalten keine Namen der Wähler/innen.

## Betrieb im Alltag

- Server nicht ausschalten/aktualisieren, solange eine Wahl läuft. Zwischen den Wahlen darf er laufen oder gestoppt werden (`docker compose stop`).
- Alle paar Monate: `git pull && docker compose up -d --build` (vorher `./scripts/backup.sh`), Betriebssystem-Updates einspielen.
