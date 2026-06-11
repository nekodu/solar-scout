"""Interactive 3D visualisation (three.js).

When measured CityGML LoD2 geometry is available the viewer renders the RAW
surveyed surfaces - every roof plane, hip, dormer and wall exactly as flown -
with the panel quads lying on the measured plane (the same quads that were
projected onto the aerial photo, so 3D, Luftbild, letter and CSV are identical
by construction). Without LoD2 it falls back to the procedural gable/flat box.

All triangulation happens here in Python; the page only assembles buffers.
"""

import base64
import html
import json
import math
from pathlib import Path
from typing import List, Optional

import cv2
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import triangulate as _delaunay

from .imagery import GeoImage
from .layout import RoofPlan, _vcross, _vdot, _vnorm, _vsub
from .roofs import RoofSpec

PANEL_LIFT_FLAT = 0.35
PANEL_LIFT_PITCH = 0.15


# ---------------------------------------------------------------- procedural
def _panel_quads(plan: RoofPlan, spec: RoofSpec, ox: float, oy: float) -> List[list]:
    """Panel corners for the procedural (non-LoD2) roof model."""
    tan_t = math.tan(math.radians(plan.tilt))
    sin_t = math.sin(math.radians(plan.tilt))
    cos_t = math.cos(math.radians(plan.tilt))
    ux, uy = plan.updir
    quads = []
    for poly in plan.panels_ground:
        corners = list(poly.exterior.coords)[:4]
        if plan.shape == "flat":
            ds = [(c[0] * ux + c[1] * uy) for c in corners]
            dmin, base = min(ds), spec.eaves_height + PANEL_LIFT_FLAT
            zs = [base + (d - dmin) * tan_t for d in ds]
        else:
            ex, ey = plan.eaves_origin
            ds = [((c[0] - ex) * ux + (c[1] - ey) * uy) for c in corners]
            zs = [spec.eaves_height + d * tan_t for d in ds]
        nx, ny, nz = -sin_t * ux, -sin_t * uy, cos_t
        lift = PANEL_LIFT_PITCH if plan.shape != "flat" else 0.04
        quads.append([[c[0] - ox + nx * lift, c[1] - oy + ny * lift, z + nz * lift]
                      for c, z in zip(corners, zs)])
    return quads


def _gable_mesh(roof: Polygon, spec: RoofSpec, ox: float, oy: float) -> List[list]:
    mrr = list(roof.minimum_rotated_rectangle.exterior.coords)[:4]
    def d(a, b): return math.hypot(b[0] - a[0], b[1] - a[1])
    if d(mrr[0], mrr[1]) < d(mrr[1], mrr[2]):
        mrr = mrr[1:] + mrr[:1]
    p0, p1, p2, p3 = mrr
    half_w = d(p1, p2) / 2.0
    eaves_z = spec.eaves_height
    ridge_z = eaves_z + half_w * math.tan(math.radians(spec.pitch))
    A = [(p0[0] + p3[0]) / 2 - ox, (p0[1] + p3[1]) / 2 - oy, ridge_z]
    B = [(p1[0] + p2[0]) / 2 - ox, (p1[1] + p2[1]) / 2 - oy, ridge_z]
    P = lambda p: [p[0] - ox, p[1] - oy, eaves_z]
    c0, c1, c2, c3 = P(p0), P(p1), P(p2), P(p3)
    return [[c0, c1, B], [c0, B, A], [c3, A, B], [c3, B, c2],
            [c0, A, c3], [c1, c2, B]]


# ------------------------------------------------------------------- LoD2 raw
def _triangulate_ring(ring3d: list) -> List[list]:
    """Triangles of one planar 3D polygon (roof plane, wall, ...)."""
    ring = ring3d[:-1] if tuple(ring3d[0]) == tuple(ring3d[-1]) else ring3d
    if len(ring) < 3:
        return []
    if len(ring) == 3:
        return [list(ring)]
    n = (0.0, 0.0, 0.0)
    for a, b in zip(ring, ring[1:] + ring[:1]):
        n = (n[0] + (a[1] - b[1]) * (a[2] + b[2]),
             n[1] + (a[2] - b[2]) * (a[0] + b[0]),
             n[2] + (a[0] - b[0]) * (a[1] + b[1]))
    n = _vnorm(n)
    if abs(n[2]) > 0.99:
        u, v = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    else:
        u = _vnorm(_vcross((0.0, 0.0, 1.0), n))
        v = _vcross(n, u)
    o = ring[0]
    pts2d = [(_vdot(_vsub(p, o), u), _vdot(_vsub(p, o), v)) for p in ring]
    if len(ring) == 4:
        return [[ring[0], ring[1], ring[2]], [ring[0], ring[2], ring[3]]]
    try:
        poly = Polygon(pts2d).buffer(0)
        back = {(round(x, 4), round(y, 4)): p for (x, y), p in zip(pts2d, ring)}
        tris = []
        for t in _delaunay(MultiPoint(pts2d)):
            if poly.contains(t.representative_point()):
                tri = [back.get((round(cx, 4), round(cy, 4)))
                       for cx, cy in list(t.exterior.coords)[:3]]
                if all(tri):
                    tris.append(tri)
        if tris:
            return tris
    except Exception:
        pass
    return [[ring[0], ring[i], ring[i + 1]] for i in range(1, len(ring) - 1)]  # fan


