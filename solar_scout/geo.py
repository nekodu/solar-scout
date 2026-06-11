"""Geocoding of the user's filters (city / district / postcode / address) via Nominatim."""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "solar-scout/0.1 (open-data solar roof screening; contact: local user)"


@dataclass
class Place:
    display_name: str
    osm_type: str          # node | way | relation
    osm_id: int
    lat: float
    lon: float
    bbox: tuple            # (south, west, north, east)
    state: Optional[str]   # Bundesland (display); absent for city-states
    iso: Optional[str]     # ISO3166-2 code, e.g. DE-BE - used to pick imagery
    category: str = ""     # nominatim category, "building" for exact addresses
    has_housenumber: bool = False
    postcode: str = ""
    city: str = ""
    suburb: str = ""

    @property
    def locality(self) -> dict:
        return {"postcode": self.postcode, "city": self.city, "suburb": self.suburb}

    @property
    def is_address(self) -> bool:
        """True when the result pinpoints one building, not a street or area."""
        return self.category == "building" or (
            self.has_housenumber and self.osm_type == "node")

    @property
    def overpass_area_id(self) -> Optional[int]:
        if self.osm_type == "relation":
            return 3600000000 + self.osm_id
        if self.osm_type == "way":
            return 2400000000 + self.osm_id
        return None


def geocode(query: Optional[str] = None, city: Optional[str] = None,
            district: Optional[str] = None, postcode: Optional[str] = None) -> Place:
    """Resolve the location filters to a single OSM place.

    Free-form `query` wins; otherwise the parts are combined into one query so
    combinations like district+city or postcode alone all work.
    """
    if not query:
        parts = [p for p in (district, city, postcode) if p]
        if not parts:
            raise ValueError("need at least one of: --query, --city, --district, --postcode")
        query = ", ".join(parts)
    params = {
        "q": f"{query}, Deutschland",
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 5,
        "countrycodes": "de",
    }
    r = requests.get(NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    results = r.json()
    if not results:
        raise ValueError(f"Nominatim found nothing for {query!r}")
    # An exact-address hit (category=building / place_rank 30) always wins;
    # otherwise prefer boundary relations (cities, suburbs, postal code areas).
    results.sort(key=lambda x: (0 if x.get("category") == "building" else 1,
                                0 if x.get("osm_type") == "relation" else 1))
    best = results[0]
    s, n, w, e = (float(v) for v in best["boundingbox"])
    addr = best.get("address", {})
    return Place(
        display_name=best["display_name"],
        osm_type=best["osm_type"],
        osm_id=int(best["osm_id"]),
        lat=float(best["lat"]),
        lon=float(best["lon"]),
        bbox=(s, w, n, e),
        state=addr.get("state") or addr.get("city"),
        iso=addr.get("ISO3166-2-lvl4"),
        category=best.get("category", ""),
        has_housenumber=bool(addr.get("house_number")),
        postcode=addr.get("postcode", ""),
        city=addr.get("city") or addr.get("town") or addr.get("village", ""),
        suburb=addr.get("suburb") or addr.get("borough")
               or addr.get("city_district", ""),
    )


def geocode_address(address: str) -> Tuple[Place, Optional[str]]:
    """Resolve one exact street address to a building-level Place.

    Nominatim silently falls back to the *street* when the house number is not
    in OSM - that must never be analysed as a roof. We try spelling variants
    ('Str.' -> 'Straße') and finally the base number ('15A' -> '15'); the
    second return value is a note when a fallback variant was used.
    """
    variants = [(address, None)]
    # district decorations confuse Nominatim ("Berlin-Bezirk Pankow", "(Pankow)");
    # German postcodes are unique nationwide, so "street number, postcode" suffices
    m = re.match(r"^(.*?\d+\s*[a-zA-Z]?)\s*,.*?(\d{5})", address)
    if m:
        variants.append((f"{m.group(1)}, {m.group(2)}", None))
    for v, _ in list(variants):
        expanded = re.sub(r"(?i)\bstr\.\s*", "Straße ", v)
        expanded = re.sub(r"(?i)(\w)str\.", r"\1straße", expanded)
        if expanded != v:
            variants.append((expanded, None))
    for v, _ in list(variants):
        base = re.sub(r"(\d+)\s*[a-zA-Z]\b", r"\1", v)
        if base != v:
            variants.append((base, f"Hausnummer nicht in OpenStreetMap erfasst — "
                                   f"nächstgelegene erfasste Adresse verwendet."))
    for query, note in variants:
        try:
            place = geocode(query=query)
        except ValueError:
            continue
        if place.is_address:
            return place, note
    raise ValueError(
        f"Adresse {address!r} nicht gefunden: Straße oder Hausnummer ist nicht "
        f"(oder anders) in OpenStreetMap erfasst.")
