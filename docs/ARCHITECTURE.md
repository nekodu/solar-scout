# solar-scout

Screen German roofs on open aerial imagery, skip the ones that already have solar
panels (**YOLOv8 ML detection**), render a proposed panel layout onto each remaining
roof, compute the **address-specific sun exposure (PVGIS satellite data)**, and
estimate panels, m², kWp, price, annual yield, annual benefit, payback and CO₂
savings. Generates a browsable report, **print-ready German letters per owner**,
**interactive 3D views (three.js)** and a **mail-merge CSV**.

```
pip install -r requirements.txt

# one exact address (any address in Germany), analysed in real time
python -m solar_scout --address "Str. vor Schönholz 14, 13158 Berlin"

# residential, by neighbourhood
python -m solar_scout --city Köln --district Ehrenfeld --limit 25

# by postal code
python -m solar_scout --postcode 50823 --limit 40

# B2B: only commercial/industrial roofs (bigger systems, public contact data)
python -m solar_scout --city Köln --segment b2b --min-roof-area 400 --max-roof-area 10000
```

Open `out/report.html` when it finishes — or browse everything in the web UI:

```
python -m solar_scout.webui            # http://127.0.0.1:8765
```

The web UI discovers every run directory, shows a filterable/sortable card grid
(status, segment, search, sort by kWp/payback/price), aggregate stats, and a
per-roof detail view with the interactive 3D model, the aerial render and the
generated letter.

## Pipeline

1. **Filter → area** — city / district / postcode / address geocoded with Nominatim.
2. **Roofs** — OSM building footprints (Overpass), filtered by area, building type and
   segment (`--segment b2b|residential|all`). Segment classification is
   roof-aware: explicit tags first, then geometry - a large lone flat roof is
   a business hall, but terraced/attached blocks (≥2 touching neighbours) and
   complex multi-plane LoD2 roofscapes stay residential.
