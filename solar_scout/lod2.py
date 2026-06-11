"""Measured roof geometry from the states' open CityGML LoD2 building models.

Every German state surveys real 3D roof shapes (LiDAR): roof form, ridge and
eaves heights, per-plane tilt and azimuth. Five states are wired up with
verified, keyless per-tile downloads (Brandenburg, Berlin, NRW, Bayern,
Niedersachsen); tiles are cached locally, so repeat lookups in the same km²
are instant. Where no LoD2 source is wired up (or the building is missing),
the caller keeps the procedural OSM-tag/heuristic roof model.

AdV roofType codes: 1000 flat, 2100 shed, 3100 gabled, 3200 hipped,
3300 half-hipped, 3400 mansard, 3500 tent/pyramid.
"""

import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests
from pyproj import Transformer
from shapely.geometry import Point, Polygon

CACHE = Path.home() / ".cache" / "solar_scout" / "lod2"
TIMEOUT = (10, 120)
USER_AGENT = "solar-scout/0.1 (open-data LoD2 lookup)"

ROOF_LABELS = {
    "1000": "Flachdach", "2100": "Pultdach", "3100": "Satteldach",
    "3200": "Walmdach", "3300": "Krüppelwalmdach", "3400": "Mansarddach",
    "3500": "Zeltdach",
}


@dataclass
class MeasuredRoof:
    label: str            # e.g. "Satteldach"
    is_flat: bool
    pitch: float          # tilt of the chosen roof plane, deg
    aspect: float         # PVGIS aspect of the chosen plane (0=S, -90=E)
    eaves_height: float   # m above ground
    plane_area: float     # m² of the chosen plane
    source: str           # attribution
    epsg: int = 25832     # CRS of the rings below
    ground_z: float = 0.0 # absolute terrain elevation (DHHN)
    n_planes: int = 0     # number of roof planes (complex roofscape = residential)
    best_ring: Optional[list] = None   # 3D outer ring of the chosen plane
    surfaces: Optional[list] = None    # [(kind, ring3d)], kind roof|wall|ground
    obstructions: Optional[list] = None  # rings of structures ABOVE the plane
                                         # (penthouses etc.) - unusable area


