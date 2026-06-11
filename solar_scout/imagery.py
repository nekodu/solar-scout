"""Aerial imagery from open-data WMS services of the German states.

Why these sources (due diligence summary):
- EUMETSAT: weather satellites, pixel size in the hundreds of metres -> cannot see roofs.
- Google Earth / Maps tiles: licence forbids offline/derivative analysis like this.
- BKG nationwide DOP20 WMS: exists, but GetMap is fee-gated (tested: 403 NOACCESS_METHOD).
- State open-data orthophotos (Datenlizenz Deutschland): free, 10-40 cm resolution. NRW
  and Bavaria are bundled below; any other state's WMS can be plugged in via --wms-url.
"""

import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np
import requests


@dataclass(frozen=True)
class Provider:
    key: str
    url: str
    layers: str
    epsg: int
    resolution: float       # native ground resolution in m/pixel
    attribution: str


PROVIDERS = {
    "nrw": Provider(
        key="nrw",
        url="https://www.wms.nrw.de/geobasis/wms_nw_dop",
        layers="nw_dop_rgb",
        epsg=25832,
        resolution=0.10,
        attribution="Geobasis NRW, dl-de/zero-2-0",
    ),
    "bavaria": Provider(
        key="bavaria",
        url="https://geoservices.bayern.de/od/wms/dop/v1/dop40",
        layers="by_dop40c",
        epsg=25832,
        resolution=0.40,
        attribution="Bayerische Vermessungsverwaltung, CC BY 4.0",
    ),
    "berlin": Provider(
        key="berlin",
        url="https://gdi.berlin.de/services/wms/truedop_2024",
        layers="truedop_2024",
        epsg=25832,
        resolution=0.20,
        attribution="Geoportal Berlin / TrueDOP 2024, dl-de/zero-2-0",
    ),
    # The 13 entries below were each verified 2026-06-11 with an anonymous
    # WMS 1.3.0 GetMap (EPSG:25832) returning real orthophoto imagery.
    "brandenburg": Provider("brandenburg", "https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms",
                            "bebb_dop20c", 25832, 0.2, "© GeoBasis-DE/LGB, dl-de/by-2-0"),
    "sachsen": Provider("sachsen", "https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest",
                        "sn_dop_020", 25832, 0.2, "© GeoSN, dl-de/by-2-0"),
    "sachsen-anhalt": Provider("sachsen-anhalt",
                               "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DOP_WMS_OpenData/guest",
                               "lsa_lvermgeo_dop20_2", 25832, 0.2,
                               "© GeoBasis-DE/LVermGeo ST, dl-de/by-2-0"),
    "thueringen": Provider("thueringen", "https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP",
                           "th_dop", 25832, 0.2, "© GDI-Th, dl-de/by-2-0"),
    "mecklenburg-vorpommern": Provider("mecklenburg-vorpommern",
                                       "https://www.geodaten-mv.de/dienste/adv_dop",
                                       "mv_dop", 25832, 0.2, "© GeoBasis-DE/M-V, CC BY 4.0"),
    "hessen": Provider("hessen", "https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows",
                       "he_dop20_rgb", 25832, 0.2, "© HVBG, dl-de/zero-2-0"),
    "hamburg": Provider("hamburg", "https://geodienste.hamburg.de/wms_dop_zeitreihe_unbelaubt",
                        "dop_zeitreihe_unbelaubt", 25832, 0.2,
                        "© Freie und Hansestadt Hamburg, LGV, dl-de/by-2-0"),
    "schleswig-holstein": Provider("schleswig-holstein", "https://service.gdi-sh.de/WMS_SH_DOP20col_OpenGBD",
                                   "sh_dop20_rgb", 25832, 0.2,
                                   "© GeoBasis-DE/LVermGeo SH, CC BY 4.0"),
    "niedersachsen": Provider("niedersachsen", "https://opendata.lgln.niedersachsen.de/doorman/noauth/dop_wms",
                              "ni_dop20", 25832, 0.2, "© LGLN, CC BY 4.0"),
    # year-stamped endpoint/layer - update after the next flight year;
    # Bremerhaven is the separate layer DOP20_2023_BHV
    "bremen": Provider("bremen", "https://geodienste.bremen.de/wms_dop20_2023",
                       "DOP20_2023_HB", 25832, 0.2,
                       "© Landesamt GeoInformation Bremen, CC BY 4.0"),
    "rheinland-pfalz": Provider("rheinland-pfalz", "https://geo4.service24.rlp.de/wms/rp_dop20.fcgi",
                                "rp_dop20", 25832, 0.2, "© GeoBasis-DE/LVermGeoRP, dl-de/by-2-0"),
    "saarland": Provider("saarland", "https://geoportal.saarland.de/freewms/dop",
                         "sl_dop", 25832, 0.2, "© GeoBasis DE/LVGL-SL, dl-de/by-2-0"),
    "baden-wuerttemberg": Provider("baden-wuerttemberg",
                                   "https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C",
                                   "IMAGES_DOP_20_RGB", 25832, 0.2,
                                   "© LGL-BW, dl-de/by-2-0, www.lgl-bw.de"),
}