3. **Imagery** — 10–40 cm open-data orthophotos per roof (see sources below).
4. **Existing-panel detection (ML)** — YOLOv8s instance segmentation
   ([finloop/yolov8s-seg-solar-panels](https://huggingface.co/finloop/yolov8s-seg-solar-panels),
   MIT), weights auto-downloaded (~24 MB). Falls back to a classical-CV heuristic
   (`--no-ml` or if torch is unavailable). In testing the ML detector caught a fully
   covered roof the heuristic had missed.
5. **Roof shape** — measured first, inferred second:
   - **CityGML LoD2** (`lod2.py`): the states' open 3D building models (LiDAR)
     give the real roof form, pitch, plane azimuth and eaves height per
     building. Wired up with verified keyless tile downloads for Brandenburg,
     Berlin, NRW, Bayern and Niedersachsen (tiles cached in
     `~/.cache/solar_scout/lod2/`). `--no-lod2` disables.
   - Fallback (`roofs.py`): OSM `roof:shape`/`roof:angle` tags, else heuristic
     (industrial kinds & >600 m² → flat, residential → gabled 38°).
6. **Layout (obstruction-aware)** — flat roofs get south-facing racked rows
   (0.9 m inter-row gap) on the measured main slab, **minus everything LoD2
   says rises above it** (penthouses, mechanical rooms) and minus visually
   cluttered patches (equipment, terraces, skylights - per-cell edge-density
   check on the orthophoto, flat roofs only since tile texture defeats it).
   Surviving cells are then collapsed to contiguous installer-style strings
   (>= 3 adjacent panels per row, >= 4 total) so layouts route cleanly around
   chimneys and equipment instead of scattering single modules.
   Pitched roofs get the modules on the chosen measured plane, minus
   ground-overlapping dormer/superstructure footprints; without LoD2 the
   footprint-half method (stretched by 1/cos(pitch)) is the fallback. Roofs
   with no usable area left are reported as `obstructed`, not proposed. 1.13×1.72 m modules, edge margins, capped at
   `--usable-fraction` (35 %) of the footprint. With LoD2 the panels are
   planned directly ON the surveyed roof plane: one set of 3D panel quads
   drives the photo overlay, the 3D scene, the letter and the CSV - the
   artifacts cannot disagree. The 3D viewer renders the RAW LoD2 surfaces
   (every roof plane, hip and wall as flown); without LoD2 it falls back to a
   procedural gable.
7. **Sun exposure** — roof orientation derived from the footprint geometry, then
   **PVGIS v5.3** (EU JRC) returns the yield for the *exact coordinate*: SARAH-3
   satellite irradiance (2005–2023), ERA5 weather, terrain-horizon shading. Module
   tilt and azimuth come from the roof model: the gable pitch on the chosen
   roof plane, or 15° south-facing racks on flat roofs. `--no-pvgis` falls back to 950 kWh/kWp.
   *Why not ML for this? PVGIS already encodes 18+ years of measured satellite
   irradiance with a validated physical PV model — ML is used where it wins
   (image recognition), physics where it wins (energy yield).*
8. **Economics** — size-tiered turnkey prices (1,350 €/kWp ≤10 kWp … 900 €/kWp >100 kWp,
   0 % VAT), tiered EEG feed-in tariff (02–07/2026: 7.78/6.73/5.50 ct/kWh),
   self-consumption savings (35 ct/kWh residential, 25 ct/kWh + 60 % self-use B2B),
   payback years, CO₂ savings (0.35 kg/kWh grid mix).

## Outputs

| File | Purpose |
|---|---|
| `out/report.html` | card gallery with all figures and totals |
| `out/report.csv` | full data (incl. orientation, yield, payback, contacts) |
| `out/mailmerge.csv` | one row per eligible roof → feed to any mail-merge/CRM tool |
| `out/letters/<id>_brief.html` | print-ready German A4 letter per roof (image embedded, self-contained) — print to PDF and post |
| `out/viewers/<id>_3d.html` | interactive three.js 3D view: orthophoto ground, extruded building, panel array |
| `out/images/*.png` | static renders (these go inside the letters) |

**Why letters + linked 3D instead of three.js in the mail:** e-mail clients do not
execute JavaScript. The mailable artifact is the static render inside a printable
letter (or its PDF); the 3D page is an attachment/link for follow-up conversations.

## Demo: in-house tool concept

- **Customer side** (`/`): anyone searches their address, gets a real-time
  roof analysis and a private result sheet, and can request a quote
  (first-party: the data stays with the operating company; GDPR-clean opt-in).
- **Internal side** (`/partner`, "Demand-Cockpit"): the ops view a solar
  company would use. Branch (Standort) region picker, weekly activity,
  KPI chips, funnel (roof checks -> suitable roofs -> quote requests),
  consented requests routed to the local Meisterbetrieb, a region comparison
  (where demand grows, conversion per district) and integration hooks
  (CRM CSV export, Heartbeat candidates, expansion gap analysis).
- **Branding** is env-driven (PARTNER_NAME etc., see docker-compose.yml), so
  the demo can be re-pointed at another company in one restart.
- **Demo data**: `python -m solar_scout.demo_seed` seeds ~9 weeks of plausible
  Berlin activity (synthetic OSM ids >= 900000000; remove with `--wipe`).
- **Consent model (GDPR)**: named requests exist only after the homeowner
  explicitly opts in; everything else aggregates anonymously per district.
  Revoked consent removes the request.

## Market intelligence

Every analysis (web searches and CLI scans) is upserted into `market.db`
(SQLite) and aggregated by city / suburb / postcode: roofs analysed, eligible,
B2B share, total kWp, investment volume, average payback. The web UI shows the
"Marktübersicht" table; `/api/market/export.csv` exports the aggregates (add
`?full=true` for raw rows - but license the AGGREGATES to PV businesses;
selling rows about identifiable private homes needs a GDPR legal basis).
Individual web-search results remain private/ephemeral for visitors.

## Outreach & legal (Germany — not legal advice)

- **Postal mail works without knowing the owner**: address it to
  "An die Eigentümerin / den Eigentümer des Gebäudes …" — the letters are generated
  exactly like that. Owner *names* are not in any open dataset (Grundbuch access
  requires legitimate interest; GDPR).
- **E-mail requires prior consent** under §7 UWG — *even B2B*. The B2B segment is
  still the better channel: businesses publish their own contact data (OSM tags,
  Impressum, registries), phone cold-calls to businesses are permissible with
  presumed interest, and big flat roofs + 60 % daytime self-consumption give
  payback ≈6 years. `mailmerge.csv` carries any publicly OSM-tagged
  email/phone/website per building.

## Imagery sources — due diligence

| Source | Verdict |
|---|---|
| EUMETSAT | ✗ weather satellites, km-scale pixels |
| Google Earth/Maps | ✗ ToS forbids derivative analysis (their paid Solar API is the commercial route) |
| Sentinel-2 | ✗ 10 m/px, too coarse |
| BKG nationwide DOP20 | ✗ GetMap fee-gated (tested) |
| **State open-data orthophotos** | ✓ 10–40 cm, free, permissive licences |

Bundled: verified open orthophoto WMS for **all 16 federal states** (10–40 cm
resolution; every endpoint passed a real keyless GetMap test on 2026-06-11),
auto-selected from the geocoded state — full nationwide address coverage.
A custom/newer WMS can still be plugged in via `--wms-url … --wms-layers …`.

## Estimates, not promises (legal framing)

Every artifact (web UI, letters, reports, 3D, exports) is explicitly labelled a
**non-binding automated estimate** ("unverbindliche Schätzung"). This is not
just modesty — under German case law a yield forecast that is not clearly
non-binding can become a *warranted characteristic* (shortfalls of ~10 % are
then treated as defects), and pre-contractual advice must disclose risks; even
advertising the 0 % VAT rate without its §12 Abs. 3 UStG conditions has been
ruled anti-competitive. The shared texts live in `disclaimer.py`; the engine's
known error sources (detection misses/false alarms, data age, unmodelled
shading, unchecked statics, moving prices/tariffs, ±20 %+ deviations) are
spelled out there. Do not weaken them in customer-facing output.

## Limitations

- Screening, not engineering: statics, on-roof obstacles (HVAC, skylights, dormers)
  and inter-row shading are not modelled; the 35 % usable fraction absorbs this on
  average. PVGIS horizon covers *terrain* shading, not neighbouring buildings/trees.
- Roof pitch/azimuth/heights are **measured (LoD2)** in BB/BE/NW/BY/NI; elsewhere
  tag-based or assumed (38° gable default). The 3D viewer body is still a
  procedural gable fitted with the measured parameters — rendering the raw LoD2
  surface polygons (incl. hips, dormers, L-shapes) is the next refinement.
- OSM footprint completeness varies; orthophotos are 1–3 years old.
- The YOLO model is community-trained; validate on your target area before trusting
  it at scale (`--include-existing` saves the detections for review).

## Attribution

Imagery © GeoBasis NRW (dl-de/zero-2-0) / © Bayerische Vermessungsverwaltung
(CC BY 4.0); footprints © OpenStreetMap contributors (ODbL); geocoding Nominatim;
irradiance PVGIS © European Union; detector model MIT (finloop). The report and
letters include the required attributions.
