"""Building footprints from OpenStreetMap via the Overpass API."""

import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests
from shapely.geometry import Polygon

from .geo import Place, USER_AGENT

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Building types that can never carry a rooftop PV proposal.
EXCLUDED_BUILDING_VALUES = {
    "ruins", "construction", "greenhouse", "tent", "collapsed", "no",
    "container", "tower", "water_tower", "chimney",
}

# building=* values that indicate a commercial/industrial roof (B2B targets).
BUSINESS_BUILDING_VALUES = {
    "industrial", "commercial", "retail", "warehouse", "office", "supermarket",
    "factory", "manufacture", "service", "hangar", "depot", "logistics",
}

# explicitly residential building=* values - never reclassified as business
RESIDENTIAL_BUILDING_VALUES = {
    "house", "detached", "semidetached_house", "terrace", "apartments",
    "residential", "bungalow", "dormitory", "hut",
}


def classify_segment(b: "Building", footprint_m2: float,
                     measured=None, touching: int = 0) -> str:
    """b2b or residential - by tags first, then by the roof itself.

    A big single flat roof standing alone is a business hall. But a block of
    compact attached apartments can have the same footprint area - those are
    betrayed by residential tags, by many touching neighbour buildings
    (terraced rows), or by a complex multi-plane LoD2 roofscape.
    """
    if b.is_business:
        return "b2b"
    if b.kind in RESIDENTIAL_BUILDING_VALUES:
        return "residential"
    if measured is not None and measured.n_planes > 6:
        return "residential"            # many roof planes = grown roofscape
    if touching >= 2:
        return "residential"            # attached row - not one big hall
    flat = measured.is_flat if measured is not None else None
    if footprint_m2 > 800 and flat is not False:
        return "b2b"                    # large, alone, flat(ish): business hall
    return "residential"


@dataclass
class Building:
    osm_id: int
    polygon: Polygon            # WGS84, (lon, lat)
    tags: dict = field(default_factory=dict)

    @property
    def address(self) -> str:
        t = self.tags
        street = " ".join(p for p in (t.get("addr:street"), t.get("addr:housenumber")) if p)
        bits = [p for p in (t.get("name"), street, t.get("addr:postcode"), t.get("addr:city")) if p]
        return ", ".join(bits) or f"OSM way {self.osm_id}"

    @property
    def kind(self) -> str:
        return self.tags.get("building", "yes")

    @property
    def is_business(self) -> bool:
        t = self.tags
        return (t.get("building") in BUSINESS_BUILDING_VALUES
                or any(k in t for k in ("shop", "office", "craft", "industrial")))

    @property
    def business_name(self) -> str:
        return self.tags.get("name") or self.tags.get("operator") or ""

    @property
    def contact(self) -> dict:
        """Publicly OSM-tagged contact data (mostly present on business buildings)."""
        t = self.tags
        return {
            "email": t.get("contact:email") or t.get("email") or "",
            "phone": t.get("contact:phone") or t.get("phone") or "",
            "website": t.get("contact:website") or t.get("website") or "",
        }


def _query(place: Place, radius_m: int, max_elements: int) -> str:
    area_id = place.overpass_area_id
    if area_id is not None:
        scope_def = f"area({area_id})->.a;"
        scope = "(area.a)"
    else:
        scope_def = ""
        scope = f"(around:{radius_m},{place.lat},{place.lon})"
    return (
        f"[out:json][timeout:180];{scope_def}"
        f'way["building"]{scope};'
        f"out tags geom {max_elements};"
    )


def _parse_ways(data: dict, skip_excluded: bool = True) -> List[Building]:
    out: List[Building] = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        if skip_excluded and tags.get("building", "yes") in EXCLUDED_BUILDING_VALUES:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
        if len(coords) < 4:
            continue
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        out.append(Building(osm_id=el["id"], polygon=poly, tags=tags))
    return out


def fetch_buildings(place: Place, radius_m: int = 250,
                    max_elements: int = 4000) -> List[Building]:
    return _parse_ways(_post_with_retry(_query(place, radius_m, max_elements)))


def fetch_building_at(place: Place) -> Building:
    """The one building a geocoded street address points at.

    Nominatim usually resolves a house number straight to the building way;
    otherwise (address node, entrance) take the surrounding building that
    contains - or is nearest to - the geocoded point.
    """
    from shapely.geometry import Point

    if not place.is_address:
        raise ValueError(f"{place.display_name!r} is not a building-level address")
    data = _post_with_retry(
        f'[out:json][timeout:60];(way["building"]'
        f"(around:40,{place.lat},{place.lon});way({place.osm_id}););out tags geom;"
        if place.osm_type == "way" else
        f'[out:json][timeout:60];way["building"]'
        f"(around:40,{place.lat},{place.lon});out tags geom;")
    found = _parse_ways(data)
    if not found:
        raise ValueError(f"no OSM building found at {place.display_name!r}")
    pt = Point(place.lon, place.lat)
    # complexes are mapped as overlapping ways - prefer the LARGEST building
    # containing the address point so we always analyse the full structure
    inside = [b for b in found if b.polygon.buffer(1e-7).contains(pt)]
    if inside:
        return max(inside, key=lambda b: b.polygon.area)
    return min(found, key=lambda b: b.polygon.distance(pt))


def _post_with_retry(query: str, attempts: int = 3) -> dict:
    last_exc: Optional[Exception] = None
    for i in range(attempts):
        try:
            r = requests.post(OVERPASS_URL, data={"data": query}, timeout=200,
                              headers={"User-Agent": USER_AGENT})
            if r.status_code in (429, 504):
                time.sleep(8 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"Overpass API failed after {attempts} attempts: {last_exc}")