def _ring_tilt(ring: list) -> float:
    n = (0.0, 0.0, 0.0)
    rr = ring[:-1] if tuple(ring[0]) == tuple(ring[-1]) else ring
    for a, b in zip(rr, rr[1:] + rr[:1]):
        n = (n[0] + (a[1] - b[1]) * (a[2] + b[2]),
             n[1] + (a[2] - b[2]) * (a[0] + b[0]),
             n[2] + (a[0] - b[0]) * (a[1] + b[1]))
    n = _vnorm(n)
    return math.degrees(math.acos(min(1.0, abs(n[2]))))


def _lod2_tris(surfaces: list, kind: str, ox: float, oy: float, gz: float,
               tilt_range=None) -> List[list]:
    out = []
    for k, ring in surfaces:
        if k != kind:
            continue
        if tilt_range is not None:
            t = _ring_tilt(ring)
            if not (tilt_range[0] <= t < tilt_range[1]):
                continue
        for tri in _triangulate_ring(ring):
            out.append([[p[0] - ox, p[1] - oy, p[2] - gz] for p in tri])
    return out


def write_viewer(roof: Polygon, plan: RoofPlan, spec: RoofSpec, geoimg: GeoImage,
                 info: dict, out_path: Path,
                 lod2_geom: Optional[dict] = None) -> None:
    ox, oy = roof.centroid.x, roof.centroid.y
    ring = [[x - ox, y - oy] for x, y in roof.exterior.coords]

    flat_tris = []
    if lod2_geom:
        # raw surveyed surfaces - also for flat buildings (penthouses & all);
        # tile-red only for pitched planes, gray membrane for flat ones
        gz = lod2_geom["ground_z"]
        roof_tris = _lod2_tris(lod2_geom["surfaces"], "roof", ox, oy, gz,
                               tilt_range=(15.0, 90.0))
        flat_tris = _lod2_tris(lod2_geom["surfaces"], "roof", ox, oy, gz,
                               tilt_range=(0.0, 15.0))
        wall_tris = _lod2_tris(lod2_geom["surfaces"], "wall", ox, oy, gz)
        height = max((p[2] for t in roof_tris for p in t), default=8.0)
        if plan.panels3d:
            panels = [[[p[0] - ox, p[1] - oy, p[2] - gz] for p in q]
                      for q in plan.panels3d]
        else:                       # flat LoD2 roof: procedural racked rows
            panels = _panel_quads(plan, spec, ox, oy)
    else:
        panels = _panel_quads(plan, spec, ox, oy)
        roof_tris = _gable_mesh(roof, spec, ox, oy) if not spec.is_flat else []
        wall_tris = []
        height = spec.eaves_height

    h, w = geoimg.img.shape[:2]
    ok, buf = cv2.imencode(".jpg", geoimg.img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    tex_b64 = base64.b64encode(buf).decode() if ok else ""
    data = json.dumps({
        "ring": ring, "height": round(spec.eaves_height, 2), "camH": round(height, 2),
        "panels": panels, "roofTris": roof_tris, "wallTris": wall_tris,
        "flatTris": flat_tris,
        "lod2": bool(wall_tris),
        "ground": {"w": w * geoimg.resolution, "h": h * geoimg.resolution,
                   "cx": geoimg.minx + w * geoimg.resolution / 2 - ox,
                   "cy": geoimg.maxy - h * geoimg.resolution / 2 - oy},
    })

    doc = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>3D – {html.escape(str(info.get('address','')))}</title>
<style>
 body {{ margin:0; overflow:hidden; font-family: system-ui, sans-serif; }}
 #hud {{ position:fixed; top:12px; left:12px; background:rgba(16,21,28,.88); color:#e8e8e8;
        padding:12px 16px; border-radius:10px; font-size:13px; max-width:300px; }}
 #hud h2 {{ margin:0 0 6px; font-size:14px; }} #hud p {{ margin:3px 0; color:#b8c2d0; }}
