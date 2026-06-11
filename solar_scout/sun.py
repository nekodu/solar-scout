"""Per-address sun exposure and PV yield via PVGIS (EU Joint Research Centre).

Due diligence note: "use ML to calculate sun exposure" is better served by
physics. PVGIS models the yield from 18+ years of satellite-derived irradiance
(SARAH-3), ERA5 weather and a DEM-computed terrain horizon (hill/valley
shading) for the exact coordinate - free, no API key, and validated by the JRC.
A self-trained ML model could not beat that with the data we have. What ML *is*
good for here is panel/roof detection (see ml_detect.py).
"""

import math
from typing import Optional

import requests
from shapely.geometry import Polygon

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
_cache: dict = {}


def roof_aspect_deg(roof: Polygon, dominant_angle_deg: float) -> float:
    """PVGIS aspect (0 = south, -90 = east, 90 = west) of the better roof half.

    The panel rows run along the roof's long axis, so modules face one of the
    two perpendiculars; assume the owner uses the more southern-facing side.
    """
    for cand in (dominant_angle_deg + 90.0, dominant_angle_deg - 90.0):
        # geometry angle (CCW from east) -> compass azimuth from south
        compass = (90.0 - cand) % 360.0          # 0=N, 90=E, 180=S, 270=W
        aspect = ((compass - 180.0 + 180.0) % 360.0) - 180.0
        if abs(aspect) <= 90.0:
            return round(aspect, 1)
    return 0.0


def pv_yield(lat: float, lon: float, kwp: float, tilt: float, aspect: float,
             loss_pct: float = 14.0, timeout: int = 30) -> Optional[dict]:
    """Annual energy (kWh) and specific yield (kWh/kWp) for this exact location.

    Returns None on network failure so the caller can fall back to a flat
    Germany-average specific yield. PVGIS yield scales linearly with kWp, so
    results are cached per (location, orientation) and rescaled.
    """
    key = (round(lat, 3), round(lon, 3), round(tilt), round(aspect / 10) * 10)
    if key in _cache:
        per_kwp = _cache[key]
    else:
        params = {
            "lat": f"{lat:.4f}", "lon": f"{lon:.4f}",
            "peakpower": 1.0, "loss": loss_pct,
            "angle": tilt, "aspect": aspect,
            "usehorizon": 1, "outputformat": "json",
        }
        try:
            r = requests.get(PVGIS_URL, params=params, timeout=timeout)
            r.raise_for_status()
            totals = r.json()["outputs"]["totals"]["fixed"]
        except (requests.RequestException, KeyError, ValueError):
            return None
        per_kwp = {
            "specific_yield": totals["E_y"],          # kWh/kWp/a for peakpower=1
            "irradiation": totals.get("H(i)_y"),      # kWh/m²/a in module plane
        }
        _cache[key] = per_kwp
    return {
        "annual_kwh": round(per_kwp["specific_yield"] * kwp, -1),
        "specific_yield": round(per_kwp["specific_yield"]),
        "irradiation": per_kwp["irradiation"],
        "aspect": aspect,
        "tilt": tilt,
    }


def compass_label(aspect: float) -> str:
    """Human label for a PVGIS aspect, e.g. -45 -> 'Südost'."""
    names = ["Süd", "Südwest", "West", "Nordwest", "Nord",
             "Nordost", "Ost", "Südost"]
    idx = int(((aspect % 360) + 22.5) // 45) % 8
    return names[idx]
