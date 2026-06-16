"""Web UI to browse scan results.

    python -m solar_scout.webui [--base .] [--port 8765]

Discovers every run directory under --base that contains a report.csv and
serves a single-page app: filterable card grid, per-roof detail with the
interactive three.js 3D view, the letter and the raw figures.
"""

import argparse
import csv
import os
import re
import secrets
import shutil
import time
from pathlib import Path

import threading

import requests
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pyproj import Transformer
from shapely.ops import transform as shp_transform

from . import buildings, geo, imagery, market, pipeline, visitors

STATIC_DIR = Path(__file__).parent / "webui_static"
# On-demand address analyses are PRIVATE and EPHEMERAL: each lives in an
# unguessable token directory, is never listed in the archive, and is deleted
# after PRIVATE_TTL. Visitors must not be able to browse other people's homes.
PRIVATE_DIRNAME = "private"
PRIVATE_TTL = 6 * 3600
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,}$")

# public-deployment guards: analyses are CPU-heavy (YOLO + geometry), so cap
# concurrency hard and rate-limit per client on top of Cloudflare's WAF
ANALYZE_SLOTS = 2
RATE_LIMIT = 8            # analyses per window per client IP
RATE_WINDOW = 600         # seconds

NUMERIC = {
    "footprint_m2", "existing_panel_coverage", "proposed_panels", "proposed_kwp",
    "proposed_panel_area_m2", "specific_yield", "estimated_cost_eur",
    "estimated_annual_kwh", "annual_benefit_eur", "payback_years", "co2_t_per_year",
}


