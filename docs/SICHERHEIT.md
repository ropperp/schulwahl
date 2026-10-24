# Sicherheitskonzept und Prüfbericht

Stand: Oktober 2026. Geprüft wurde die gesamte Anwendung (Code, Datenbank, Docker-Betrieb, Skripte) mit **132 automatischen Tests**
(SQLite und PostgreSQL), einem Lasttest mit 1000 simulierten Wählern gegen den echten Docker-Betrieb und einer Prüfung der Abhängigkeiten (`pip-audit`: keine bekannten Schwachstellen).
Die Tests laufen bei jedem Commit automatisch (GitHub Actions, `.github/workflows/ci.yml`).

## Schutzziele

1. **Wahlgeheimnis:** Niemand kann nachträglich feststellen, wer was gewählt hat.
2. **Ein Code – eine Stimme:** Kein Code kann zweimal wirken, auch nicht bei gleichzeitigen Anfragen.
3. **Integrität:** Stimmen und Ergebnis lassen sich nicht über die Weboberfläche verändern; Auszählung ist nachprüfbar.
4. **Vertraulichkeit der Zwischenstände:** Ergebnisse gibt es erst nach Wahlende.
5. **Verfügbarkeit:** 1000 Schüler/innen können gleichzeitig wählen.
6. **Zugriffsschutz:** Das Backoffice ist nur für berechtigte Personen mit passender Rolle zugänglich.

## Wahlgeheimnis – wie es gesichert ist

| Maßnahme | Wirkung |
|---|---|
| `votes` hat **keinen Verweis auf `codes`**, keinen Zeitstempel, zufällige IDs | Strukturell keine Verbindung (Test `test_votes_are_not_linked_to_codes`) |
| **Urne:** Stimmen liegen bis Wahlende in `pending_ballots` und werden erst dann **in einer Transaktion und in zufälliger Reihenfolge** nach `votes` übernommen | PostgreSQL speichert in jeder Zeile die ID der schreibenden Transaktion (`xmin`). Ohne Urne trugen Code und Stimmen dieselbe ID – ein `JOIN` hätte jede Stimme einem Code zugeordnet (**bei der Prüfung entdeckt und behoben**, Test `test_no_transaction_id_links_votes_to_codes`) |
| Nach der Übernahme `TRUNCATE` der Urne (PostgreSQL) | Keine gelöschten Zeilen-Reste auf der Platte |
| Beide Stimmen einer Person (Schulsprecher + Abteilung) landen **ungepaart** in der Tabelle | Auch die Kombination bleibt geheim |
| Zufällige Kandidatenreihenfolge pro Stimmzettel (kryptografischer Zufall) | Kein Listenplatz-Vorteil |
| Wahlseiten setzen **keine Cookies**, **kein Access-Log** | QR-Links mit Codes landen in keiner Protokolldatei und keinem Browser-Cookie |

## Zugriffsschutz und Konten

