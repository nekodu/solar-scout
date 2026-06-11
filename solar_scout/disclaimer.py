"""Central disclaimer texts.

Legal background (Germany, not legal advice): a yield forecast that is not
clearly framed as a non-binding estimate can be construed as a warranted
characteristic (zugesicherte Eigenschaft) - case law treats shortfalls of
~10 % vs. a warranted yield as a defect, and pre-contractual advice must
disclose general and system-specific risks. Advertising the 0 % VAT rate
without naming its conditions has been ruled anti-competitive (OLG Frankfurt).
Hence: every artifact carries the estimate framing, the error sources, and
the qualified VAT note. Do not weaken these texts in customer-facing output.
"""

BADGE_DE = "Unverbindliche Schätzung"

SHORT_DE = (
    "Alle Werte sind unverbindliche, automatisiert erstellte Schätzwerte — "
    "keine zugesicherten Eigenschaften. Tatsächliche Belegung, Erträge und "
    "Kosten können nach Vor-Ort-Prüfung deutlich abweichen (auch ±20 % und mehr)."
)

SOURCES_DE = (
    "Berechnungsgrundlage: amtliche Luftbilder und 3D-Gebäudedaten (LoD2) der "
    "Länder, Gebäudeumrisse aus OpenStreetMap, Einstrahlung nach PVGIS "
    "(EU-Kommission, langjährige Satellitenmittel). Bekannte Fehlerquellen: "
    "Luftbilder/3D-Daten können 1–3 Jahre alt sein; die automatische Erkennung "
    "vorhandener Module, Aufbauten und Hindernisse kann fehlschlagen (sowohl "
    "übersehen als auch fälschlich erkennen); Verschattung durch Bäume und "
    "Nachbargebäude ist nicht modelliert; Dachstatik, Elektrik und "
    "Denkmalschutz sind nicht geprüft; Modulpreise und EEG-Vergütung ändern "
    "sich laufend. Preisangaben sind Marktdurchschnitte; 0 % USt. gilt nur "
    "unter den Voraussetzungen des § 12 Abs. 3 UStG (u. a. Lieferung an den "
    "Betreiber, Wohngebäude). Verbindliche Aussagen erst nach Vor-Ort-Prüfung "
    "und individuellem Angebot."
)

LETTER_DE = SHORT_DE + " " + SOURCES_DE

SHORT_EN = (
    "All figures are non-binding, automatically generated estimates, not "
    "warranted characteristics. Actual layout, yields and costs can differ "
    "considerably after an on-site survey (by 20 percent and more)."
)

SOURCES_EN = (
    "Basis of calculation: official aerial imagery and 3D building data "
    "(LoD2) of the German states, building footprints from OpenStreetMap, "
    "irradiance from PVGIS (European Commission, long-term satellite "
    "averages). Known error sources: imagery and 3D data can be 1 to 3 years "
    "old; automatic detection of existing modules, superstructures and "
    "obstacles can fail in both directions; shading from trees and "
    "neighbouring buildings is not modelled; structural, electrical and "
    "heritage aspects are not checked; module prices and feed-in tariffs "
    "change continuously. Prices are market averages; the German 0% VAT rate "
    "applies only under the conditions of sec. 12 (3) German VAT Act (UStG). "
    "Binding statements require an on-site survey and an individual quote."
)

LETTER_EN = SHORT_EN + " " + SOURCES_EN

README_EN = (
    "All outputs are non-binding automated estimates, not warranted "
    "characteristics: the engine can miss or falsely detect existing modules "
    "and obstructions, imagery/3D data may be years old, tree/neighbour "
    "shading is not modelled, statics/electrics/heritage rules are unchecked, "
    "and prices/tariffs move. Real-world deviations of ±20 % and more are "
    "possible; binding figures require an on-site assessment."
)
