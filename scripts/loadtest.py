#!/usr/bin/env python3
"""Lasttest: simuliert viele Schüler/innen, die gleichzeitig wählen - gegen den ECHTEN Server (Domain inklusive HTTPS).

Der Test legt selbst eine Wahl "Lasttest" an, erzeugt Codes, startet die Wahl, lässt die Schüler wählen, beendet die Wahl,
prüft Zählung und Konsistenz und LÖSCHT die Test-Wahl am Ende wieder. Dafür ist ein Administrator-Login nötig.
Nie während einer echten Wahl ausführen (es belastet den Server absichtlich)!

Beispiel (1000 Schüler, die innerhalb von 30 Sekunden alle wählen):
    pip install httpx
    python scripts/loadtest.py --url https://wahl.htl1-klu.at --user admin --voters 1000 --spread 30
Das Passwort wird abgefragt (oder Umgebungsvariable LOADTEST_PASSWORD).

--spread 0 = alle in derselben Sekunde (härtester Fall). Realistisch: Klassen wählen nacheinander, also 30-300 Sekunden.
"""
import argparse
import asyncio
import getpass
import io
import multiprocessing as mp
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta

import httpx

TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else 0.0


def jpeg() -> bytes:
    try:
        from PIL import Image
    except ImportError:  # ohne Pillow: kleines festes JPEG
        return bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001400010000000000000000000000000000000affc40014100100000000000000000000000000000000ffda0008010100003f00d2cf20ffd9")
    buf = io.BytesIO()
    Image.new("RGB", (800, 800), (40, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


def admin_client(url, user, password):
    c = httpx.Client(base_url=url, follow_redirects=False, timeout=60)
    r = c.post("/admin/login", data={"username": user, "password": password,
                                     "csrf_token": TOKEN.search(c.get("/admin/login").text).group(1)})
    if r.status_code != 303 or c.get("/admin").status_code != 200:
        sys.exit("Login fehlgeschlagen (Benutzername/Passwort prüfen; mit Rolle admin anmelden, "
                 "und das Passwort muss schon geändert sein).")
    return c, TOKEN.search(c.get("/admin/account").text).group(1)


def prepare(url, user, password, voters):
    c, t = admin_client(url, user, password)
    now = datetime.now()
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M")  # noqa: E731
    r = c.post("/admin/elections", data={"csrf_token": t, "name": "Lasttest (wird gelöscht)",
                                         "start_at": fmt(now + timedelta(days=1)), "end_at": fmt(now + timedelta(days=1, hours=2)),
                                         "standard_races": "1", "departments": "Lasttest A\nLasttest B"})
    eid = int(r.headers["location"].rstrip("/").split("/")[-1])
    photo = jpeg()
    for rid in re.findall(r"/admin/races/(\d+)/candidates", c.get(f"/admin/elections/{eid}/candidates").text):
        for i in range(5):
            c.post(f"/admin/races/{rid}/candidates", data={"csrf_token": t, "name": f"Kandidat {rid}-{i}"},
                   files={"photo": ("k.jpg", photo, "image/jpeg")})
    depts = re.findall(r'<option value="(\d+)">', c.get(f"/admin/elections/{eid}/codes").text)
    for d in depts:
        c.post(f"/admin/elections/{eid}/codes", data={"csrf_token": t, "department_id": d, "count": voters // len(depts) + 1})
    c.post(f"/admin/elections/{eid}/start-now", data={"csrf_token": t})
    page = c.get(f"/admin/elections/{eid}/codes").text
    codes = [a + b for a, b in re.findall(r"<td><code>([A-Z2-9]{3}) ([A-Z2-9]{3})</code>", page)][:voters]
    return c, t, eid, codes


async def voter(client, code, delay, stats):
    await asyncio.sleep(delay)
    try:
        async def req(kind, method, path, **kw):
            t0 = time.perf_counter()
            r = await client.request(method, path, **kw)
            stats[kind].append(time.perf_counter() - t0)
            return r

        await req("seite", "GET", f"/c/{code}")
        await req("seite", "GET", "/static/style.css")
        r = await req("seite", "POST", "/vote/start", data={"code": code})
        if r.status_code != 200:
            raise RuntimeError(f"Stimmzettel {r.status_code}")
        await asyncio.gather(*(req("foto", "GET", u) for u in set(re.findall(r'src="(/media/candidate/\d+)"', r.text))))
        form = {"code": code}
        for rid in set(re.findall(r'name="race_(\d+)"', r.text)):
            form[f"race_{rid}"] = random.choice(re.findall(rf'name="race_{rid}" value="(\d+)"', r.text))
        await asyncio.sleep(random.uniform(2, 8))  # die Schüler/innen überlegen kurz
        if (await req("seite", "POST", "/vote/review", data=form)).status_code != 200:
            raise RuntimeError("Zusammenfassung")
        r = await req("abgabe", "POST", "/vote/submit", data=form)
        if r.status_code != 200:
            raise RuntimeError(f"Abgabe {r.status_code}")
        stats["ok"].append(1)
    except Exception as exc:  # noqa: BLE001
        stats["fehler"].append(f"{type(exc).__name__}: {str(exc)[:60]}")


async def run_shard(url, codes, spread):
    stats = {"seite": [], "foto": [], "abgabe": [], "ok": [], "fehler": []}
    limits = httpx.Limits(max_connections=1500, max_keepalive_connections=300)
    async with httpx.AsyncClient(base_url=url, timeout=90, limits=limits) as client:
        await asyncio.gather(*(voter(client, c, random.uniform(0, spread), stats) for c in codes))
    return stats


def shard_main(args):
    url, codes, spread = args
    return asyncio.run(run_shard(url, codes, spread))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="z. B. https://wahl.htl1-klu.at")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--voters", type=int, default=1000)
    ap.add_argument("--spread", type=float, default=30, help="Sekunden, über die die Schüler verteilt starten (0 = alle gleichzeitig)")
    ap.add_argument("--procs", type=int, default=max(2, (os.cpu_count() or 2)), help="Client-Prozesse (der Test-PC darf nicht der Engpass sein)")
    ap.add_argument("--keep", action="store_true", help="Test-Wahl am Ende NICHT löschen")
    a = ap.parse_args()
    password = os.getenv("LOADTEST_PASSWORD") or getpass.getpass(f"Passwort für {a.user}: ")
    url = a.url.rstrip("/")

    print("Bereite Test-Wahl vor ...")
    c, t, eid, codes = prepare(url, a.user, password, a.voters)
    print(f"{len(codes)} Codes erzeugt. Starte {len(codes)} Wähler (Verteilung über {a.spread:.0f} s, {a.procs} Prozesse) ...")
    shards = [(url, codes[i::a.procs], a.spread) for i in range(a.procs)]
    t0 = time.time()
    with mp.Pool(a.procs) as pool:
        results = pool.map(shard_main, shards)
    took = time.time() - t0
    total = {k: sum((r[k] for r in results), []) for k in results[0]}

    print(f"\nDauer insgesamt: {took:.1f} s")
    print(f"Erfolgreich gewählt: {len(total['ok'])} von {len(codes)}")
    for kind in ("seite", "foto", "abgabe"):
        v = total[kind]
        print(f"  {kind:7s} n={len(v):5d}  Median {pct(v, .5) * 1000:6.0f} ms   95% {pct(v, .95) * 1000:6.0f} ms   max {max(v, default=0) * 1000:6.0f} ms")
    if total["fehler"]:
        from collections import Counter
        print("Fehler:")
        for msg, n in Counter(total["fehler"]).most_common(5):
            print(f"  {n}x {msg}")

    c.post(f"/admin/elections/{eid}/close", data={"csrf_token": t})
    dash = c.get(f"/admin/elections/{eid}/dashboard").text
    consistent = "verbrauchte Codes" not in dash  # Warnung "x Stimmen, aber y verbrauchte Codes"
    ok = len(total["ok"]) == len(codes) and not total["fehler"]
    print("\nZählung/Konsistenz im Dashboard:", "in Ordnung" if consistent else "AUFFÄLLIG - bitte Dashboard prüfen")
    if a.keep:
        print(f"Test-Wahl bleibt bestehen: {url}/admin/elections/{eid}")
    else:
        c.post(f"/admin/elections/{eid}/delete", data={"csrf_token": t})
        print("Test-Wahl wurde gelöscht.")
    p95 = pct(total["seite"], .95)
    verdict = "BESTANDEN" if ok and consistent and p95 < 5 else "NICHT BESTANDEN (Fehler oder Antworten > 5 s)"
    print(f"\nErgebnis: {verdict}")
    sys.exit(0 if verdict == "BESTANDEN" else 1)


if __name__ == "__main__":
    main()
