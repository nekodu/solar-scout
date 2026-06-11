# solar-scout CLI

## Overview

The `solar-scout` command-line tool screens German roofs for solar potential using open geodata. It geocodes a target area, fetches OSM building footprints and corresponding open aerial imagery, detects existing PV panels, plans a new panel layout on unexploited roofs, and estimates system size, yield, cost, payback, and CO₂ impact. Results are written as CSV, HTML, per-roof PNGs, 3-D viewers, and ready-to-print letters to an output directory of your choice.

## Quick start

Install the dependencies (`pip install -r requirements.txt`), then run from the repo root:

```bash
# 1. Screen a neighbourhood
python -m solar_scout --city Köln --district Ehrenfeld --limit 20

# 2. Screen by postcode
python -m solar_scout --postcode 50823 --limit 40

# 3. Screen a specific address with a custom search radius
python -m solar_scout --query "Hauptstraße 12, Gangelt" --radius 120
```

## Command reference

### General options

| Flag | Default | Description |
|------|---------|-------------|
| `-h`, `--help` | — | show this help message and exit |
| `--out` | `out` | output directory |

### Location filters

| Flag | Default | Description |
|------|---------|-------------|
| `--address` | — | one exact address → analyse just that building (falls back from e.g. `15A` to `15` with a note when the number is not in OSM) |
| `--city` | — | — |
| `--district` | — | neighbourhood / Stadtteil, e.g. Ehrenfeld |
| `--postcode` | — | Postleitzahl, e.g. 50823 |
| `--query` | — | free-form, e.g. 'Hauptstraße 12, Gangelt' |
| `--radius` | `250` | search radius in m when the filter resolves to a point/address |

### Selection

| Flag | Default | Description |
|------|---------|-------------|
| `--limit` | `25` | max roofs to analyse |
| `--min-roof-area` | `40.0` | m² footprint |
| `--max-roof-area` | `3000.0` | m² footprint |
| `--segment` | `all` | `all`, `b2b` or `residential`. b2b = only commercial/industrial roofs (public contact data, bigger systems, faster payback) |

### Imagery (auto-picked from the state; override for other states)

| Flag | Default | Description |
|------|---------|-------------|
| `--provider` | — | `bavaria` or `nrw` |
| `--wms-url` | — | any other open WMS endpoint |
| `--wms-layers` | — | — |
| `--wms-epsg` | `25832` | — |
| `--wms-resolution` | `0.2` | m/pixel |
| `--wms-attribution` | `custom WMS` | — |

### Assumptions

| Flag | Default | Description |
|------|---------|-------------|
| `--usable-fraction` | `0.35` | — |
| `--panel-wp` | `440.0` | — |
| `--eur-per-kwp` | `0.0` | 0 = size-tiered market prices (1350 -> 900 €/kWp) |
| `--electricity-price` | `0.35` | €/kWh |
| `--self-consumption` | `0.30` | share of production used on site (B2B segment uses 0.60) |
| `--tilt` | `0.0` | module tilt in deg; 0 = auto (35 pitched / 15 flat-racked) |

### Features

| Flag | Default | Description |
|------|---------|-------------|
| `--no-ml` | `false` | skip YOLO, use CV heuristic |
| `--no-pvgis` | `false` | skip per-address PVGIS irradiance (use flat 950 kWh/kWp) |
| `--no-letters` | `false` | — |
| `--no-3d` | `false` | — |
| `--include-existing` | `false` | also save images of roofs where panels were detected |

## Execution flow

A numbered walkthrough of `main()` showing the actual module functions invoked:

