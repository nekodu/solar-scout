"""Heuristic detection of *existing* solar panels on a roof crop.

Classical CV, no training data: existing panels show up as dark blue-to-black,
strongly rectangular patches inside the roof outline. This is a screening
heuristic (expect some errors on slate/anthracite roofs and roof windows); for
production accuracy swap this module for an ML model (e.g. a DeepSolar-style
segmentation net) or the paid Google Solar API - the rest of the pipeline does
not need to change.
"""

from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np


@dataclass
class DetectionResult:
    has_panels: bool
    coverage: float            # fraction of the roof area covered by detected panels
    panel_boxes: list          # rotated rects of detected patches (px), for debug rendering


def detect_panels(img: np.ndarray, roof_px: List[Tuple[int, int]],
                  resolution: float, min_coverage: float = 0.06) -> DetectionResult:
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [np.array(roof_px, np.int32)], 255)
    roof_area_px = int(np.count_nonzero(mask))
    if roof_area_px == 0:
        return DetectionResult(False, 0.0, [])

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hch, sch, vch = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    bluish = (hch >= 95) & (hch <= 135) & (sch >= 50) & (vch >= 25) & (vch <= 175)
    very_dark = vch < 65

    # If most of the roof is very dark it is the roofing material itself
    # (slate/anthracite tiles), not panels - then only trust the bluish cue.
    dark_frac = np.count_nonzero(very_dark & (mask > 0)) / roof_area_px
    cand = bluish | very_dark if dark_frac < 0.70 else bluish

    cand = (cand.astype(np.uint8) * 255) & mask
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    px_per_m2 = 1.0 / (resolution * resolution)
    min_patch_px = 2.5 * px_per_m2          # one panel is ~2 m²; require >= 2.5 m² patches
    kept_px, boxes = 0, []
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_patch_px:
            continue
        pts = np.column_stack(np.where(labels == i))[:, ::-1].astype(np.float32)
        rect = cv2.minAreaRect(pts)
        (rw, rh) = rect[1]
        if rw < 1 or rh < 1:
            continue
        rectangularity = area / (rw * rh)
        short_side_m = min(rw, rh) * resolution
        if rectangularity >= 0.55 and short_side_m >= 0.8:
            kept_px += int(area)
            boxes.append(rect)

    coverage = kept_px / roof_area_px
    # relative OR absolute: a 30 m² array on a huge hall is far below the
    # coverage threshold but is still an existing installation
    has = coverage >= min_coverage or kept_px * resolution * resolution >= 25.0
    return DetectionResult(has, coverage, boxes)


def unobstructed_indices(img: np.ndarray, cells_px: List[List[Tuple[int, int]]],
                         max_edge_frac: float = 0.14) -> List[int]:
    """Indices of planned panel cells whose roof patch looks clear.

    HVAC, vents, roof terraces, chimneys, skylights and antennas show up as
    dense edges in the orthophoto, while clear roofing (tiles, gravel,
    bitumen) is comparatively smooth. Cells overlapping cluttered patches are
    rejected - the geometric (LoD2) obstruction mask cannot see equipment or
    vegetation, this filter can."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 70, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    keep = []
    for i, pts in enumerate(cells_px):
        mask = np.zeros(gray.shape, np.uint8)
        cv2.fillPoly(mask, [np.array(pts, np.int32)], 255)
        n = int(np.count_nonzero(mask))
        if n == 0:
            continue
        frac = np.count_nonzero(edges & mask) / n
        if frac <= max_edge_frac:
            keep.append(i)
    return keep
