"""Single-building analysis - shared by the CLI batch loop and the web UI."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import cv2
from pyproj import Transformer

from . import (buildings, detect, economics, imagery, layout, letter, lod2,
               ml_detect, roofs, sun, viewer3d)

GERMAN_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                 "August", "September", "Oktober", "November", "Dezember"]
ENGLISH_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]


@dataclass
class Options:
    usable_fraction: float = 0.35
    panel_wp: float = 440.0
    eur_per_kwp: float = 0.0          # 0 = size-tiered market prices
    electricity_price: float = 0.35
    self_consumption: float = 0.30
    tilt: float = 0.0                 # 0 = auto (35 pitched / 15 flat-racked)
    use_ml: bool = True
    use_pvgis: bool = True
    use_lod2: bool = True
    letters: bool = True
    viewer: bool = True
    include_existing: bool = False


def german_date() -> str:
    t = date.today()
    return f"{t.day}. {GERMAN_MONTHS[t.month - 1]} {t.year}"


def english_date() -> str:
    t = date.today()
    return f"{ENGLISH_MONTHS[t.month - 1]} {t.day}, {t.year}"


def base_assumptions(opts: Options) -> economics.Assumptions:
    return economics.Assumptions(
        panel_wp=opts.panel_wp, eur_per_kwp=opts.eur_per_kwp,
        electricity_price=opts.electricity_price,
        self_consumption=opts.self_consumption)


def analyze_one(b: "buildings.Building", poly, provider: imagery.Provider,
                opts: Options, out_dir: Path, iso: str = None,
                touching: int = 0, locality: dict = None,
                display_address: str = None) -> dict:
    """Analyse one building footprint (already projected to provider CRS).

    Writes images/letter/3D viewer under out_dir and returns the report row.
    Raises RuntimeError if imagery cannot be fetched.
    """
    for sub in ("images", "letters", "viewers"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    geoimg = imagery.fetch_geoimage(provider, poly.bounds)
    roof_px = [geoimg.to_px(x, y) for x, y in poly.exterior.coords]

    det = None
    use_ml = opts.use_ml and ml_detect.available()
    if use_ml:
        det = ml_detect.detect_panels_ml(geoimg.img, roof_px, geoimg.resolution)
    if det is None:
        use_ml = False
        det = detect.detect_panels(geoimg.img, roof_px, geoimg.resolution)

    contact = b.contact
    loc = locality or {}
    # display_address replaces the real address in EVERY artifact (sheet, 3D
    # HUD, letter) - used for demo example properties whose owners should not
    # have their address shown
    row = {
        "osm_id": b.osm_id,
        "address": display_address or b.address,
        "building_type": b.kind,
        "segment": "b2b" if b.is_business else "residential",   # refined below
        "postcode": b.tags.get("addr:postcode") or loc.get("postcode", ""),
        "city": b.tags.get("addr:city") or loc.get("city", ""),
        "suburb": b.tags.get("addr:suburb") or loc.get("suburb", ""),
        "business_name": b.business_name,
        "contact_email": contact["email"], "contact_phone": contact["phone"],
        "website": contact["website"],
        "footprint_m2": round(poly.area, 1),
        "existing_panel_coverage": round(det.coverage, 3),
        "detector": "yolov8" if use_ml else "heuristic",
        "proposed_panels": 0, "proposed_kwp": 0.0, "proposed_panel_area_m2": 0.0,
        "orientation": "", "specific_yield": 0, "estimated_cost_eur": 0.0,
        "estimated_annual_kwh": 0.0, "annual_benefit_eur": 0.0,
        "payback_years": 0.0, "co2_t_per_year": 0.0,
        "image": "", "letter": "", "letter_en": "", "viewer": "",
        "osm_url": f"https://www.openstreetmap.org/way/{b.osm_id}",
    }

    if det.has_panels:
        row["status"] = "has_panels"
        if opts.include_existing:
            img = layout.render(geoimg, roof_px, [], geoimg.to_px, det.panel_boxes)
            rel = f"images/{b.osm_id}_existing.png"
            cv2.imwrite(str(out_dir / rel), img)
            row["image"] = rel
        return row

    spec = roofs.infer_roof(b, poly.area)
    prefer_aspect, m, lod2_geom = None, None, None
    if opts.use_lod2:
        c = b.polygon.centroid
        m = lod2.measure(iso, c.x, c.y)
        if m:                                   # measured beats inferred
            if m.is_flat:
                spec = roofs.RoofSpec(roofs.FLAT, roofs.FLAT_RACK_TILT,
                                      m.eaves_height, f"{m.label} (LoD2)")
            else:
                spec = roofs.RoofSpec(roofs.GABLED, min(max(m.pitch, 15.0), 65.0),
                                      min(m.eaves_height, 30.0), f"{m.label} (LoD2)")
                prefer_aspect = m.aspect
    # roof-based segment classification (big single flat roof = business,
    # but tagged/complex residential blocks stay residential)
    row["segment"] = buildings.classify_segment(b, poly.area, m, touching)
    if opts.tilt:
        spec.pitch = opts.tilt

    target = int(poly.area * opts.usable_fraction
                 // (layout.PANEL_W * layout.PANEL_L))
    plan = None
    if m and m.surfaces:
        if m.epsg != provider.epsg:
            tr = Transformer.from_crs(m.epsg, provider.epsg, always_xy=True).transform
            conv = lambda ring: [(*tr(p[0], p[1]), p[2]) for p in ring]
        else:
            conv = lambda ring: [tuple(p) for p in ring]
        # raw surveyed surfaces always go to the 3D viewer when available
        lod2_geom = {"surfaces": [(k, conv(r)) for k, r in m.surfaces],
                     "ground_z": m.ground_z}
        if m.best_ring and not opts.tilt:
            # plan directly ON the surveyed plane, minus everything LoD2 says
            # rises above it (penthouses, dormers): one set of 3D quads drives
            # photo overlay, 3D scene, letter and figures
            obstr = [conv(r) for r in (m.obstructions or [])]
            if m.is_flat:
                plan = layout.plan_flat_measured(conv(m.best_ring), target, obstr)
            else:
                plan = layout.plan_on_plane(conv(m.best_ring), target,
                                            obstructions=obstr)
            if plan is not None and not plan.n_panels:
                plan = "obstructed"
    if plan is None:
        plan = layout.plan(poly, opts.usable_fraction, spec, prefer_aspect)
    if plan == "obstructed" or not plan.n_panels:
        row["status"] = "obstructed" if plan == "obstructed" else "too_small"
        if opts.include_existing:
            img = layout.render(geoimg, roof_px, [], geoimg.to_px)
            rel = f"images/{b.osm_id}_roof.png"
            cv2.imwrite(str(out_dir / rel), img)
            row["image"] = rel
        return row

    # image-based obstruction check, FLAT ROOFS ONLY: clear membrane/gravel is
    # smooth, equipment/terraces/skylights are edge-dense. (Pitched tile roofs
    # are texture-rich and would false-positive; their dormers are handled by
    # the LoD2 ground-overlap mask instead.)
    if plan.shape in ("flat", "lod2_flat"):
        cells_px = [[geoimg.to_px(x, y) for x, y in p.exterior.coords]
                    for p in plan.panels_ground]
        keep = detect.unobstructed_indices(geoimg.img, cells_px)
        # installers build contiguous strings: collapse hole-punched cells to
        # clean runs that route around obstructions (chimneys, equipment)
        keep = layout.coherent_indices(plan, keep,
                                       layout.PANEL_L + layout.FLAT_ROW_GAP)
        if len(keep) < len(plan.panels_ground):
            plan.panels_ground = [plan.panels_ground[i] for i in keep]
            if plan.panels3d:
                plan.panels3d = [plan.panels3d[i] for i in keep]
    if not plan.n_panels:
        row["status"] = "obstructed"
        if opts.include_existing:
            img = layout.render(geoimg, roof_px, [], geoimg.to_px)
            rel = f"images/{b.osm_id}_roof.png"
            cv2.imwrite(str(out_dir / rel), img)
            row["image"] = rel
        return row

    kwp = plan.n_panels * opts.panel_wp / 1000.0
    pv = None
    if opts.use_pvgis:
        lonlat = b.polygon.centroid
        pv = sun.pv_yield(lonlat.y, lonlat.x, kwp, plan.tilt, plan.aspect)
    assum = base_assumptions(opts)
    if row["segment"] == "b2b":
        assum = assum.for_business()
    est = economics.estimate(plan.n_panels, assum,
                             annual_kwh=pv["annual_kwh"] if pv else 0.0,
                             specific_yield=pv["specific_yield"] if pv else 0.0)

    img = layout.render(geoimg, roof_px, plan.panels_ground, geoimg.to_px)
    rel = f"images/{b.osm_id}_proposal.png"
    cv2.imwrite(str(out_dir / rel), img)
    row.update(
        status="eligible", image=rel,
        proposed_panels=est.n_panels, proposed_kwp=est.kwp,
        proposed_panel_area_m2=est.panel_area_m2,
        orientation=f"{spec.label} {sun.compass_label(plan.aspect)} {plan.tilt:.0f}°",
        specific_yield=est.specific_yield,
        estimated_cost_eur=est.cost_eur, estimated_annual_kwh=est.annual_kwh,
        annual_benefit_eur=est.annual_benefit_eur, payback_years=est.payback_years,
        co2_t_per_year=est.co2_t_per_year)

    if opts.viewer:
        vrel = f"viewers/{b.osm_id}_3d.html"
        viewer3d.write_viewer(poly, plan, spec, geoimg, row, out_dir / vrel,
                              lod2_geom=lod2_geom)
        row["viewer"] = vrel
    if opts.letters:
        row_letter = dict(row, self_consumption_pct=assum.self_consumption * 100,
                          electricity_price_ct=assum.electricity_price * 100,
                          feed_in_ct=est.feed_in_rate * 100)
        # both languages are generated up front so a language switch in the UI
        # can never serve a stale letter in the wrong language
        lrel = f"letters/{b.osm_id}_brief_de.html"
        letter.write_letter(row_letter, img, out_dir / lrel, german_date(),
                            row["viewer"], lang="de")
        lrel_en = f"letters/{b.osm_id}_brief_en.html"
        letter.write_letter(row_letter, img, out_dir / lrel_en, english_date(),
                            row["viewer"], lang="en")
        row["letter"] = lrel
        row["letter_en"] = lrel_en
    return row
