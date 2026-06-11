"""CSV and HTML report writers."""

import csv
import html
from pathlib import Path
from typing import List

CSV_FIELDS = [
    "osm_id", "address", "building_type", "segment", "business_name",
    "contact_email", "contact_phone", "website", "status", "footprint_m2",
    "existing_panel_coverage", "detector", "proposed_panels", "proposed_kwp",
    "proposed_panel_area_m2", "orientation", "specific_yield",
    "estimated_cost_eur", "estimated_annual_kwh", "annual_benefit_eur",
    "payback_years", "co2_t_per_year", "image", "letter", "letter_en",
    "viewer", "osm_url",
]

MAILMERGE_FIELDS = [
    "address", "segment", "business_name", "contact_email", "contact_phone",
    "website", "proposed_kwp", "estimated_cost_eur", "estimated_annual_kwh",
    "annual_benefit_eur", "payback_years", "letter", "viewer",
]


def write_csv(rows: List[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_mailmerge(rows: List[dict], path: Path) -> None:
    """Eligible roofs only, one line per letter - feed this to any mail-merge tool."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MAILMERGE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(r for r in rows if r["status"] == "eligible")


def write_html(rows: List[dict], path: Path, title: str, attribution: str,
               assumptions_note: str) -> None:
    eligible = [r for r in rows if r["status"] == "eligible"]
    total_kwp = sum(r["proposed_kwp"] for r in eligible)
    total_cost = sum(r["estimated_cost_eur"] for r in eligible)
    total_m2 = sum(r["proposed_panel_area_m2"] for r in eligible)

    cards = []
    for r in rows:
        badge = ("<span class='badge ok'>eligible</span>" if r["status"] == "eligible"
                 else "<span class='badge has'>existing panels</span>")
        if r["status"] == "eligible":
            links = " &middot; ".join(
                f"<a href='{r[k]}'>{lbl}</a>" for k, lbl in
                (("letter", "letter"), ("viewer", "3D")) if r.get(k))
            biz = (f"<p><b>{html.escape(r['business_name'])}</b> "
                   f"{html.escape(r.get('contact_email') or '')}</p>"
                   if r.get("business_name") else "")
            body = (
                f"{biz}<p>{r['proposed_panels']} panels &middot; {r['proposed_kwp']} kWp &middot; "
                f"{r['proposed_panel_area_m2']} m&sup2; &middot; {html.escape(r.get('orientation',''))}<br>"
                f"&approx; {r['estimated_cost_eur']:,.0f} &euro; turnkey &middot; "
                f"&approx; {r['estimated_annual_kwh']:,.0f} kWh/a "
                f"({r['specific_yield']} kWh/kWp, PVGIS)<br>"
                f"benefit &approx; {r['annual_benefit_eur']:,.0f} &euro;/a &middot; "
                f"payback &approx; {r['payback_years']} a &middot; "
                f"{r['co2_t_per_year']} t CO&#8322;/a"
                + (f"<br>{links}" if links else "") + "</p>")
        else:
            body = f"<p>detected module coverage: {r['existing_panel_coverage']:.0%}</p>"
        cards.append(f"""
  <div class="card">
    <img src="{html.escape(r['image'])}" loading="lazy">
    <div class="meta">
      {badge}
      <h3>{html.escape(r['address'])}</h3>
      <p>{html.escape(r['building_type'])} &middot; footprint {r['footprint_m2']:.0f} m&sup2;
         &middot; <a href="{r['osm_url']}">OSM</a></p>
      {body}
    </div>
  </div>""")

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; background:#10151c; color:#e8e8e8; }}
 h1 {{ font-weight: 600; }} a {{ color:#7ab8ff; }}
 .summary {{ background:#1b2330; padding:1rem 1.5rem; border-radius:10px; margin-bottom:1.5rem; }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:1.2rem; }}
 .card {{ background:#1b2330; border-radius:10px; overflow:hidden; }}
 .card img {{ width:100%; display:block; aspect-ratio:1; object-fit:cover; }}
 .meta {{ padding:.8rem 1rem 1rem; }} .meta h3 {{ margin:.4rem 0 .2rem; font-size:1rem; }}
 .meta p {{ margin:.25rem 0; font-size:.85rem; color:#b8c2d0; }}
 .badge {{ font-size:.72rem; padding:.15rem .55rem; border-radius:99px; font-weight:600; }}
 .badge.ok {{ background:#1f5c2e; color:#9ff0b3; }} .badge.has {{ background:#5c2e1f; color:#f0b89f; }}
 footer {{ margin-top:2rem; font-size:.8rem; color:#8a93a3; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<div class="summary">
  <b>{len(eligible)}</b> of {len(rows)} analysed roofs eligible (no panels detected) &middot;
  potential <b>{total_kwp:,.0f} kWp</b> on <b>{total_m2:,.0f} m&sup2;</b> of modules &middot;
  estimated investment <b>{total_cost:,.0f} &euro;</b>
</div>
<div class="grid">{''.join(cards)}
</div>
<footer>Imagery: {html.escape(attribution)} &middot; Footprints: &copy; OpenStreetMap contributors (ODbL)
<br>{html.escape(assumptions_note)}
<br>All figures are NON-BINDING automated estimates, not warranted characteristics:
detection of existing modules and obstructions can fail in both directions, imagery
and 3D data may be years old, tree/neighbour shading is not modelled, statics and
electrics are unchecked, prices and tariffs move. Deviations of &plusmn;20&nbsp;% and
more are possible; binding figures require an on-site assessment.</footer>
</body></html>"""
    path.write_text(doc, encoding="utf-8")
