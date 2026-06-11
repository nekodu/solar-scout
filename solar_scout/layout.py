"""Fit a panel grid onto the roof and render it onto the imagery.

Flat roofs get south-facing racked rows over the whole inset footprint.
Pitched roofs get panels on the *south-facing roof plane only*: the footprint
half on the sunny side of the ridge, stretched by 1/cos(pitch) because the
sloped plane is larger than its ground projection, then planned, then
projected back for rendering and georeferencing.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np
from shapely import affinity
from shapely.geometry import Polygon, box

from .imagery import GeoImage
from .roofs import RoofSpec

PANEL_W = 1.134
PANEL_L = 1.722
GAP = 0.05
EDGE_MARGIN = 0.6


@dataclass
class RoofPlan:
    shape: str
    tilt: float                       # module tilt (= roof pitch when gabled)
    aspect: float                     # PVGIS aspect of the module plane
    ridge_angle: float                # deg, CCW from east (geometry convention)
    panels_ground: List[Polygon] = field(default_factory=list)   # horizontal projection
    eaves_origin: Optional[Tuple[float, float]] = None  # point on the eaves line
    updir: Optional[Tuple[float, float]] = None         # ground unit vector eaves->ridge
    panels3d: Optional[list] = None   # 4 corners (x,y,z_abs) per panel, on the
                                      # measured LoD2 plane - single source of
                                      # truth shared by photo render and 3D

    @property
    def n_panels(self) -> int:
        return len(self.panels_ground)


def _vsub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _vdot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _vcross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                           a[0] * b[1] - a[1] * b[0])
def _vnorm(a):
    l = math.sqrt(_vdot(a, a))
    return (a[0] / l, a[1] / l, a[2] / l) if l > 1e-12 else (0.0, 0.0, 1.0)


def plan_on_plane(ring3d: list, target: int, margin: float = 0.5,
                  lift: float = 0.12, obstructions: list = None) -> Optional[RoofPlan]:
    """Panels on a measured (LoD2) roof plane given by its 3D outer ring.

    The plane's own coordinates are used for the grid (true sizes, no cos
    distortion); the returned quads carry exact 3D corners and their ground
    projection, so every artifact renders the identical layout.
    """
    ring = ring3d[:-1] if ring3d[0] == ring3d[-1] else ring3d
    if len(ring) < 3 or target <= 0:
        return None
    n = (0.0, 0.0, 0.0)
    for a, b in zip(ring, ring[1:] + ring[:1]):
        n = (n[0] + (a[1] - b[1]) * (a[2] + b[2]),
             n[1] + (a[2] - b[2]) * (a[0] + b[0]),
             n[2] + (a[0] - b[0]) * (a[1] + b[1]))
    n = _vnorm(n)
    if n[2] < 0:
        n = (-n[0], -n[1], -n[2])
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, n[2]))))
    if tilt < 8.0 or tilt > 75.0:
        return None
    u = _vnorm(_vcross((0.0, 0.0, 1.0), n))      # horizontal strike (eaves dir)
    v = _vcross(n, u)                            # upslope within the plane

    origin = min(ring, key=lambda p: p[2])
    pts2d = [(_vdot(_vsub(p, origin), u), _vdot(_vsub(p, origin), v)) for p in ring]
    poly = Polygon(pts2d)
    if not poly.is_valid:
        poly = poly.buffer(0)
    # dormers/chimneys obstruct only where their GROUND footprint overlaps the
    # plane's footprint; map that overlap into plane coords with the affine
    # transform of on-plane points (avoids folding geometry from behind the
    # ridge onto this plane)
    plane_ground = _ground_poly(ring3d)
    overlaps = []
    for r in obstructions or []:
        g = _ground_poly(r)
        if g is not None and plane_ground is not None:
            inter = g.intersection(plane_ground)
            if not inter.is_empty and inter.area > 0.3:
                overlaps.append(inter)
    if overlaps:
        from shapely.ops import unary_union
        ox_, oy_ = origin[0], origin[1]
        a1, a2 = u[0], u[1]
        b1 = v[0] - v[2] * n[0] / n[2]
        b2 = v[1] - v[2] * n[1] / n[2]
        mat = [a1, a2, b1, b2, -(a1 * ox_ + a2 * oy_), -(b1 * ox_ + b2 * oy_)]
        obstr2d = affinity.affine_transform(unary_union(overlaps), mat)
        poly = poly.difference(obstr2d.buffer(margin))
    inset = poly.buffer(-margin)
    cells = _grid_fill(inset, target, from_y=poly.bounds[1])

    panels3d, ground = [], []
    for c in cells:
        quad = []
        for px, py in list(c.exterior.coords)[:4]:
            quad.append((origin[0] + u[0] * px + v[0] * py + n[0] * lift,
                         origin[1] + u[1] * px + v[1] * py + n[1] * lift,
                         origin[2] + u[2] * px + v[2] * py + n[2] * lift))
        panels3d.append(quad)
        ground.append(Polygon([(q[0], q[1]) for q in quad]))

    aspect = ((-math.degrees(math.atan2(n[0], -n[1])) + 180.0) % 360.0) - 180.0
    return RoofPlan(shape="lod2", tilt=round(tilt, 1), aspect=round(aspect, 1),
                    ridge_angle=math.degrees(math.atan2(u[1], u[0])),
                    panels_ground=ground, panels3d=panels3d)


def plan_flat_measured(ring3d: list, target: int, obstructions: list = None,
                       rack_tilt: float = 15.0, margin: float = 0.6,
                       base_lift: float = 0.35) -> Optional[RoofPlan]:
    """South-facing racked rows on a measured flat slab (LoD2), avoiding
    everything that rises above it (penthouses, mechanical rooms)."""
    ring = ring3d[:-1] if tuple(ring3d[0]) == tuple(ring3d[-1]) else ring3d
    if len(ring) < 3 or target <= 0:
        return None
    slab_z = min(p[2] for p in ring)
    region = Polygon([(p[0], p[1]) for p in ring])
    if not region.is_valid:
        region = region.buffer(0)
    obstr_polys = []
    for r in obstructions or []:
        rr = r[:-1] if tuple(r[0]) == tuple(r[-1]) else r
        if len(rr) >= 3:
            p = Polygon([(q[0], q[1]) for q in rr])
            if not p.is_valid:
                p = p.buffer(0)
            obstr_polys.append(p)
    if obstr_polys:
        from shapely.ops import unary_union
        region = region.difference(unary_union(obstr_polys).buffer(margin))
    region = region.buffer(-margin)
    cells = _grid_fill(region, target, from_y=region.bounds[1] if not region.is_empty
                       else 0.0, row_gap=FLAT_ROW_GAP)

    tan_t = math.tan(math.radians(rack_tilt))
    panels3d, ground = [], []
    for c in cells:
        corners = list(c.exterior.coords)[:4]
        ymin = min(cy for _, cy in corners)
        quad = [(cx, cy, slab_z + base_lift + (cy - ymin) * tan_t)
                for cx, cy in corners]
        panels3d.append(quad)
        ground.append(c)
    return RoofPlan(shape="lod2_flat", tilt=rack_tilt, aspect=0.0, ridge_angle=0.0,
                    panels_ground=ground, panels3d=panels3d, updir=(0.0, 1.0))


def dominant_angle(poly: Polygon) -> float:
    """Angle (degrees) of the longest edge of the minimum rotated rectangle."""
    mrr = poly.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)
    best_len, best_angle = -1.0, 0.0
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        if length > best_len:
            best_len = length
            best_angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    return best_angle


def _aspect_of(direction: Tuple[float, float]) -> float:
    """PVGIS aspect (0=south, -90=east, 90=west) of a ground direction vector."""
    a = -math.degrees(math.atan2(direction[0], -direction[1]))
    return ((a + 180.0) % 360.0) - 180.0


def _grid_fill(region: Polygon, target: int, from_y: float,
               row_gap: float = GAP) -> List[Polygon]:
    """Portrait panels row by row, starting at the edge nearest `from_y`.

    Works on Polygon AND MultiPolygon regions (obstruction subtraction can
    split a roof into several usable patches)."""
    if region.is_empty or target <= 0:
        return []
    EPS = 1e-4                       # keep cells strictly inside despite fp noise
    minx, miny, maxx, maxy = region.bounds
    minx += EPS; miny += EPS; maxx -= EPS; maxy -= EPS
    rows_up = abs(from_y - miny) <= abs(from_y - maxy)
    panels: List[Polygon] = []
    y = miny if rows_up else maxy - PANEL_L
    while (y + PANEL_L <= maxy if rows_up else y >= miny) and len(panels) < target:
        x = minx
        while x + PANEL_W <= maxx and len(panels) < target:
            cell = box(x, y, x + PANEL_W, y + PANEL_L)
            # covers, not contains: rows/columns at the inset edge touch the
            # boundary exactly and must still count
            if region.covers(cell):
                panels.append(cell)
            x += PANEL_W + GAP
        y = y + PANEL_L + row_gap if rows_up else y - PANEL_L - row_gap
    return panels


FLAT_ROW_GAP = 0.9      # m between racked rows on flat roofs (inter-row shading)


def _ground_poly(ring3d) -> Optional[Polygon]:
    rr = ring3d[:-1] if tuple(ring3d[0]) == tuple(ring3d[-1]) else ring3d
    if len(rr) < 3:
        return None
    p = Polygon([(q[0], q[1]) for q in rr])
    return p if p.is_valid else p.buffer(0)


def plan(roof: Polygon, usable_fraction: float, spec: RoofSpec,
         prefer_aspect: Optional[float] = None) -> RoofPlan:
    angle = dominant_angle(roof)
    target = int(roof.area * usable_fraction // (PANEL_W * PANEL_L))
    origin = roof.minimum_rotated_rectangle.centroid

    if spec.is_flat:
        plan_ = RoofPlan(shape=spec.shape, tilt=spec.pitch, aspect=0.0,
                         ridge_angle=angle, updir=(0.0, 1.0))
        inset = roof.buffer(-EDGE_MARGIN)
        if inset.is_empty or target <= 0:
            return plan_
        rot = affinity.rotate(inset, -angle, origin=origin)
        cells = _grid_fill(rot, target, rot.bounds[1], row_gap=FLAT_ROW_GAP)
        plan_.panels_ground = [affinity.rotate(c, angle, origin=origin) for c in cells]
        return plan_

    # ---- gabled: choose the southern roof plane ----
    a_rad = math.radians(angle)
    # in the rotated frame the ridge runs along x through origin.y;
    # the lower half (y < ridge) faces the world direction R(angle)·(0,-1)
    aspect_lower = _aspect_of((math.sin(a_rad), -math.cos(a_rad)))
    aspect_upper = ((aspect_lower + 360.0) % 360.0) - 180.0
    if prefer_aspect is not None:
        # LoD2 measured the productive plane - put the panels on that side
        diff = lambda a, b: abs(((a - b + 180.0) % 360.0) - 180.0)
        lower = diff(aspect_lower, prefer_aspect) <= diff(aspect_upper, prefer_aspect)
    else:
        lower = abs(aspect_lower) <= 90.0
    aspect = aspect_lower if lower else aspect_upper

    rot = affinity.rotate(roof, -angle, origin=origin)
    minx, miny, maxx, maxy = rot.bounds
    yc = origin.y
    half = rot.intersection(box(minx - 1, miny - 1, maxx + 1, yc) if lower
                            else box(minx - 1, yc, maxx + 1, maxy + 1))
    eaves_y = miny if lower else maxy

    # stretch ground projection to true plane size, plan there, project back
    f = 1.0 / math.cos(math.radians(spec.pitch))
    plane = affinity.scale(half, xfact=1.0, yfact=f, origin=(0.0, eaves_y))
    inset = plane.buffer(-EDGE_MARGIN)
    cells = _grid_fill(inset, target, eaves_y)
    ground = [affinity.rotate(
                  affinity.scale(c, xfact=1.0, yfact=1.0 / f, origin=(0.0, eaves_y)),
                  angle, origin=origin)
              for c in cells]

    # eaves reference line and uphill direction in world coordinates
    eaves_pt = affinity.rotate(Polygon([(origin.x, eaves_y)] * 3).centroid,
                               angle, origin=origin)
    up_frame = (0.0, 1.0) if lower else (0.0, -1.0)
    updir = (up_frame[0] * math.cos(a_rad) - up_frame[1] * math.sin(a_rad),
             up_frame[0] * math.sin(a_rad) + up_frame[1] * math.cos(a_rad))
    return RoofPlan(shape=spec.shape, tilt=spec.pitch, aspect=round(aspect, 1),
                    ridge_angle=angle, panels_ground=ground,
                    eaves_origin=(eaves_pt.x, eaves_pt.y), updir=updir)


def coherent_indices(plan: RoofPlan, kept: List[int], row_pitch: float,
                     min_run: int = 3, min_total: int = 4) -> List[int]:
    """Reduce a hole-punched cell set to contiguous panel runs.

    Installers build rectangular strings, not confetti: within each row keep
    only runs of >= min_run adjacent panels; drop the layout entirely when
    fewer than min_total survive. Rows/columns are reconstructed by projecting
    panel centers onto the planning row axis (plan.ridge_angle)."""
    if not kept:
        return []
    a = math.radians(plan.ridge_angle)
    ux, uy = math.cos(a), math.sin(a)          # along-row direction
    vx, vy = -uy, ux                            # row-to-row direction
    rows = {}
    for i in kept:
        c = plan.panels_ground[i].centroid
        row_id = round((c.x * vx + c.y * vy) / row_pitch)
        rows.setdefault(row_id, []).append((c.x * ux + c.y * uy, i))
    out = []
    step = (PANEL_W + GAP) * 1.5
    for _, items in rows.items():
        items.sort()
        run = [items[0]]
        for prev, cur in zip(items, items[1:]):
            if cur[0] - prev[0] <= step:
                run.append(cur)
            else:
                if len(run) >= min_run:
                    out += [i for _, i in run]
                run = [cur]
        if len(run) >= min_run:
            out += [i for _, i in run]
    return out if len(out) >= min_total else []


def render(geoimg: GeoImage, roof_px: List[Tuple[int, int]],
           panels_ground: List[Polygon],
           to_px, existing_boxes: Optional[list] = None) -> np.ndarray:
    """Roof outline + proposed panels (or detected existing panels) on the photo."""
    img = geoimg.img.copy()
    overlay = img.copy()
    for panel in panels_ground:
        pts = np.array([to_px(x, y) for x, y in panel.exterior.coords], np.int32)
        cv2.fillPoly(overlay, [pts], (70, 35, 16))          # dark navy module
    img = cv2.addWeighted(overlay, 0.82, img, 0.18, 0)
    for panel in panels_ground:
        pts = np.array([to_px(x, y) for x, y in panel.exterior.coords], np.int32)
        cv2.polylines(img, [pts], True, (200, 150, 90), 1, cv2.LINE_AA)

    if existing_boxes:
        for rect in existing_boxes:
            pts = cv2.boxPoints(rect).astype(np.int32)
            cv2.polylines(img, [pts], True, (0, 0, 230), 2, cv2.LINE_AA)

    outline = np.array(roof_px, np.int32)
    cv2.polylines(img, [outline], True, (60, 220, 60), 2, cv2.LINE_AA)
    return img
