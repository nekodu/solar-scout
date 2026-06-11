"""Roof shape inference.

Most German residential roofs are pitched (Satteldach & friends), not flat -
treating them as flat misplaces panels, understates usable area (a pitched
plane is footprint/cos(pitch) large) and looks wrong in 3D. Order of truth:

1. OSM tags (roof:shape, roof:angle) when mapped.
2. Heuristic: industrial/commercial kinds and very large footprints -> flat;
   everything residential -> gabled at the German-typical ~38 deg.

Hipped/mansard/pyramidal etc. are approximated as gabled in v1; the upgrade
path to measured shapes is the states' open CityGML LoD2 data.
"""

from dataclasses import dataclass

from .buildings import BUSINESS_BUILDING_VALUES, Building

FLAT = "flat"
GABLED = "gabled"

# OSM roof:shape values that mean "effectively flat"
_FLAT_SHAPES = {"flat", "skillion"}
# kinds whose roofs are typically flat when untagged
_FLAT_KINDS = BUSINESS_BUILDING_VALUES | {"garage", "garages", "carport", "shed"}

DEFAULT_PITCH = 38.0          # typical German Satteldach
FLAT_RACK_TILT = 15.0         # racked rows on a flat roof
LEVEL_HEIGHT = 3.2            # m per building level


@dataclass
class RoofSpec:
    shape: str                # FLAT | GABLED
    pitch: float              # roof plane tilt in deg (module tilt on flat racks)
    eaves_height: float       # m, wall height to the eaves
    label: str                # German label for reports ("Satteldach", "Flachdach")

    @property
    def is_flat(self) -> bool:
        return self.shape == FLAT


def infer_roof(b: Building, footprint_m2: float) -> RoofSpec:
    tags = b.tags
    levels = float(tags.get("building:levels", 0) or 0)

    shape_tag = tags.get("roof:shape", "").lower()
    if shape_tag in _FLAT_SHAPES:
        shape = FLAT
    elif shape_tag:                                   # gabled, hipped, mansard, ...
        shape = GABLED
    elif b.kind in _FLAT_KINDS or footprint_m2 > 600:
        shape = FLAT
    else:
        shape = GABLED

    if shape == FLAT:
        pitch = FLAT_RACK_TILT
        eaves = LEVEL_HEIGHT * (levels or 3)
        label = "Flachdach"
    else:
        try:
            pitch = float(tags.get("roof:angle", DEFAULT_PITCH))
        except ValueError:
            pitch = DEFAULT_PITCH
        pitch = min(max(pitch, 15.0), 65.0)
        eaves = LEVEL_HEIGHT * (levels or 2)
        label = {"hipped": "Walmdach", "half-hipped": "Krüppelwalmdach",
                 "mansard": "Mansarddach", "gambrel": "Mansarddach"}.get(
                     shape_tag, "Satteldach")
    return RoofSpec(shape=shape, pitch=pitch, eaves_height=eaves, label=label)
