"""Command-line entry point.

Examples:
    python -m solar_scout --address "Str. vor Schönholz 14, 13158 Berlin"
    python -m solar_scout --city Köln --district Ehrenfeld --limit 20
    python -m solar_scout --postcode 50823 --limit 40
    python -m solar_scout --city Köln --segment b2b --min-roof-area 400
"""

import argparse
import sys
import time
from pathlib import Path

from pyproj import Transformer
from shapely.ops import transform as shp_transform
from shapely.strtree import STRtree

from . import buildings, geo, imagery, market, ml_detect, pipeline, report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="solar-scout",
        description="Find roofs without solar panels on German open aerial imagery, "
                    "render a panel layout and estimate size, yield, price and payback.")
    loc = p.add_argument_group("location filters")
    loc.add_argument("--address", help="one exact address -> analyse just that building, "
                                       "e.g. 'Str. vor Schönholz 14, 13158 Berlin'")
    loc.add_argument("--city")
    loc.add_argument("--district", help="neighbourhood / Stadtteil, e.g. Ehrenfeld")
    loc.add_argument("--postcode", help="Postleitzahl, e.g. 50823")
    loc.add_argument("--query", help="free-form area query")
    loc.add_argument("--radius", type=int, default=250,
                     help="search radius in m when the filter resolves to a point")

    sel = p.add_argument_group("selection (area scans)")
    sel.add_argument("--limit", type=int, default=25, help="max roofs to analyse")
    sel.add_argument("--min-roof-area", type=float, default=40.0, help="m² footprint")
    sel.add_argument("--max-roof-area", type=float, default=3000.0, help="m² footprint")
    sel.add_argument("--segment", choices=["all", "b2b", "residential"], default="all",
                     help="b2b = only commercial/industrial roofs (public contact data, "
                          "bigger systems, faster payback)")

    img = p.add_argument_group("imagery (auto-picked from the state; override for other states)")
    img.add_argument("--provider", choices=sorted(imagery.PROVIDERS))
    img.add_argument("--wms-url", help="any other open WMS endpoint")
    img.add_argument("--wms-layers")
    img.add_argument("--wms-epsg", type=int, default=25832)
    img.add_argument("--wms-resolution", type=float, default=0.2, help="m/pixel")
    img.add_argument("--wms-attribution", default="custom WMS")

    eco = p.add_argument_group("assumptions")
    eco.add_argument("--usable-fraction", type=float, default=0.35)
    eco.add_argument("--panel-wp", type=float, default=440.0)
    eco.add_argument("--eur-per-kwp", type=float, default=0.0,
                     help="0 = size-tiered market prices (1350 -> 900 €/kWp)")
    eco.add_argument("--electricity-price", type=float, default=0.35, help="€/kWh")
    eco.add_argument("--self-consumption", type=float, default=0.30,
                     help="share of production used on site (B2B segment uses 0.60)")
    eco.add_argument("--tilt", type=float, default=0.0,
                     help="module tilt in deg; 0 = auto (35 pitched / 15 flat-racked)")

    feat = p.add_argument_group("features")
    feat.add_argument("--no-ml", action="store_true", help="skip YOLO, use CV heuristic")
    feat.add_argument("--no-pvgis", action="store_true",
                      help="skip per-address PVGIS irradiance (use flat 950 kWh/kWp)")
    feat.add_argument("--no-lod2", action="store_true",
                      help="skip measured CityGML LoD2 roof shapes (use OSM/heuristic)")
    feat.add_argument("--no-letters", action="store_true")
    feat.add_argument("--no-3d", action="store_true")
    feat.add_argument("--include-existing", action="store_true",
                      help="also save images of roofs where panels were detected")
    p.add_argument("--out", default="out", help="output directory")
    return p


def pick_provider(args, place) -> imagery.Provider:
    if args.wms_url:
        if not args.wms_layers:
            sys.exit("--wms-url requires --wms-layers")
        return imagery.Provider("custom", args.wms_url, args.wms_layers,
                                args.wms_epsg, args.wms_resolution, args.wms_attribution)
    if args.provider:
        return imagery.PROVIDERS[args.provider]
    prov = imagery.provider_for_place(place)
    if prov is None:
        sys.exit(f"No bundled open-imagery provider for {place.state!r} ({place.iso}). "
                 f"Pass --provider, or --wms-url/--wms-layers with your state's open "
                 f"DOP WMS (most German states publish one, see README).")
    return prov