| Maßnahme | Details |
|---|---|
| Eigene Konten mit Rollen **Administrator/in** und **Wahlleiter/in** | Rechte laut Tabelle in `BETRIEB.md`; **jede Route** ist per automatischem Test auf Login- und Rollenschutz geprüft |
| Passwörter | `scrypt` mit Salt (nie im Klartext), Mindestlänge 10, einfache/triviale Passwörter werden abgelehnt |
| Einmal-Passwörter | Vom System erzeugt, nur einmal angezeigt, **Zwang zum Wechsel** beim ersten Login; Zurücksetzen nur durch den Admin |
| Sitzungen | Signiertes Cookie, `HttpOnly`, `SameSite=Strict`, `Secure` mit HTTPS, 8 h gültig; **neue Sitzung bei Login** (keine Session-Fixation); Abmelden und Passwortänderung **entwerten alle Sitzungen** des Kontos (auch kopierte Cookies) |
| Brute-Force-Schutz | Sperre pro IP (5/5 min) **und** pro Benutzername (20/10 min); Fehlermeldung verrät nicht, ob ein Benutzer existiert (auch die Antwortzeit wird angeglichen) |
| CSRF | Token in jedem POST (Test prüft **alle** POST-Routen automatisch) |
| Protokoll | Anmeldungen, Passwortänderungen, Codes, Start/Ende, Löschungen – mit Benutzer und IP (nur Administratoren sehen es) |
| Notfallzugang | Nur über den Server: `python -m app.manage reset-password` |
| Entwickler-Notfallzugang | Siehe Abschnitt „Entwickler-Notfallzugang“ unten |
| Zweiter Administrator | Im Backoffice unter „Benutzer“ anlegbar; ein Administrator kann andere zurücksetzen/löschen, aber nie sich selbst; der letzte sichtbare Administrator bleibt erhalten |
| Ersteinrichtung | Es steht kein Passwort in einer Datei. Eine frische Installation erzeugt einen **Einrichtungscode** (8 Zeichen, nur auf dem Server sichtbar); ohne ihn kann niemand den Administrator anlegen. Falsche Codes werden gesperrt (5 Versuche/5 min pro IP) und protokolliert; nach der Einrichtung ist die Seite gesperrt (Tests: `test_setup.py`) |

## Schutz gegen typische Angriffe – was getestet wurde

| Angriff | Gegenmaßnahme | Test |
|---|---|---|
| Code erraten (Brute Force) | 32⁶ ≈ 1 Mrd. Codes bei ~1200 gültigen; Sperre pro IP (300 Fehlversuche/5 min – bewusst großzügig wegen Schul-WLAN); gefälschtes `X-Forwarded-For` wirkt nur bei vertrauenswürdigem Proxy | `test_code_guessing_is_rate_limited…`, `test_xff_is_used_only_when_proxy_is_trusted` |
| Doppelte Stimme / Wiederholung / Parallelanfragen | atomares `UPDATE … WHERE used = false` in derselben Transaktion | `test_concurrent_double_submit…`, `test_replay_of_used_code…` |
| Manipulierte Formulare (fremder Kandidat, fremder Wahlgang, Fantasiewerte) | Auswahl wird serverseitig gegen den Stimmzettel des Codes geprüft | `test_candidate_of_other_department…`, `test_extra_race_ids…` |
| Stimmabgabe vor/nach dem Zeitfenster | Serverseitige Statusprüfung | `test_voting_before_start_and_after_end_is_blocked` |
| SQL-Injection | Ausschließlich parametrisierte Abfragen (SQLAlchemy); Eingaben mit `'`, `--`, `;`, Nullbytes, Unicode-Tricks getestet | `test_sql_injection_*`, `test_nul_byte…` |
| Cross-Site-Scripting | Automatisches Escaping aller Ausgaben + strenge CSP **ohne** Inline-Skripte/-Styles; getestet mit Skripten in Namen, Info, Wahlname, Code-Feld | `test_hostile_names_are_escaped_everywhere`, `test_reflected_code…` |
| Clickjacking, MIME-Sniffing, Referrer-Leck | `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP/CORP, HSTS mit HTTPS | `test_security_headers_everywhere`, `test_hsts…` |
| Gefälschte/manipulierte Sitzungs-Cookies | Signatur (`SECRET_KEY`), Sitzungsversion in der Datenbank | `test_forged_and_tampered_session_cookies…` |
| Zugriff ohne Login / mit falscher Rolle | Dependency-Prüfung auf jeder Route; Test iteriert über **alle** Routen | `test_every_backoffice_route_requires_login`, `test_admin_only_routes_are_closed_for_wahlleiter` |
| Schädliche Uploads (SVG, PHP, HTML, getarnte Dateien) | Whitelist JPG/PNG/WebP, Datei wird dekodiert und **neu kodiert** (entfernt EXIF/GPS und eingebettete Inhalte), max. 5 MB | `test_dangerous_uploads_rejected`, `test_uploaded_image_is_reencoded…` |
| Dekompressionsbombe (winzige Datei, riesiges Bild) | Harte Pixelgrenze 25 Mio. | `test_decompression_bomb_rejected` |
| Speicher-/Größen-Flut | Anfragelimit 32 KB (Schülerseiten) bzw. 8 MB (Backoffice), auch ohne `Content-Length` | `test_oversized_*` |
| Pfad-Traversal (`/static/../…`) | Sicherer Static-Handler | `test_static_path_traversal_blocked` |
| CSV-/Formel-Injection in Excel (`=HYPERLINK(...)`) | Verdächtige Zellen werden entschärft | `test_csv_export_neutralises_formulas` |
| Riesige/negative IDs, Nullbytes → Serverfehler | Als Eingabefehler (400/404) behandelt | `test_huge_and_negative_ids…` |
| Manipulierte QR-Adresse (Phishing) | Nur Administratoren ändern sie, strenge Prüfung (nur `http(s)://host[:port]`), nicht per Parameter überschreibbar | `test_invalid_qr_urls_rejected`, `test_wahlleiter_cannot_override_qr_url…` |
| Vorzeitiges Auslesen von Ergebnissen | Ergebnisse, CSV, Protokoll erst nach Ende; öffentlich nur nach Freigabe | `test_results_hidden_until_end_and_publication` |
| API-Dokumentation/Fehlerseiten verraten Interna | `/docs`, `/openapi.json` aus; keine Stacktraces; `Server`-Header entfernt | `test_no_api_docs_exposed` |
| Verlust/Beschädigung von Sicherungen | Backup prüft Vollständigkeit; Restore prüft **vor** dem Löschen | manuell getestet (kaputte Datei, gestoppte DB) |