# keyed by ISO3166-2 code; works for city-states where Nominatim has no "state"
STATE_TO_PROVIDER = {
    "DE-NW": "nrw",
    "DE-BY": "bavaria",
    "DE-BE": "berlin",
    "DE-BB": "brandenburg",
    "DE-SN": "sachsen",
    "DE-ST": "sachsen-anhalt",
    "DE-TH": "thueringen",
    "DE-MV": "mecklenburg-vorpommern",
    "DE-HE": "hessen",
    "DE-HH": "hamburg",
    "DE-SH": "schleswig-holstein",
    "DE-NI": "niedersachsen",
    "DE-HB": "bremen",
    "DE-RP": "rheinland-pfalz",
    "DE-SL": "saarland",
    "DE-BW": "baden-wuerttemberg",
}


@dataclass
class GeoImage:
    """Image plus the affine info needed to map projected coords -> pixels."""
    img: np.ndarray            # BGR
    minx: float
    maxy: float
    resolution: float

    def to_px(self, x: float, y: float) -> Tuple[int, int]:
        return (
            int(round((x - self.minx) / self.resolution)),
            int(round((self.maxy - y) / self.resolution)),
        )


def provider_for_place(place) -> Optional[Provider]:
    key = STATE_TO_PROVIDER.get(getattr(place, "iso", None) or "")
    return PROVIDERS.get(key) if key else None


def fetch_geoimage(provider: Provider, bounds: Tuple[float, float, float, float],
                   margin_m: float = 4.0, max_px: int = 1600) -> GeoImage:
    """GetMap for a building's bounding box (projected coords) plus a margin."""
    minx, miny, maxx, maxy = bounds
    minx -= margin_m; miny -= margin_m; maxx += margin_m; maxy += margin_m
    res = provider.resolution
    width = int((maxx - minx) / res)
    height = int((maxy - miny) / res)
    scale = max(width, height) / max_px
    if scale > 1.0:
        res *= scale
        width = int((maxx - minx) / res)
        height = int((maxy - miny) / res)
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
        "LAYERS": provider.layers, "STYLES": "",
        "CRS": f"EPSG:{provider.epsg}",
        "BBOX": f"{minx},{miny},{maxx},{maxy}",
        "WIDTH": width, "HEIGHT": height,
        "FORMAT": "image/png",
    }
    raw = _get_with_retry(provider.url, params)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"WMS {provider.key} returned a non-image for bbox {bounds}")
    return GeoImage(img=img, minx=minx, maxy=maxy, resolution=res)


def _get_with_retry(url: str, params: dict, attempts: int = 3) -> bytes:
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("image/"):
                return r.content
            raise RuntimeError(f"WMS error response: {r.text[:300]}")
        except (requests.RequestException, RuntimeError) as exc:
            last = exc
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"WMS request failed after {attempts} attempts: {last}")