</style>
<script type="importmap">{{"imports":{{
 "three":"https://unpkg.com/three@0.160.0/build/three.module.js",
 "three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}}}</script>
</head><body>
<div id="hud"><h2>{html.escape(str(info.get('address','')))}</h2>
<p>{html.escape(str(info.get('orientation','')))}</p>
<p>{info.get('proposed_panels',0)} PV-Module · {info.get('proposed_kwp',0)} kWp ·
{info.get('proposed_panel_area_m2',0)} m²</p>
<p>≈ {info.get('estimated_annual_kwh',0):,.0f} kWh p.a. · ≈ {info.get('estimated_cost_eur',0):,.0f} €</p>
<p style="color:#d9b96a">Unverbindliche Schätzung · Non-binding estimate</p>
<p>🖱 Ziehen/drag: drehen · Scrollen/scroll: Zoom</p></div>
<script id="data" type="application/json">{data}</script>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
const D = JSON.parse(document.getElementById('data').textContent);
const V = p => new THREE.Vector3(p[0], p[2], -p[1]);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xbfd9ee);
const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, .1, 2000);
camera.position.set(D.ground.w*.4, Math.max(D.camH*3.2, 24), D.ground.h*.4);
const renderer = new THREE.WebGLRenderer({{antialias:true}});
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
document.body.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, D.camH*.55, 0);

scene.add(new THREE.AmbientLight(0xffffff, .6));
const sunL = new THREE.DirectionalLight(0xfff3d6, 1.7);
sunL.position.set(-30, 60, 25); sunL.castShadow = true;
sunL.shadow.camera.left=-80; sunL.shadow.camera.right=80;
sunL.shadow.camera.top=80; sunL.shadow.camera.bottom=-80;
scene.add(sunL);

const tex = new THREE.TextureLoader().load('data:image/jpeg;base64,{tex_b64}');
tex.colorSpace = THREE.SRGBColorSpace;
const ground = new THREE.Mesh(new THREE.PlaneGeometry(D.ground.w, D.ground.h),
  new THREE.MeshStandardMaterial({{map: tex}}));
ground.rotation.x = -Math.PI/2;
ground.position.set(D.ground.cx, 0, -D.ground.cy);
ground.receiveShadow = true;
scene.add(ground);

function meshFromTris(tris, mat) {{
  const pos = [];
  for (const t of tris) for (const p of t) {{ const v = V(p); pos.push(v.x, v.y, v.z); }}
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, mat);
  m.castShadow = m.receiveShadow = true;
  return m;
}}

if (D.lod2) {{
  // surveyed LoD2 geometry: real walls + every real roof plane
  scene.add(meshFromTris(D.wallTris, new THREE.MeshStandardMaterial(
    {{color: 0xd8d2c4, roughness:.9, side: THREE.DoubleSide}})));
}} else {{
  const shape = new THREE.Shape(D.ring.map(p => new THREE.Vector2(p[0], p[1])));
  const walls = new THREE.Mesh(
    new THREE.ExtrudeGeometry(shape, {{depth: D.height, bevelEnabled: false}}),
    new THREE.MeshStandardMaterial({{color: 0xd8d2c4}}));
  walls.rotation.x = -Math.PI/2;
  walls.castShadow = walls.receiveShadow = true;
  scene.add(walls);
}}
if (D.roofTris.length) scene.add(meshFromTris(D.roofTris,
  new THREE.MeshStandardMaterial({{color: 0x9c4f3c, roughness:.85,
    side: THREE.DoubleSide}})));
if (D.flatTris && D.flatTris.length) scene.add(meshFromTris(D.flatTris,
  new THREE.MeshStandardMaterial({{color: 0x8e8d89, roughness:.95,
    side: THREE.DoubleSide}})));

const quadTris = D.panels.flatMap(q => [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]);
scene.add(meshFromTris(quadTris, new THREE.MeshStandardMaterial({{
  color: 0x10204a, roughness:.3, metalness:.55, side: THREE.DoubleSide}})));
const linePos = [];
for (const q of D.panels) for (let i = 0; i < 4; i++) {{
  const a = V(q[i]), b = V(q[(i+1)%4]);
  linePos.push(a.x, a.y, a.z, b.x, b.y, b.z);
}}
const lg = new THREE.BufferGeometry();
lg.setAttribute('position', new THREE.Float32BufferAttribute(linePos, 3));
scene.add(new THREE.LineSegments(lg,
  new THREE.LineBasicMaterial({{color: 0x9aa7b8, transparent:true, opacity:.7}})));

addEventListener('resize', () => {{
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
}});
(function loop() {{ requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); }})();
</script></body></html>"""
    out_path.write_text(doc, encoding="utf-8")