## Entwickler-Notfallzugang

Für den Fall, dass keine Anmeldung mehr funktioniert (alle Passwörter verloren, niemand mit Server-Zugang erreichbar), gibt es einen **fest hinterlegten Zugang mit Administrator-Rechten** für die ursprünglichen Entwickler/innen.
Transparenz ist wichtig, weil ein solcher Zugang mächtig ist – deshalb gilt:

* **Nur Hashes, kein Klartext:** Weder Benutzername noch Passwort stehen im Programm, nur zwei scrypt-Hashes in `app/developer_access.py`. Ohne beide Hashes ist der Zugang **aus**.
* **Nicht unsichtbar:** Der Zugang erscheint in keiner Benutzerliste und lässt sich nicht über das Backoffice ändern, **aber jede Anmeldung und jede Aktion steht im Protokoll als „Entwicklerzugang“** (Administratoren sehen das). Der Benutzername selbst wird nie angezeigt oder protokolliert (auch bei falschem Passwort nicht).
* **Die Schule hat die Kontrolle:** `DEVELOPER_ACCESS=off` in der `.env` und `docker compose up -d` schaltet ihn ab. Auf dem Server kann der Zugang außerdem jederzeit geprüft werden (`SELECT username FROM users` zeigt die interne Zeile `_entwickler`; erst nach einer Anmeldung vorhanden).
* **Gleiche Schutzmaßnahmen wie jede Anmeldung:** Sperre nach Fehlversuchen (pro IP und Name), gleiche Antwortzeit, keine Passwortänderung in der App.
* **Passwortstärke:** Das Passwort muss mind. 16 Zeichen haben; Hash erzeugen mit `python -m app.passwords` (Eingabe unsichtbar). Wer das Repository liest, sieht nur die Hashes – bei einem zufälligen langen Passwort sind sie praktisch nicht zu knacken.
* **Empfehlung:** Dem Informatik-Lehrer/der Schulleitung die Existenz dieses Zugangs mitteilen und ihn abschalten, sobald die Entwickler/innen nicht mehr gebraucht werden.

## Betrieb / Infrastruktur

