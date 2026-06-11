"""Print-ready German letters for owners (B2C) and businesses (B2B).

Each letter is a self-contained A4-styled HTML file (image embedded as
base64). Open and print to PDF, or feed the whole batch to a mail-merge tool
via out/mailmerge.csv. Layout loosely follows DIN 5008 (window envelope
address position, date right, bold subject line).

Legal note (Germany): postal mail to "the owner of <address>" is permitted;
unsolicited e-mail requires prior consent under §7 UWG, even B2B. Owner names
are not in any open dataset (GDPR); businesses publish contact data
themselves. The estimate framing in the fine print is legally load-bearing,
see disclaimer.py.
"""

import base64
import html
from pathlib import Path

import cv2
import numpy as np

from . import disclaimer

# letterhead placeholders: replace with the real company before sending
BRAND = "solar·scout"
SENDER_LINE = "[Ihr Firmenname] · [Straße Nr.] · [PLZ Ort]"
SENDER_CONTACT = "[Telefon] · [E-Mail] · [www.ihre-firma.de]"

# letters exist in German and English; ADDRESSES always stay German
TEXTS = {
    "de": {
        "tagline": "Dachanalyse aus amtlichen Geodaten",
        "object_no": "Objekt-Nr.", "page": "Seite 1 von 1",
        "owner": "An die Eigentümerin / den Eigentümer<br>des Gebäudes",
        "mgmt": "Geschäftsleitung",
        "subject": "Ihr Dach kann mehr: ca. {kwp:.1f} kWp Solarpotenzial",
        "subject_note": "(unverbindliche Schätzung)",
        "salutation": "Sehr geehrte Damen und Herren,",
        "intro": "wir haben Ihr Dach anhand amtlicher Luftbilder und "
                 "3D-Gebäudedaten geprüft. Das Ergebnis: Ihre Dachfläche eignet "
                 "sich voraussichtlich gut für eine Photovoltaikanlage, und "
                 "bislang wurden dort keine Module erkannt. Das Wichtigste auf "
                 "einen Blick:",
        "k_size": "Anlagengröße", "k_kwh": "Strom pro Jahr",
        "k_benefit": "Vorteil pro Jahr", "k_payback": "Amortisation",
        "figcap": "Mögliche Modulbelegung auf dem Luftbild Ihres Dachs "
                  "({n} Module, Schätzwerte*, Prüfung vor Ort vorbehalten)",
        "t_area": "Belegte Modulfläche",
        "t_orient": "Dachausrichtung und Sonnenertrag*",
        "t_orient_unit": "kWh/kWp im Jahr",
        "t_invest": "Geschätzte Investition (schlüsselfertig, 0&nbsp;% USt.*)",
        "t_co2": "CO&#8322;-Einsparung pro Jahr",
        "steps_h": "So einfach geht es weiter:",
        "s1": "Sie melden sich kostenlos und unverbindlich bei uns.",
        "s2": "Wir prüfen Ihr Dach vor Ort und erstellen ein verbindliches Angebot.",
        "s3": "Montage, Anmeldung und Inbetriebnahme aus einer Hand.",
        "closing": "Wir freuen uns auf Ihre Nachricht. Antworten Sie einfach auf "
                   "dieses Schreiben oder rufen Sie uns an.",
        "regards": "Mit sonnigen Grüßen",
        "assumptions": "Wirtschaftlichkeitsannahmen: {sc:.0f}&nbsp;% Eigenverbrauch, "
                       "{ep:.0f}&nbsp;ct/kWh Strompreis, EEG-Vergütung "
                       "{fi:.2f}&nbsp;ct/kWh (Stand 06/2026). Keine Rechts- oder "
                       "Steuerberatung. Werbung. Bildquellen: amtliche "
                       "Geobasisdaten der Länder, © OpenStreetMap (ODbL). "
                       "Verantwortlich: {sender}.",
        "disclaimer": disclaimer.LETTER_DE,
        "yr": "J.",
    },
    "en": {
        "tagline": "Roof analysis from official geodata",
        "object_no": "Property ref.", "page": "Page 1 of 1",
        "owner": "To the owner(s)<br>of the building at",
        "mgmt": "Management",
        "subject": "Your roof can do more: approx. {kwp:.1f} kWp of solar potential",
        "subject_note": "(non-binding estimate)",
        "salutation": "Dear Sir or Madam,",
        "intro": "We analysed your roof using official aerial imagery and 3D "
                 "building data. The result: your roof area appears well suited "
                 "for a photovoltaic system, and no existing modules were "
                 "detected. The key figures at a glance:",
        "k_size": "System size", "k_kwh": "Electricity per year",
        "k_benefit": "Benefit per year", "k_payback": "Payback",
        "figcap": "Possible module layout on the aerial image of your roof "
                  "({n} modules, estimates*, subject to on-site survey)",
        "t_area": "Module area used",
        "t_orient": "Roof orientation and solar yield*",
        "t_orient_unit": "kWh/kWp per year",
        "t_invest": "Estimated investment (turnkey, 0&nbsp;% VAT*)",
        "t_co2": "CO&#8322; savings per year",
        "steps_h": "Three simple steps:",
        "s1": "Contact us, free of charge and without obligation.",
        "s2": "We survey your roof on site and prepare a binding quote.",
        "s3": "Installation, registration and commissioning from one provider.",
        "closing": "We look forward to hearing from you. Simply reply to this "
                   "letter or give us a call.",
        "regards": "With sunny regards",
        "assumptions": "Economic assumptions: {sc:.0f}% self-consumption, "
                       "{ep:.0f} ct/kWh electricity price, German feed-in "
                       "tariff {fi:.2f} ct/kWh (as of 06/2026). No legal or tax "
                       "advice. Advertising. Imagery: official German state "
                       "geodata, © OpenStreetMap (ODbL). Responsible: {sender}.",
        "disclaimer": disclaimer.LETTER_EN,
        "yr": "yrs",
    },
}


