"""Seed market.db with plausible demo activity for the partner portal.

    python -m solar_scout.demo_seed [--db market.db] [--wipe]

Generates a few months of homeowner searches across Berlin districts (Pankow
weighted, since the demo partner sits there) plus a realistic share of
consented leads. Synthetic rows use OSM ids >= 900000000 so they never collide
with real analyses and can be wiped selectively.
"""

import argparse
import random
import sqlite3
import time
from pathlib import Path

from . import market

DISTRICTS = {
    "Pankow": (38, ["Berliner Straße", "Breite Straße", "Florastraße",
                    "Mühlenstraße", "Granitzstraße", "Straße vor Schönholz",
                    "Wolfshagener Straße", "Kavalierstraße"]),
    "Niederschönhausen": (16, ["Dietzgenstraße", "Platanenstraße",
                               "Friedrich-Engels-Straße", "Grabbeallee"]),
    "Weißensee": (12, ["Berliner Allee", "Pistoriusstraße", "Gounodstraße"]),
    "Prenzlauer Berg": (18, ["Stargarder Straße", "Greifenhagener Straße",
                             "Dunckerstraße", "Kuglerstraße"]),
    "Mitte": (14, ["Ackerstraße", "Invalidenstraße", "Chausseestraße"]),
    "Reinickendorf": (10, ["Residenzstraße", "Scharnweberstraße"]),
    "Spandau": (8, ["Pichelsdorfer Straße", "Heerstraße"]),
}
STATUSES = [("eligible", 0.62), ("has_panels", 0.20), ("obstructed", 0.10),
            ("too_small", 0.08)]
FIRST = ["mueller", "schmidt", "weber", "fischer", "becker", "wagner", "koch",
         "richter", "wolf", "neumann", "krause", "lehmann"]
MAIL = ["web.de", "gmx.de", "t-online.de", "gmail.com", "posteo.de"]


def pick_status(rng):
    r, acc = rng.random(), 0.0
    for s, p in STATUSES:
        acc += p
        if r <= acc:
            return s
    return "eligible"


def seed(db: Path, weeks: int = 9, seed_val: int = 42) -> None:
    rng = random.Random(seed_val)
    now = time.time()
    rows, leads = [], []
    osm = 900000000
    for suburb, (count, streets) in DISTRICTS.items():
        for _ in range(count):
            osm += 1
            ts = now - rng.uniform(0, weeks * 7 * 86400)
            status = pick_status(rng)
            b2b = rng.random() < 0.12
            kwp = round(rng.uniform(30, 220), 1) if b2b else round(rng.uniform(4, 16), 1)
            cost = round(kwp * rng.uniform(950, 1400), -2)
            yield_ = rng.uniform(820, 1010)
            kwh = round(kwp * yield_, -1)
            benefit = round(kwh * rng.uniform(0.12, 0.2), -1)
            addr = f"{rng.choice(streets)} {rng.randint(1, 89)}, Berlin"
            row = {
                "osm_id": osm, "ts": ts, "address": addr,
                "postcode": f"13{rng.randint(100, 599)}", "city": "Berlin",
                "suburb": suburb, "segment": "b2b" if b2b else "residential",
                "status": status, "building_type": "yes",
                "footprint_m2": round(kwp * rng.uniform(8, 14), 1),
                "orientation": rng.choice(["Satteldach Süd 38°", "Satteldach Ost 45°",
                                           "Flachdach Süd 15°", "Walmdach Südwest 35°"]),
                "specific_yield": round(yield_), "kwp": kwp if status == "eligible" else 0,
                "panel_area_m2": round(kwp * 4.4, 1) if status == "eligible" else 0,
                "cost_eur": cost if status == "eligible" else 0,
                "annual_kwh": kwh if status == "eligible" else 0,
                "annual_benefit_eur": benefit if status == "eligible" else 0,
                "payback_years": round(cost / benefit, 1) if status == "eligible" else 0,
            }
            rows.append(row)
            # roughly a fifth of eligible homeowners ask for offers
            if status == "eligible" and rng.random() < 0.22:
                leads.append((osm, ts + rng.uniform(60, 1800), addr, row["postcode"],
                              "Berlin", suburb, row["segment"], kwp, cost,
                              row["payback_years"],
                              f"{rng.choice(FIRST)}{rng.randint(2, 99)}@{rng.choice(MAIL)}"))

    with sqlite3.connect(db) as con:
        con.execute(market.SCHEMA)
        con.execute(market.LEADS_SCHEMA)
        con.executemany(
            "INSERT OR REPLACE INTO roofs VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["osm_id"], r["ts"], r["address"], r["postcode"], r["city"],
              r["suburb"], r["segment"], r["status"], r["building_type"],
              r["footprint_m2"], r["orientation"], r["specific_yield"], r["kwp"],
              r["panel_area_m2"], r["cost_eur"], r["annual_kwh"],
              r["annual_benefit_eur"], r["payback_years"], "", "", "")
             for r in rows])
        con.executemany(
            "INSERT INTO leads (osm_id, ts, address, postcode, city, suburb,"
            " segment, kwp, cost_eur, payback_years, email, phone, consent, demo)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,'',1,1)", leads)
    print(f"Seeded {len(rows)} demo analyses and {len(leads)} consented demo leads.")


def wipe(db: Path) -> None:
    with sqlite3.connect(db) as con:
        con.execute(market.SCHEMA)
        con.execute(market.LEADS_SCHEMA)
        con.execute("DELETE FROM roofs WHERE osm_id >= 900000000")
        con.execute("DELETE FROM leads WHERE demo = 1")
    print("Demo rows removed.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="market.db")
    p.add_argument("--wipe", action="store_true", help="remove demo rows instead")
    a = p.parse_args()
    (wipe if a.wipe else seed)(Path(a.db))