- Container: App läuft als **Nicht-Root-Benutzer**, mit **schreibgeschütztem Dateisystem**, ohne Linux-Capabilities, `no-new-privileges`, Prozess-Limit.
- Die **Datenbank ist nicht von außen erreichbar** (kein veröffentlichter Port), Passwort wird zufällig erzeugt.
- **Ersteinrichtung zügig abschließen:** Bis zum Abschluss ist die Einrichtungsseite von außen erreichbar (geschützt durch den Code). `setup.sh` zeigt den Code am Ende an – danach gleich im Browser fertig stellen.
- Von außen sichtbar ist nur HTTPS (Caddy bzw. Schul-Proxy). Die `.env` hat Rechte `600`, ist per `.gitignore` vom Repository ausgeschlossen und nicht im Docker-Image.
- Abhängigkeiten sind auf feste Versionen gepinnt; Dependabot und `pip-audit` in der CI melden bekannte Schwachstellen.
- Mehrere Prozesse (`WEB_CONCURRENCY`) starten sicher parallel (Datenbank-Sperre bei Tabellenanlage).

## Bekannte Grenzen (Restrisiken)

Diese Punkte lassen sich mit einer Web-Anwendung nicht vollständig ausschließen; sie sind organisatorisch abzudecken:

1. **Code-Zettel sind Inhabertoken.** Wer einen Zettel besitzt, kann wählen (Weitergabe, Foto). Gegenmaßnahme: Verteilung durch Klassenvorstände mit Stückzahlkontrolle, Restzettel vernichten.
2. **Insider mit Wahlleiter-/Admin-Rechten** sehen unbenutzte Codes und könnten sie selbst einlösen. Gegenmaßnahme: Vier-Augen-Prinzip, Protokoll (zeigt Code-Erzeugung und PDF-Zugriffe), Stückzahlabgleich: *ausgegebene Codes = Schüler + Reserve*, Konsistenzprüfung im Dashboard.
3. **Insider mit Root-Zugriff auf den Server** (wer den Server verwaltet) könnte den Programmcode verändern oder den Speicher auslesen. Datenbank-Auswertungen nach der Wahl können die Stimmen **nicht** mehr Codes zuordnen. Während der laufenden Wahl lassen sich in der Urne jedoch noch die *physische Zeilenreihenfolge* und Datenbank-Interna eines Superusers auswerten – das erfordert gezielten Missbrauch von Administratorrechten, hinterlässt aber keine Spuren in der App. Der Server-Zugang gehört deshalb in wenige, vertrauenswürdige Hände.
4. **Sicherungen** enthalten die komplette Wahl. Wie Wahlunterlagen behandeln und nach der Aufbewahrungsfrist löschen.
5. **Verfügbarkeit:** Ein einzelner Server. Volumetrische Angriffe (DDoS) kann die Anwendung nicht abwehren (ggf. Schul-Firewall/CDN vorschalten). Stromausfall/Neustart: Docker startet die Dienste automatisch neu, die Daten bleiben erhalten.
6. **Endgeräte:** Ein infiziertes Handy oder jemand, der über die Schulter schaut, ist nicht abwehrbar. Die Seite speichert nichts im Browser außer dem Formular.
7. **Phishing:** Eine gefälschte Wahlseite mit ähnlicher Adresse. Gegenmaßnahme: Adresse nur über die gedruckten Zettel/QR-Codes kommunizieren, HTTPS-Schloss.

## Sicherheitslücke gefunden?

Bitte nicht öffentlich als Issue melden, sondern direkt an die Projektverantwortlichen (GitHub → *Security* → *Report a vulnerability*, falls aktiviert, sonst per E-Mail an die Schul-IT).

## Härtungs-Checkliste für den Server

- [ ] Nur Ports 22 (SSH), 80, 443 offen; SSH mit Schlüssel statt Passwort, `PermitRootLogin no`
- [ ] Automatische Sicherheitsupdates (`unattended-upgrades`) aktiv – Neustart-Zeitpunkt nicht am Wahltag
- [ ] Docker-Zugriff (Gruppe `docker` = Root-Rechte!) nur für die Server-Verantwortlichen
- [ ] Festplattenverschlüsselung bzw. physisch gesicherter Serverraum
- [ ] Backups verschlüsselt/gesichert abgelegt, Zugriff beschränkt
- [ ] Uhrzeit synchron (`timedatectl`) – Wahlstart/-ende hängen daran