def options_from_args(args) -> pipeline.Options:
    return pipeline.Options(
        usable_fraction=args.usable_fraction, panel_wp=args.panel_wp,
        eur_per_kwp=args.eur_per_kwp, electricity_price=args.electricity_price,
        self_consumption=args.self_consumption, tilt=args.tilt,
        use_ml=not args.no_ml, use_pvgis=not args.no_pvgis,
        use_lod2=not args.no_lod2,
        letters=not args.no_letters, viewer=not args.no_3d,
        include_existing=args.include_existing)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.address:
        place, note = geo.geocode_address(args.address)
        if note:
            print(f"Note: {note}")
    else:
        place = geo.geocode(args.query, args.city, args.district, args.postcode)
    print(f"Area: {place.display_name}")
    provider = pick_provider(args, place)
    print(f"Imagery: {provider.key} ({provider.resolution} m/px) — {provider.attribution}")
    opts = options_from_args(args)
    use_ml = opts.use_ml and ml_detect.available()
    print(f"Panel detector: {'YOLOv8-seg (' + ml_detect.HF_REPO + ')' if use_ml else 'CV heuristic'}")

    to_proj = Transformer.from_crs(4326, provider.epsg, always_xy=True).transform

    neighbor_count = {}
    if args.address:
        opts.include_existing = True   # the user asked about *this* roof - always show it
        b = buildings.fetch_building_at(place)
        candidates = [(b, shp_transform(to_proj, b.polygon))]
    else:
        blds = buildings.fetch_buildings(place, radius_m=args.radius)
        print(f"OSM building footprints fetched: {len(blds)}")
        all_polys = [shp_transform(to_proj, b.polygon) for b in blds]
        tree = STRtree(all_polys)
        candidates = []
        for b, poly in zip(blds, all_polys):
            if args.segment == "b2b" and not b.is_business:
                continue
            if args.segment == "residential" and b.is_business:
                continue
            if args.min_roof_area <= poly.area <= args.max_roof_area:
                # touching neighbours separate terraced blocks from lone halls
                idx = tree.query(poly)
                neighbor_count[b.osm_id] = sum(
                    1 for i in idx
                    if all_polys[i] is not poly and all_polys[i].distance(poly) < 0.2)
                candidates.append((b, poly))
        candidates.sort(key=lambda t: t[1].area, reverse=True)
        candidates = candidates[: args.limit]
        print(f"Candidates after segment/area filter (limit {args.limit}): {len(candidates)}")

    rows, n_eligible = [], 0
    for i, (b, poly) in enumerate(candidates, 1):
        try:
            row = pipeline.analyze_one(b, poly, provider, opts, out_dir,
                                       iso=place.iso,
                                       touching=neighbor_count.get(b.osm_id, 0),
                                       locality=place.locality)
        except RuntimeError as exc:
            print(f"  [{i}/{len(candidates)}] {b.address}: imagery failed ({exc})")
            continue
        rows.append(row)
        if row["status"] == "eligible":
            n_eligible += 1
            print(f"  [{i}/{len(candidates)}] {b.address}: ELIGIBLE — "
                  f"{row['proposed_panels']} panels, {row['proposed_kwp']} kWp, "
                  f"{row['orientation']}, {row['specific_yield']} kWh/kWp, "
                  f"≈{row['estimated_cost_eur']:,.0f} €, payback {row['payback_years']} a")
        elif row["status"] == "has_panels":
            print(f"  [{i}/{len(candidates)}] {b.address}: existing panels "
                  f"({row['existing_panel_coverage']:.0%} coverage) — skipped")
        elif row["status"] == "obstructed":
            print(f"  [{i}/{len(candidates)}] {b.address}: roof obstructed "
                  f"(superstructures/clutter) — no usable area")
        else:
            print(f"  [{i}/{len(candidates)}] {b.address}: roof too small after margins")
        time.sleep(0.15)   # be polite to the public APIs

    if not rows:
        print("Nothing analysed — no buildings matched the filters.")
        return 1

    note = (f"Assumptions: {args.panel_wp:.0f} Wp modules, usable area = "
            f"{args.usable_fraction:.0%} of footprint, size-tiered turnkey prices "
            f"(0% VAT), EEG tariff 02-07/2026, yield per address via PVGIS SARAH-3.")
    report.write_csv(rows, out_dir / "report.csv")
    report.write_mailmerge(rows, out_dir / "mailmerge.csv")
    market.upsert(Path("market.db"), rows)
    report.write_html(rows, out_dir / "report.html",
                      title=f"Solar potential — {place.display_name}",
                      attribution=provider.attribution, assumptions_note=note)
    print(f"\nDone: {n_eligible} eligible roofs out of {len(rows)} analysed.")
    print(f"Report:    {out_dir / 'report.html'}")
    print(f"CSV:       {out_dir / 'report.csv'}  |  mail-merge: {out_dir / 'mailmerge.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