def _tile_url(iso: str, e: float, n: float) -> Optional[Tuple[str, str]]:
    """(url, kind) for the LoD2 tile containing projected coords; kind gml|zip."""
    ek, nk = int(e // 1000), int(n // 1000)
    if iso == "DE-BB":
        return (f"https://data.geobasis-bb.de/geobasis/daten/3d_gebaeude/"
                f"lod2_gml/lod2_33{ek:03d}-{nk}.zip", "zip")
    if iso == "DE-BE":
        return (f"https://gdi.berlin.de/data/a_lod2/atom/LoD2_{ek}_{nk}.zip", "zip")
    if iso == "DE-NW":
        return (f"https://www.opengeodata.nrw.de/produkte/geobasis/3dg/lod2_gml/"
                f"lod2_gml/LoD2_32_{ek}_{nk}_1_NW.gml", "gml")
    if iso == "DE-BY":
        return (f"https://download1.bayernwolke.de/a/lod2/citygml/"
                f"{ek // 2 * 2}_{nk // 2 * 2}.gml", "gml")
    return None


def _stac_url_ni(lon: float, lat: float) -> Optional[Tuple[str, str]]:
    r = requests.get("https://lod.stac.lgln.niedersachsen.de/search",
                     params={"collections": "lod2",
                             "bbox": f"{lon-.001},{lat-.001},{lon+.001},{lat+.001}"},
                     headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    for item in r.json().get("features", []):
        for a in item.get("assets", {}).values():
            href = a.get("href", "")
            if href.endswith(".gml"):
                return href, "gml"
    return None


# tile CRS per state (UTM zone 33 in the east, 32 elsewhere)
_ZONE33 = {"DE-BB", "DE-BE"}
SUPPORTED = {"DE-BB", "DE-BE", "DE-NW", "DE-BY", "DE-NI"}


def _fetch_tile(iso: str, lon: float, lat: float) -> Optional[Path]:
    epsg = 25833 if iso in _ZONE33 else 25832
    e, n = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    loc = _stac_url_ni(lon, lat) if iso == "DE-NI" else _tile_url(iso, e, n)
    if not loc:
        return None
    url, kind = loc
    CACHE.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1].replace(".zip", "")
    out = CACHE / (name if name.endswith(".gml") else name + ".gml")
    if out.is_file():
        return out
    r = requests.get(url, headers={"User-Agent": USER_AGENT},
                     timeout=TIMEOUT, stream=True)
    if r.status_code != 200:
        return None
    raw = out.with_suffix(".part")
    with raw.open("wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    if kind == "zip":
        with zipfile.ZipFile(raw) as z:
            member = next((m for m in z.namelist()
                           if m.lower().endswith((".gml", ".xml"))), None)
            if not member:
                raw.unlink()
                return None
            out.write_bytes(z.read(member))
        raw.unlink()
    else:
        raw.rename(out)
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _rings(building_el):
    """(roof rings, wall rings, ground rings, roofType) under one Building."""
    out = {"RoofSurface": [], "WallSurface": [], "GroundSurface": []}
    rtype = None
    for el in building_el.iter():
        name = _local(el.tag)
        if name in out:
            for pl in el.iter():
                if _local(pl.tag) == "posList" and pl.text:
                    vals = [float(v) for v in pl.text.split()]
                    out[name].append([(vals[i], vals[i + 1], vals[i + 2])
                                      for i in range(0, len(vals) - 2, 3)])
        elif name == "roofType" and rtype is None:
            rtype = (el.text or "").strip()
    return out["RoofSurface"], out["WallSurface"], out["GroundSurface"], rtype


def _newell(ring) -> Tuple[float, float, float, float]:
    """(nx, ny, nz, area) of a 3D polygon ring via Newell's method."""
    nx = ny = nz = 0.0
    for (x1, y1, z1), (x2, y2, z2) in zip(ring, ring[1:] + ring[:1]):
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    norm = math.hypot(nx, math.hypot(ny, nz))
    return (nx / norm, ny / norm, nz / norm, norm / 2.0) if norm > 1e-9 else (0, 0, 1, 0)


def measure(iso: Optional[str], lon: float, lat: float) -> Optional[MeasuredRoof]:
    """Measured roof of the building at lon/lat, or None (caller falls back)."""
    if iso not in SUPPORTED:
        return None
    try:
        tile = _fetch_tile(iso, lon, lat)
        if tile is None:
            return None
        epsg = 25833 if iso in _ZONE33 else 25832
        x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
        pt = Point(x, y)

        match = None
        for _, el in ET.iterparse(str(tile)):
            if _local(el.tag) != "Building":
                continue
            roof, wall, ground, rtype = _rings(el)
            rings2d = [r for r in (ground or roof)]
            if rings2d:
                xs = [p[0] for r in rings2d for p in r]
                ys = [p[1] for r in rings2d for p in r]
                if min(xs) - 2 <= x <= max(xs) + 2 and min(ys) - 2 <= y <= max(ys) + 2:
                    if any(Polygon([(p[0], p[1]) for p in r]).buffer(0.5).contains(pt)
                           for r in rings2d if len(r) >= 3):
                        match = (roof, wall, ground, rtype)
                        el.clear()
                        break
            el.clear()
        if not match or not match[0]:
            return None
        roof, wall, ground, rtype = match

        ground_z = min((p[2] for r in (ground or roof) for p in r))
        surfaces = ([("roof", r) for r in roof] + [("wall", r) for r in wall]
                    + [("ground", r) for r in ground])
        planes = []
        for ring in roof:
            nx, ny, nz, area = _newell(ring)
            if nz < 0:
                nx, ny, nz = -nx, -ny, -nz
            tilt = math.degrees(math.acos(max(-1.0, min(1.0, nz))))
            aspect = ((-math.degrees(math.atan2(nx, -ny)) + 180) % 360) - 180
            planes.append({"tilt": tilt, "aspect": aspect, "area": area,
                           "min_z": min(p[2] for p in ring), "ring": ring})

        epsg = 25833 if iso in _ZONE33 else 25832
        sloped = [p for p in planes if p["tilt"] >= 10.0]
        flat = [p for p in planes if p["tilt"] < 10.0]
        label = ROOF_LABELS.get(rtype or "", "Dach")
        src = f"LoD2 {iso}"
        # decide flat vs pitched by DOMINANT AREA (and the surveyed roof-type
        # code), never by the mere existence of a sloped plane - office blocks
        # have small pitched penthouse roofs on a flat main roof
        flat_area = sum(p["area"] for p in flat)
        sloped_area = sum(p["area"] for p in sloped)
        if not sloped or rtype == "1000" or flat_area >= sloped_area:
            ref = max(flat or planes, key=lambda p: p["area"])
            eaves = ref["min_z"] - ground_z
            # anything whose footprint rises above the main slab obstructs it:
            # penthouses, mechanical rooms, higher wings
            obstr = [p["ring"] for p in planes
                     if p is not ref and p["min_z"] > ref["min_z"] + 0.8]
            return MeasuredRoof("Flachdach", True, 0.0, 0.0, max(eaves, 2.5),
                                flat_area or ref["area"], src, epsg, ground_z,
                                len(planes), ref["ring"], surfaces, obstr)
        # pitched: dormers etc. above the chosen plane are subtracted as well
        southish = [p for p in sloped if abs(p["aspect"]) <= 90.0]
        best = max(southish or sloped, key=lambda p: p["area"])
        eaves = min(p["min_z"] for p in sloped) - ground_z
        if label == "Dach":
            label = "Steildach"
        obstr = [p["ring"] for p in planes
                 if p is not best and p["min_z"] > best["min_z"] + 0.3
                 and p["area"] < best["area"]]
        return MeasuredRoof(label, False, round(best["tilt"], 1),
                            round(best["aspect"], 1), max(eaves, 2.5),
                            round(best["area"], 1), src, epsg, ground_z,
                            len(planes), best["ring"], surfaces, obstr)
    except Exception:
        return None
