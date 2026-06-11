"""Internal market-intelligence store.

Every analysis (web searches and CLI area scans alike) is upserted into one
SQLite database keyed by OSM id, then aggregated by city / suburb / postcode
for area overviews - the sellable product for PV installers. This is operator
data: the public web UI keeps individual results private and ephemeral; only
the operator endpoints expose the database, and what you license to a buyer
should be the AGGREGATES (selling rows about identifiable private homes would
need a GDPR legal basis - business roofs are far less restricted).
"""

import csv
import io
import sqlite3
import time
from pathlib import Path
from typing import List

SCHEMA = """
CREATE TABLE IF NOT EXISTS roofs (
  osm_id       INTEGER PRIMARY KEY,
  ts           REAL,
  address      TEXT,
  postcode     TEXT,
  city         TEXT,
  suburb       TEXT,
  segment      TEXT,
  status       TEXT,
  building_type TEXT,
  footprint_m2 REAL,
  orientation  TEXT,
  specific_yield REAL,
  kwp          REAL,
  panel_area_m2 REAL,
  cost_eur     REAL,
  annual_kwh   REAL,
  annual_benefit_eur REAL,
  payback_years REAL,
  contact_email TEXT,
  contact_phone TEXT,
  business_name TEXT
)"""


LEADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  osm_id       INTEGER,
  ts           REAL,
  address      TEXT,
  postcode     TEXT,
  city         TEXT,
  suburb       TEXT,
  segment      TEXT,
  kwp          REAL,
  cost_eur     REAL,
  payback_years REAL,
  email        TEXT,
  phone        TEXT,
  consent      INTEGER DEFAULT 1,
  demo         INTEGER DEFAULT 0
)"""


def add_lead(db_path: Path, row: dict, email: str, phone: str = "",
             demo: bool = False) -> None:
    """A homeowner explicitly asked for offers. Only these rows may be shown
    to partner companies as named leads (GDPR consent); everything else stays
    anonymous area statistics."""
    with sqlite3.connect(db_path) as con:
        con.execute(LEADS_SCHEMA)
        con.execute(
            "INSERT INTO leads (osm_id, ts, address, postcode, city, suburb,"
            " segment, kwp, cost_eur, payback_years, email, phone, consent, demo)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
            (row.get("osm_id"), time.time(), row.get("address", ""),
             row.get("postcode", ""), row.get("city", ""), row.get("suburb", ""),
             row.get("segment", ""), row.get("proposed_kwp", 0),
             row.get("estimated_cost_eur", 0), row.get("payback_years", 0),
             email, phone, 1 if demo else 0))


def partner_summary(db_path: Path, city: str = "", suburb: str = "",
                    weeks: int = 8) -> dict:
    """Region dashboard for a partner company: KPIs, weekly activity and the
    consented leads of their region, plus a teaser of what lies outside it."""
    if not Path(db_path).is_file():
        return {"kpi": {}, "weekly": [], "leads": [], "outside": {}}
    cut = time.time() - weeks * 7 * 86400
    region = "AND city = ?" + (" AND suburb = ?" if suburb else "")
    args = [cut, city] + ([suburb] if suburb else [])
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        con.execute(SCHEMA)
        con.execute(LEADS_SCHEMA)
        kpi = dict(con.execute(f"""
            SELECT COUNT(*) AS searches,
                   SUM(status='eligible') AS eligible,
                   SUM(segment='b2b') AS b2b,
                   ROUND(SUM(CASE WHEN status='eligible' THEN kwp END),1) AS kwp,
                   ROUND(SUM(CASE WHEN status='eligible' THEN cost_eur END)) AS invest_eur,
                   ROUND(AVG(CASE WHEN status='eligible' THEN payback_years END),1) AS avg_payback
            FROM roofs WHERE ts >= ? {region}""", args).fetchone())
        weekly = [dict(r) for r in con.execute(f"""
            SELECT strftime('%Y-%W', ts, 'unixepoch') AS week,
                   COUNT(*) AS searches, SUM(status='eligible') AS eligible
            FROM roofs WHERE ts >= ? {region}
            GROUP BY week ORDER BY week""", args)]
        leads = [dict(r) for r in con.execute(f"""
            SELECT ts, address, postcode, suburb, segment, kwp, cost_eur,
                   payback_years, email, phone
            FROM leads WHERE consent = 1 AND ts >= ? {region}
            ORDER BY ts DESC LIMIT 50""", args)]
        outside = dict(con.execute(
            """SELECT COUNT(*) AS searches,
                      (SELECT COUNT(*) FROM leads WHERE consent=1 AND ts >= ?1
                       AND NOT (city = ?2)) AS leads
               FROM roofs WHERE ts >= ?1 AND NOT (city = ?2)""",
            [cut, city]).fetchone()) if not suburb else dict(con.execute(
            """SELECT COUNT(*) AS searches,
                      (SELECT COUNT(*) FROM leads WHERE consent=1 AND ts >= ?1
                       AND city = ?2 AND suburb != ?3) AS leads
               FROM roofs WHERE ts >= ?1 AND city = ?2 AND suburb != ?3""",
            [cut, city, suburb]).fetchone())
    return {"kpi": kpi, "weekly": weekly, "leads": leads, "outside": outside}


def upsert(db_path: Path, rows: List[dict]) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute(SCHEMA)
        now = time.time()
        con.executemany(
            "INSERT OR REPLACE INTO roofs VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["osm_id"], now, r["address"], r.get("postcode", ""),
              r.get("city", ""), r.get("suburb", ""), r["segment"], r["status"],
              r["building_type"], r["footprint_m2"], r.get("orientation", ""),
              r.get("specific_yield", 0), r.get("proposed_kwp", 0),
              r.get("proposed_panel_area_m2", 0), r.get("estimated_cost_eur", 0),
              r.get("estimated_annual_kwh", 0), r.get("annual_benefit_eur", 0),
              r.get("payback_years", 0), r.get("contact_email", ""),
              r.get("contact_phone", ""), r.get("business_name", ""))
             for r in rows])


OVERVIEW_SQL = """
SELECT r.city, r.suburb, r.postcode,
       COUNT(*)                                              AS roofs,
       SUM(r.status = 'eligible')                            AS eligible,
       SUM(r.segment = 'b2b')                                AS b2b,
       ROUND(SUM(CASE WHEN r.status='eligible' THEN r.kwp END), 1)      AS kwp,
       ROUND(SUM(CASE WHEN r.status='eligible' THEN r.cost_eur END), 0) AS invest_eur,
       ROUND(SUM(CASE WHEN r.status='eligible' THEN r.annual_kwh END), 0) AS kwh,
       ROUND(AVG(CASE WHEN r.status='eligible' THEN r.payback_years END), 1) AS avg_payback,
       (SELECT COUNT(*) FROM leads l WHERE l.consent = 1
          AND l.city = r.city AND IFNULL(l.suburb,'') = IFNULL(r.suburb,'')) AS leads
FROM roofs r
GROUP BY r.city, r.suburb, r.postcode
ORDER BY kwp DESC NULLS LAST
"""


def overview(db_path: Path) -> List[dict]:
    if not Path(db_path).is_file():
        return []
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        con.execute(LEADS_SCHEMA)
        try:
            return [dict(r) for r in con.execute(OVERVIEW_SQL)]
        except sqlite3.OperationalError:        # older sqlite without NULLS LAST
            return [dict(r) for r in con.execute(
                OVERVIEW_SQL.replace(" NULLS LAST", ""))]


DISCLAIMER_ROW = ("# All values are non-binding automated estimates (aerial "
                  "imagery, OSM, LoD2, PVGIS long-term averages). Detection and "
                  "yield errors of +/-20% and more are possible; not warranted "
                  "characteristics.")


def export_csv(db_path: Path, aggregated: bool = True) -> str:
    """CSV for licensing to PV businesses. Default: aggregates only."""
    buf = io.StringIO()
    buf.write(DISCLAIMER_ROW + "\n")
    if aggregated:
        rows = overview(db_path)
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    else:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute("SELECT * FROM roofs")]
            if rows:
                w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
    return buf.getvalue()