1. **Prepare workspace** — parses arguments, creates the output directory (`--out`) and sub-directories `images/`, `letters/`, and `viewers/`.
2. **Geocode** — `geo.geocode(args.query, args.city, args.district, args.postcode)` resolves the location filters to a `place` object (including state).
3. **Select imagery provider** — `pick_provider()` returns a provider; it checks `--provider`, `--wms-url`/`--wms-layers`, or falls back to `imagery.provider_for_state(place.state)`. If no bundled provider matches and no override is given, the process aborts.
4. **Fetch buildings** — `buildings.fetch_buildings(place, radius_m=args.radius)` queries OSM and returns building footprints.
5. **Filter candidates** — footprints are re-projected from WGS-84 to the provider’s EPSG via `pyproj.Transformer` and `shapely.ops.transform`, then filtered by `--segment`, `--min-roof-area`, `--max-roof-area`, sorted by descending area, and truncated to `--limit`.
6. **Analyse each roof** — for every remaining candidate:
   1. `imagery.fetch_geoimage(provider, poly.bounds)` downloads the orthophoto.
   2. The roof polygon exterior is mapped from world to pixel coordinates using `geoimg.to_px`.
   3. **Detection** — if ML is enabled, `ml_detect.detect_panels_ml(geoimg.img, roof_px, geoimg.resolution)` runs YOLOv8-seg; otherwise, or on failure, it falls back to `detect.detect_panels(geoimg.img, roof_px, geoimg.resolution)`.
   4. **Existing panels** — if `det.has_panels` is true, the roof is marked *has_panels*. With `--include-existing`, `layout.render()` draws the existing boxes and writes `images/{osm_id}_existing.png`.
   5. **Plan layout** — for unexploited roofs, `layout.plan_panels(poly, args.usable_fraction)` generates panel positions.
   6. **Tilt & aspect** — tilt defaults to 15° for flat roofs (>600 m² or kinds in `FLATISH_KINDS`) and 35° for pitched roofs when `--tilt` is 0. Aspect is computed via `sun.roof_aspect_deg(poly, layout.dominant_angle(poly))` (forced to 0° for flat roofs).
   7. **Yield** — unless `--no-pvgis`, `sun.pv_yield(lat, lon, kwp, tilt, aspect)` queries PVGIS SARAH-3; otherwise a flat 950 kWh/kWp is assumed.
   8. **Economics** — `economics.estimate(len(panels), assum, annual_kwh=..., specific_yield=...)` calculates cost, benefit, payback, and CO₂. Business buildings use `base_assum.for_business()`.
   9. **Render** — `layout.render(geoimg, roof_px, panels, geoimg.to_px)` draws the new layout and writes `images/{osm_id}_proposal.png`.
   10. **3-D viewer** — unless `--no-3d`, `viewer3d.write_viewer(poly, panels, geoimg, row, height, tilt, ...)` emits `viewers/{osm_id}_3d.html`.
   11. **Letter** — unless `--no-letters`, `letter.write_letter(...)` emits `letters/{osm_id}_brief.html`.
7. **Write reports** — after the loop, `report.write_csv(rows, out_dir / "report.csv")`, `report.write_mailmerge(rows, out_dir / "mailmerge.csv")`, and `report.write_html(rows, out_dir / "report.html", ...)` generate the summary files.
8. **Finish** — prints summary paths and returns `0`.

## Output files

Everything is written under the directory specified by `--out` (default: `out/`).

| Path | Condition | Content |
|------|-----------|---------|
| `images/{osm_id}_existing.png` | `--include-existing` + panels detected | Annotated aerial image of roofs that already have PV |
| `images/{osm_id}_proposal.png` | roof is eligible | Rendered panel layout on the aerial image |
| `letters/{osm_id}_brief.html` | default (omit with `--no-letters`) | Ready-to-print offer letter in German |
| `viewers/{osm_id}_3d.html` | default (omit with `--no-3d`) | Interactive 3-D roof viewer |
| `report.csv` | always (if any rows) | One-row-per-building CSV with all numeric fields |
| `mailmerge.csv` | always (if any rows) | CSV formatted for mail-merge workflows (eligible roofs only) |
| `report.html` | always (if any rows) | Styled HTML summary table and per-roof images |

## Exit codes and error behaviour

| Exit code | Situation |
|-----------|-----------|
| `0` | Success. Reports and assets were written. |
| `1` | No buildings matched the combined filters; nothing to analyse. |
| `1` (via `sys.exit`) | `--wms-url` was supplied without `--wms-layers`. |
| `1` (via `sys.exit`) | The resolved state has no bundled open-imagery provider and no override flags were given. |

Per-building errors (e.g. `RuntimeError` raised by `imagery.fetch_geoimage`) are caught inside the loop, logged to stdout, and skipped; processing continues with the next candidate.

## Extension points

### Add a new imagery provider

Register a new `Provider` instance in the `imagery.PROVIDERS` dictionary (key is the CLI name). Map the relevant German states to that key in `imagery.STATE_TO_PROVIDER` so that `imagery.provider_for_state()` auto-selects it. If you only need a one-off custom source, you can avoid code changes by passing `--wms-url`, `--wms-layers`, `--wms-epsg`, `--wms-resolution`, and `--wms-attribution` directly.

### Swap the panel detector

`main()` decides between ML and heuristic via `ml_detect.available()` and `--no-ml`. To introduce a new detector, replace or wrap `ml_detect.detect_panels_ml`. It must accept `(img, roof_px, resolution)` and return an object with `.coverage` (float), `.has_panels` (bool), and `.panel_boxes` (list). The fallback heuristic is `detect.detect_panels(...)` with the same signature.

### Adjust economics tiers

Turnkey price and feed-in tariff tiering are applied when `--eur-per-kwp` is `0`. Edit `economics.PRICE_TIERS` to change the size-dependent cost steps (default 1350 → 900 €/kWp) and `economics.FEED_IN_TIERS` to update the EEG feed-in tariff schedule. `economics.estimate()` consumes both tables automatically.