def _img_b64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf).decode() if ok else ""


def _recipient(row: dict, tx: dict) -> str:
    if row.get("business_name"):
        return (f"{html.escape(row['business_name'])}<br>{tx['mgmt']}<br>"
                f"{html.escape(row['address'])}")
    return f"{tx['owner']}<br>{html.escape(row['address'])}"


def write_letter(row: dict, render_img: np.ndarray, out_path: Path,
                 date_str: str, viewer_rel: str = "", lang: str = "de") -> None:
    e = row
    tx = TEXTS.get(lang, TEXTS["de"])
    doc = f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<title>Solar potential: {html.escape(e['address'])}</title>
<style>
 @page {{ size: A4; margin: 16mm 18mm; }}
 :root {{ --sun:#e8a013; --ink:#1c1a17; --mut:#6e675e; --line:#e3ddd2; }}
 * {{ box-sizing: border-box; }}
 body {{ font-family: Georgia, 'Times New Roman', serif; color: var(--ink);
        max-width: 174mm; margin: 8mm auto; line-height: 1.5; font-size: 11pt;
        background: #fff; }}
 .brandbar {{ display: flex; align-items: baseline; justify-content: space-between;
        border-bottom: 2.5px solid var(--sun); padding-bottom: 3mm; margin-bottom: 8mm; }}
 .brand {{ font-size: 16pt; font-weight: bold; letter-spacing: .3px; }}
 .brand span {{ color: var(--sun); }}
 .brandsub {{ font-family: Helvetica, Arial, sans-serif; font-size: 8pt;
        color: var(--mut); }}
 .senderline {{ font-family: Helvetica, Arial, sans-serif; font-size: 7.5pt;
        color: var(--mut); text-decoration: underline;
        text-decoration-color: var(--line); margin-bottom: 3mm; }}
 .addrrow {{ display: flex; justify-content: space-between; margin-bottom: 10mm; }}
 .recipient {{ font-size: 11pt; }}
 .meta {{ text-align: right; font-family: Helvetica, Arial, sans-serif;
        font-size: 8.5pt; color: var(--mut); line-height: 1.7; }}
 .subject {{ font-size: 13pt; font-weight: bold; margin: 0 0 5mm; }}
 .subject small {{ font-size: 8.5pt; font-weight: normal; color: var(--mut);
        font-family: Helvetica, Arial, sans-serif; }}
 .keyrow {{ display: flex; gap: 4mm; margin: 5mm 0; }}
 .key {{ flex: 1; border: 1px solid var(--line); border-top: 3px solid var(--sun);
        border-radius: 2mm; padding: 3mm 4mm; text-align: center; }}
 .key b {{ display: block; font-size: 15pt; }}
 .key span {{ font-family: Helvetica, Arial, sans-serif; font-size: 7.5pt;
        color: var(--mut); text-transform: uppercase; letter-spacing: .5px; }}
 figure {{ margin: 5mm 0; text-align: center; }}
 figure img {{ width: 100%; max-width: 105mm; border: 1px solid var(--line);
        border-radius: 2mm; }}
 figcaption {{ font-family: Helvetica, Arial, sans-serif; font-size: 8pt;
        color: var(--mut); margin-top: 1.5mm; }}
 table {{ border-collapse: collapse; width: 100%; margin: 4mm 0; font-size: 10pt; }}
 td {{ padding: 1.8mm 3mm; border-bottom: 1px solid var(--line); }}
 td:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}
 .steps {{ display: flex; gap: 4mm; margin: 5mm 0 6mm; }}
 .step {{ flex: 1; font-size: 9.5pt; }}
 .step b {{ display: inline-flex; width: 6mm; height: 6mm; border-radius: 50%;
        background: var(--sun); color: #fff; align-items: center;
        justify-content: center; font-family: Helvetica, Arial, sans-serif;
        font-size: 9pt; margin-right: 2mm; }}
 .sig {{ margin-top: 8mm; }}
 .sigline {{ margin-top: 12mm; border-top: 1px solid var(--mut);
        width: 60mm; font-family: Helvetica, Arial, sans-serif; font-size: 8pt;
        color: var(--mut); padding-top: 1mm; }}
 .fine {{ font-family: Helvetica, Arial, sans-serif; font-size: 6.8pt;
        color: var(--mut); margin-top: 7mm; border-top: 1px solid var(--line);
        padding-top: 2mm; line-height: 1.55; }}
</style></head><body>

<div class="brandbar">
  <div class="brand">☀ solar·<span>scout</span></div>
  <div class="brandsub">{tx['tagline']} · {SENDER_CONTACT}</div>
</div>

<div class="senderline">{SENDER_LINE}</div>
<div class="addrrow">
  <div class="recipient">{_recipient(e, tx)}</div>
  <div class="meta">
    {tx['object_no']} {e['osm_id']}<br>
    {date_str}<br>
    {tx['page']}
  </div>
</div>

<p class="subject">{tx['subject'].format(kwp=e['proposed_kwp'])}
 <small>{tx['subject_note']}</small></p>

<p>{tx['salutation']}</p>
<p>{tx['intro']}</p>

<div class="keyrow">
  <div class="key"><b>{e['proposed_kwp']:.1f} kWp</b><span>{tx['k_size']}</span></div>
  <div class="key"><b>{e['estimated_annual_kwh']:,.0f} kWh</b><span>{tx['k_kwh']}</span></div>
  <div class="key"><b>≈ {e['annual_benefit_eur']:,.0f} €</b><span>{tx['k_benefit']}</span></div>
  <div class="key"><b>≈ {e['payback_years']:.1f} {tx['yr']}</b><span>{tx['k_payback']}</span></div>
</div>

<figure>
  <img src="data:image/png;base64,{_img_b64(render_img)}" alt="Dachbelegung">
  <figcaption>{tx['figcap'].format(n=e['proposed_panels'])}</figcaption>
</figure>

<table>
 <tr><td>{tx['t_area']}</td><td>{e['proposed_panel_area_m2']:.0f} m²</td></tr>
 <tr><td>{tx['t_orient']}</td><td>{html.escape(e.get('orientation',''))} · {e['specific_yield']:.0f} {tx['t_orient_unit']}</td></tr>
 <tr><td>{tx['t_invest']}</td><td>{e['estimated_cost_eur']:,.0f} €</td></tr>
 <tr><td>{tx['t_co2']}</td><td>≈ {e['co2_t_per_year']:.1f} t</td></tr>
</table>

<p><b>{tx['steps_h']}</b></p>
<div class="steps">
  <div class="step"><b>1</b>{tx['s1']}</div>
  <div class="step"><b>2</b>{tx['s2']}</div>
  <div class="step"><b>3</b>{tx['s3']}</div>
</div>

<p>{tx['closing']}</p>

<div class="sig">
  <p>{tx['regards']}</p>
  <div class="sigline">[Name], [Position] · {SENDER_LINE}</div>
</div>

<div class="fine">
*&nbsp;{html.escape(tx['disclaimer'])}
{tx['assumptions'].format(sc=e['self_consumption_pct'], ep=e['electricity_price_ct'],
                          fi=e['feed_in_ct'], sender=SENDER_LINE)}
</div>
</body></html>"""
    out_path.write_text(doc, encoding="utf-8")