def create_app(base: Path) -> FastAPI:
    app = FastAPI(title="solar-scout results")

    # ---- visitor tracking (catch real humans / HR vs the bot noise) ----
    ADMIN_KEY = os.environ.get("ADMIN_KEY") or "stats"
    tracker = visitors.Tracker(base, ntfy_topic=os.environ.get("NTFY_TOPIC", ""),
                               ntfy_server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"))

    block_bots = os.environ.get("BLOCK_BOTS", "1") != "0"

    @app.middleware("http")
    async def _track(request: Request, call_next):
        try:
            ip = request.headers.get("cf-connecting-ip") or (
                request.client.host if request.client else "?")
            should_block = tracker.handle(
                ip, request.headers.get("cf-ipcountry", ""),
                request.headers.get("user-agent", ""),
                request.url.path, request.headers.get("referer", ""))
            if block_bots and should_block:
                from fastapi.responses import PlainTextResponse
                return PlainTextResponse("Not available.", status_code=403)
        except Exception:
            pass
        return await call_next(request)

    @app.get("/admin/visits")
    def admin_visits(key: str = "", hours: int = 48):
        if key != ADMIN_KEY:
            raise HTTPException(403, "forbidden")
        return tracker.stats(hours)

    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC_DIR / "admin.html")

    def run_dir(run: str) -> Path:
        d = (base / run).resolve()
        if base.resolve() not in d.parents or not (d / "report.csv").is_file():
            raise HTTPException(404, f"unknown run {run!r}")
        return d

    @app.get("/api/runs")
    def runs():
        out = []
        for f in sorted(base.glob("*/report.csv")):
            rows = list(csv.DictReader(f.open(encoding="utf-8")))
            out.append({"name": f.parent.name, "roofs": len(rows),
                        "mtime": f.stat().st_mtime})
        return sorted(out, key=lambda r: -r["mtime"])

    @app.get("/api/runs/{run}/results")
    def results(run: str):
        rows = []
        with (run_dir(run) / "report.csv").open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for k in NUMERIC & row.keys():
                    try:
                        row[k] = float(row[k] or 0)
                    except ValueError:
                        row[k] = 0.0
                rows.append(row)
        return rows

    private_base = base / PRIVATE_DIRNAME

    def _cleanup_private():
        if not private_base.is_dir():
            return
        cutoff = time.time() - PRIVATE_TTL
        for d in private_base.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)

    @app.get("/api/branding")
    def branding():
        """Personalization for the company the demo is prepared for - all
        env-driven so the same build can be re-branded per recipient."""
        return {
            "name": os.environ.get("PARTNER_NAME", ""),
            "city": os.environ.get("PARTNER_CITY", "Berlin"),
            "suburb": os.environ.get("PARTNER_SUBURB", "Pankow"),
            "chip_addr": os.environ.get("PARTNER_CHIP_ADDR", ""),
            "chip_de": os.environ.get("PARTNER_CHIP_DE", ""),
            "chip_en": os.environ.get("PARTNER_CHIP_EN", ""),
        }

    @app.get("/api/suggest")
    def suggest(q: str):
        """Address autocomplete via Photon (komoot) - built for type-ahead,
        unlike Nominatim whose usage policy forbids autocomplete traffic."""
        if len(q.strip()) < 3:
            return []
        try:
            r = requests.get("https://photon.komoot.io/api",
                             params={"q": q, "lang": "de", "limit": 10,
                                     "bbox": "5.5,47.2,15.1,55.1"},
                             headers={"User-Agent": geo.USER_AGENT}, timeout=8)
            r.raise_for_status()
            feats = r.json().get("features", [])
        except requests.RequestException:
            return []
        out, seen = [], set()
        for f in feats:
            p = f.get("properties", {})
            if p.get("countrycode") != "DE":
                continue
            street = p.get("street") or p.get("name") or ""
            hn = p.get("housenumber", "")
            label = f"{street} {hn}".strip()
            detail = " ".join(x for x in (p.get("postcode", ""),
                                          p.get("city") or p.get("state", "")) if x)
            value = f"{label}, {detail}" if detail else label
            if not label or value in seen:
                continue
            seen.add(value)
            out.append({"label": label, "detail": detail, "value": value,
                        "address": bool(hn)})
            if len(out) >= 6:
                break
        return out

    analyze_sem = threading.Semaphore(ANALYZE_SLOTS)
    rate: dict = {}

    def _client_ip(request: Request) -> str:
        # Cloudflare forwards the real client address in this header
        return (request.headers.get("cf-connecting-ip")
                or (request.client.host if request.client else "?"))

    @app.get("/api/analyze")
    def analyze(address: str, request: Request, label: str = ""):
        ip = _client_ip(request)
        now = time.time()
        hits = [t for t in rate.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_LIMIT:
            raise HTTPException(429, "Zu viele Anfragen. Bitte versuchen Sie es "
                                     "in ein paar Minuten erneut.")
        rate[ip] = hits + [now]
        if not analyze_sem.acquire(blocking=False):
            raise HTTPException(503, "Gerade analysieren wir andere Dächer. "
                                     "Bitte in einer Minute noch einmal versuchen.")
        try:
            return _analyze(address, label)
        finally:
            analyze_sem.release()

    def _analyze(address: str, label: str = ""):
        """Real-time analysis of one typed address: geocode -> live WMS imagery
        -> YOLO panel detection -> LoD2 roof shape -> PVGIS -> economics.
        The result is private: token-scoped files, expired after a few hours,
        never written to the archive."""
        _cleanup_private()
        try:
            place, note = geo.geocode_address(address)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        provider = imagery.provider_for_place(place)
        if provider is None:
            raise HTTPException(422,
                f"No open aerial imagery wired up yet for {place.state or place.iso}. ")
        try:
            b = buildings.fetch_building_at(place)
        except ValueError as exc:
            raise HTTPException(404, str(exc))

        to_proj = Transformer.from_crs(4326, provider.epsg, always_xy=True).transform
        poly = shp_transform(to_proj, b.polygon)
        token = secrets.token_urlsafe(24)
        out_dir = private_base / token
        opts = pipeline.Options(include_existing=True)
        display = label or None       # client sends the localized example label
        try:
            row = pipeline.analyze_one(b, poly, provider, opts, out_dir,
                                       iso=place.iso, locality=place.locality,
                                       display_address=display)
        except RuntimeError as exc:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise HTTPException(502, f"imagery/analysis failed: {exc}")
        if not label:
            row["address"] = place.display_name    # the address the user typed
        else:                                      # example property: no links or
            row["osm_url"] = ""                    # names that identify the owner
            row["business_name"] = ""
            row["contact_email"] = row["contact_phone"] = row["website"] = ""
        # files stay private+ephemeral; the FIGURES feed the internal market DB
        market.upsert(base / "market.db", [row])
        return {"token": token, "row": row, "note": note,
                "example": bool(label)}

    @app.post("/api/lead")
    def lead(payload: dict = Body(...)):
        """Disabled in the public demo: the UI shows the opt-in flow visually
        only, and the server must not collect e-mail addresses either (the
        demo runs without access control, so storing visitor PII would be a
        GDPR liability). Re-enable via market.add_lead for production."""
        raise HTTPException(403, "In der Demo deaktiviert / disabled in the demo.")

    @app.get("/api/partner/summary")
    def partner_summary(city: str = "Berlin", suburb: str = "", weeks: int = 8):
        return market.partner_summary(base / "market.db", city, suburb, weeks)

    @app.get("/partner")
    def partner_page():
        return FileResponse(STATIC_DIR / "partner.html")

    @app.get("/api/market/overview")
    def market_overview():
        """Aggregates by city/suburb/postcode - the sellable area overview."""
        return market.overview(base / "market.db")

    @app.get("/api/market/export.csv")
    def market_export(full: bool = False):
        from fastapi.responses import Response
        csv_text = market.export_csv(base / "market.db", aggregated=not full)
        return Response(csv_text, media_type="text/csv")

    @app.get("/private/{token}/{path:path}")
    def private_files(token: str, path: str):
        if not _TOKEN_RE.match(token):
            raise HTTPException(404)
        d = (private_base / token).resolve()
        f = (d / path).resolve()
        if private_base.resolve() not in d.parents or d not in f.parents or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f)

    @app.get("/files/{run}/{path:path}")
    def files(run: str, path: str):
        d = run_dir(run)
        f = (d / path).resolve()
        if d not in f.parents or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f)

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="solar-scout-web", description=__doc__)
    p.add_argument("--base", default=".", help="directory containing run output dirs")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args(argv)
    base = Path(args.base).resolve()
    print(f"Browsing runs under {base} — http://{args.host}:{args.port}")
    uvicorn.run(create_app(base), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
