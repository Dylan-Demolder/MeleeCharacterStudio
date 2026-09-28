"""Procedural model kit: build an original low-poly character from a JSON spec.

A spec (``model.json`` in a project) names skeleton landmarks, a colour
palette, optional pixel-art decals and a list of parts. Each part is a
primitive placed against the landmarks and pinned to one body segment, so the
generated ``rig.json`` knows exactly which bone every vertex follows:

* ``capsule``  – tapered tube between two points with rounded ends
* ``cylinder`` – tapered prism between two points (``sides`` 4 = square)
* ``box``      – ``center``/``size`` (+``rotate``), or ``from``/``to`` with a
  cross-section ``size`` [w, d]; each face may show a decal
* ``sphere``   – ellipsoid at ``center`` with ``radius`` (scalar or [x, y, z])
* ``cone``     – ``from`` (base) to ``to`` (tip)
* ``extrude``  – planar polygon (``points`` in the ``u``/``v`` plane at
  ``origin``) given ``thickness``; wings, ears, blades, capes

Points are ``[x, y, z]``, a landmark name, or ``{"at": name, "offset": [...]}``
(``"lerp": [a, b, t]`` interpolates two landmarks). ``"mirror": true``
duplicates a part across X with ``r_``/``l_`` names swapped. Models are Y-up
and face +Z; the character's right side is -X.

Output: ``model/model.gltf`` (+ ``.bin`` and a texture atlas ``.png``). All
geometry is generated here; no game or third-party asset is used.
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from .hsd_texture import rgba_png


class ModelKitError(ValueError):
    pass


LANDMARKS = ("pelvis", "chest", "neck", "head", "r_shoulder", "r_elbow", "r_wrist", "r_hand",
             "l_shoulder", "l_elbow", "l_wrist", "l_hand", "r_hip", "r_knee", "r_ankle", "r_toe",
             "l_hip", "l_knee", "l_ankle", "l_toe")
SEGMENTS = ("pelvis", "chest", "head", "r_clav", "r_upper", "r_fore", "r_hand", "l_clav", "l_upper", "l_fore",
            "l_hand", "r_thigh", "r_shin", "r_foot", "l_thigh", "l_shin", "l_foot")
ATLAS = 512
CELL = 32
GRID = ATLAS // CELL


# ------------------------------------------------------------------ vectors

def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _len(a): return math.sqrt(_dot(a, a))


def _norm(a):
    n = _len(a)
    if n < 1e-12:
        raise ModelKitError("degenerate direction")
    return _mul(a, 1.0 / n)


def _basis(axis, hint=None):
    """Orthonormal (u, v, w) with w along ``axis``; u follows ``hint`` or world X."""
    w = _norm(axis)
    for ref in ([hint] if hint else []) + [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]:
        u = _sub(ref, _mul(w, _dot(ref, w)))
        if _len(u) > 1e-6:
            u = _norm(u); break
    v = _cross(w, u)
    return u, v, w


def _euler(deg):
    """Rotation matrix (rows) for XYZ Euler degrees applied X, then Y, then Z."""
    x, y, z = (math.radians(float(d)) for d in deg)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))
    def mm(a, b): return tuple(tuple(sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)) for r in range(3))
    return mm(rz, mm(ry, rx))


def _apply(m, v): return tuple(m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] for r in range(3))


# --------------------------------------------------------------- texture atlas

def _hex(value):
    value = value.lstrip("#")
    if len(value) != 6:
        raise ModelKitError(f"colour {value!r} must be #rrggbb")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


class Atlas:
    """Flat colours in 32px cells; decals in 64px (2x2 cell) regions."""

    def __init__(self, palette, decals):
        self.palette = {k: _hex(v) for k, v in palette.items()}
        self.pixels = [(255, 0, 255, 255)] * (ATLAS * ATLAS)
        self.cells = {}; self.regions = {}; self.next = 0
        for name, decal in (decals or {}).items():
            self.regions[name] = self._decal(name, decal)

    def _alloc(self, span):
        # decals take aligned 2x2 blocks from the end, colours single cells from the start
        if span == 1:
            index = self.next; self.next += 1
        else:
            taken = len(self.regions)
            per_row = GRID // 2
            index = (GRID * GRID // 4 - 1 - taken)
            col, row = (index % per_row) * 2, (index // per_row) * 2
            return col, row
        if index >= GRID * GRID - 4 * (len(self.regions) + 1):
            raise ModelKitError("texture atlas is full")
        return index % GRID, index // GRID

    def _fill(self, col, row, span, pixel):
        for y in range(row * CELL, (row + span) * CELL):
            for x in range(col * CELL, (col + span) * CELL):
                self.pixels[y * ATLAS + x] = pixel(x - col * CELL, y - row * CELL)

    def colour(self, key):
        if key not in self.cells:
            rgba = self.palette[key] if key in self.palette else _hex(key)
            col, row = self._alloc(1)
            self._fill(col, row, 1, lambda x, y: rgba)
            self.cells[key] = ((col + 0.5) * CELL / ATLAS, (row + 0.5) * CELL / ATLAS)
        return self.cells[key]

    def _decal(self, name, decal):
        rows = decal["rows"]; h = len(rows); w = len(rows[0])
        if any(len(r) != w for r in rows) or w > 64 or h > 64:
            raise ModelKitError(f"decal {name}: rows must be equal length and at most 64")
        colours = {k: (self.palette[v] if v in self.palette else _hex(v)) for k, v in decal["colors"].items()}
        col, row = self._alloc(2)
        size = 2 * CELL

        def pixel(x, y):
            ch = rows[min(h - 1, y * h // size)][min(w - 1, x * w // size)]
            if ch not in colours:
                raise ModelKitError(f"decal {name}: no colour for {ch!r}")
            return colours[ch]
        self._fill(col, row, 2, pixel)
        inset = 0.5 / ATLAS
        return (col * CELL / ATLAS + inset, row * CELL / ATLAS + inset,
                (col + 2) * CELL / ATLAS - inset, (row + 2) * CELL / ATLAS - inset)

    def png(self):
        return rgba_png({"width": ATLAS, "height": ATLAS, "rgba": bytes(c for p in self.pixels for c in p)})


# --------------------------------------------------------------------- mesh

class MeshBuilder:
    def __init__(self):
        self.positions = []; self.normals = []; self.uvs = []; self.triangles = []; self.ranges = []

    def vertex(self, p, n, uv):
        self.positions.append(tuple(float(c) for c in p)); self.normals.append(_norm(n) if _len(n) > 1e-12 else (0.0, 1.0, 0.0))
        self.uvs.append(uv); return len(self.positions) - 1

    def tri(self, a, b, c):
        self.triangles.append((a, b, c))

    def quad(self, a, b, c, d):
        self.tri(a, b, c); self.tri(a, c, d)


def _ring_surface(mb, rings, uv, closed_caps):
    """Triangulate a list of rings (each a list of (point, normal)) as a tube."""
    ids = [[mb.vertex(p, n, uv) for p, n in ring] for ring in rings]
    sides = len(rings[0])
    for r in range(len(rings) - 1):
        for s in range(sides):
            a, b = ids[r][s], ids[r][(s + 1) % sides]
            c, d = ids[r + 1][(s + 1) % sides], ids[r + 1][s]
            mb.quad(a, b, c, d)
    for ring_ids, sign in closed_caps:
        centre = tuple(sum(mb.positions[i][k] for i in ring_ids) / sides for k in range(3))
        normal = _cross(_sub(mb.positions[ring_ids[1]], mb.positions[ring_ids[0]]), _sub(mb.positions[ring_ids[2]], mb.positions[ring_ids[0]]))
        normal = _mul(normal, sign)
        cap = [mb.vertex(mb.positions[i], normal, uv) for i in ring_ids]
        c = mb.vertex(centre, normal, uv)
        for s in range(sides):
            if sign > 0:
                mb.tri(c, cap[s], cap[(s + 1) % sides])
            else:
                mb.tri(c, cap[(s + 1) % sides], cap[s])
    return ids


def build_capsule(mb, a, b, ra, rb, uv, sides=10, cap_rings=3, twist=0.0, hint=None):
    axis = _sub(b, a); length = _len(axis)
    u, v, w = _basis(axis, hint)
    rings = []
    profile = []  # (t along axis, radius, normal axial component)
    for i in range(cap_rings, 0, -1):
        ang = math.pi / 2 * i / cap_rings
        profile.append((-ra * math.sin(ang), ra * math.cos(ang), -math.sin(ang)))
    profile += [(0.0, ra, 0.0), (length, rb, 0.0)]
    for i in range(1, cap_rings + 1):
        ang = math.pi / 2 * i / cap_rings
        profile.append((length + rb * math.sin(ang), rb * math.cos(ang), math.sin(ang)))
    tip_a = mb.vertex(_sub(a, _mul(w, ra)), _mul(w, -1), uv)
    for t, r, nz in profile:
        ring = []
        for s in range(sides):
            ang = 2 * math.pi * s / sides + math.radians(twist)
            radial = _add(_mul(u, math.cos(ang)), _mul(v, math.sin(ang)))
            p = _add(_add(a, _mul(w, t)), _mul(radial, max(r, 1e-4)))
            n = _add(_mul(radial, math.sqrt(max(0.0, 1 - nz * nz))), _mul(w, nz))
            ring.append((p, n))
        rings.append(ring)
    ids = _ring_surface(mb, rings, uv, ())
    tip_b = mb.vertex(_add(b, _mul(w, rb)), w, uv)
    for s in range(sides):
        mb.tri(tip_a, ids[0][(s + 1) % sides], ids[0][s])
        mb.tri(tip_b, ids[-1][s], ids[-1][(s + 1) % sides])


def build_cylinder(mb, a, b, ra, rb, uv, sides=10, twist=0.0, hint=None, caps=True):
    axis = _sub(b, a); u, v, w = _basis(axis, hint)
    flat = sides <= 6
    rings = []
    slope = (ra - rb) / max(_len(axis), 1e-9)
    if flat:
        # separate vertices per face so each side is flat shaded
        for s in range(sides):
            a0 = 2 * math.pi * s / sides + math.radians(twist) + (math.pi / sides if sides == 4 else 0)
            a1 = 2 * math.pi * (s + 1) / sides + math.radians(twist) + (math.pi / sides if sides == 4 else 0)
            d0 = _add(_mul(u, math.cos(a0)), _mul(v, math.sin(a0))); d1 = _add(_mul(u, math.cos(a1)), _mul(v, math.sin(a1)))
            n = _add(_norm(_add(d0, d1)), _mul(w, slope))
            p00 = _add(a, _mul(d0, ra)); p01 = _add(a, _mul(d1, ra)); p10 = _add(b, _mul(d0, rb)); p11 = _add(b, _mul(d1, rb))
            ids = [mb.vertex(p, n, uv) for p in (p00, p01, p11, p10)]
            mb.quad(*ids)
        ring_a = [_add(a, _mul(_add(_mul(u, math.cos(2 * math.pi * s / sides + math.radians(twist) + (math.pi / sides if sides == 4 else 0))),
                                         _mul(v, math.sin(2 * math.pi * s / sides + math.radians(twist) + (math.pi / sides if sides == 4 else 0)))), ra)) for s in range(sides)]
        ring_b = [_add(b, _sub(p, a)) if ra == 0 else _add(b, _mul(_sub(p, a), rb / ra)) for p in ring_a]
        if caps:
            for ring, normal, flip in ((ring_a, _mul(w, -1), True), (ring_b, w, False)):
                ids = [mb.vertex(p, normal, uv) for p in ring]
                for s in range(1, sides - 1):
                    if flip: mb.tri(ids[0], ids[s + 1], ids[s])
                    else: mb.tri(ids[0], ids[s], ids[s + 1])
        return
    for t, r in ((0.0, ra), (1.0, rb)):
        ring = []
        for s in range(sides):
            ang = 2 * math.pi * s / sides + math.radians(twist)
            radial = _add(_mul(u, math.cos(ang)), _mul(v, math.sin(ang)))
            ring.append((_add(_add(a, _mul(axis, t)), _mul(radial, r)), _add(radial, _mul(w, slope))))
        rings.append(ring)
    ids = _ring_surface(mb, rings, uv, ())
    if caps:
        _ring_surface_caps(mb, ids, uv)


def _ring_surface_caps(mb, ids, uv):
    for ring_ids, flip in ((ids[0], True), (ids[-1], False)):
        pts = [mb.positions[i] for i in ring_ids]; n = len(pts)
        centre = tuple(sum(p[k] for p in pts) / n for k in range(3))
        normal = _cross(_sub(pts[1], pts[0]), _sub(pts[2], pts[0]))
        normal = _mul(normal, -1 if flip else 1)
        # ring order is counter-clockwise about +axis, so the start cap faces -axis
        cap = [mb.vertex(p, normal, uv) for p in pts]; c = mb.vertex(centre, normal, uv)
        for s in range(n):
            if flip: mb.tri(c, cap[(s + 1) % n], cap[s])
            else: mb.tri(c, cap[s], cap[(s + 1) % n])


def build_sphere(mb, centre, radii, uv, rot=None, rings=8, sides=12):
    rx, ry, rz = radii
    grid = []
    for i in range(rings + 1):
        phi = math.pi * i / rings
        row = []
        for s in range(sides):
            th = 2 * math.pi * s / sides
            d = (math.sin(phi) * math.cos(th), math.cos(phi), math.sin(phi) * math.sin(th))
            p = (d[0] * rx, d[1] * ry, d[2] * rz); n = (d[0] / rx, d[1] / ry, d[2] / rz)
            if rot: p = _apply(rot, p); n = _apply(rot, n)
            row.append(mb.vertex(_add(centre, p), n, uv))
        grid.append(row)
    for i in range(rings):
        for s in range(sides):
            a, b = grid[i][s], grid[i][(s + 1) % sides]
            c, d = grid[i + 1][(s + 1) % sides], grid[i + 1][s]
            if i > 0: mb.tri(a, c, b)
            if i < rings - 1: mb.tri(a, d, c)


BOX_FACES = {  # name: (normal axis index, sign, u axis, v axis)
    "right": (0, -1), "left": (0, 1), "top": (1, 1), "bottom": (1, -1), "front": (2, 1), "back": (2, -1),
}


def build_box(mb, centre, axes, half, uv_for_face):
    """``axes`` = (x, y, z) unit vectors, ``half`` = half extents; faces use decal UVs."""
    ex, ey, ez = (_mul(axes[i], half[i]) for i in range(3))
    faces = {
        "front": (ez, ex, ey), "back": (_mul(ez, -1), _mul(ex, -1), ey),
        "left": (ex, _mul(ez, -1), ey), "right": (_mul(ex, -1), ez, ey),
        "top": (ey, ex, _mul(ez, -1)), "bottom": (_mul(ey, -1), ex, ez),
    }
    for name, (n, du, dv) in faces.items():
        c = _add(centre, n)
        corners = [_sub(_sub(c, du), dv), _sub(_add(c, du), dv), _add(_add(c, du), dv), _add(_sub(c, du), dv)]
        uvs = uv_for_face(name)
        ids = [mb.vertex(p, n, uvs[i]) for i, p in enumerate(corners)]
        mb.quad(*ids)


def _triangulate(poly):
    """Ear clipping for a simple polygon of 2D points (either winding)."""
    area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))
    idx = list(range(len(poly)))
    if area < 0:
        idx.reverse()
    out = []

    def inside(p, a, b, c):
        def s(p1, p2, p3): return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
        d1, d2, d3 = s(p, a, b), s(p, b, c), s(p, c, a)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-12:
                continue
            if any(inside(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2)); idx.pop(k); break
        else:
            raise ModelKitError("extrude polygon could not be triangulated (self-intersecting?)")
    out.append(tuple(idx))
    return out


def build_extrude(mb, origin, u, v, points, thickness, uv):
    n = _norm(_cross(u, v)); half = _mul(n, thickness / 2)
    pts3 = [_add(origin, _add(_mul(u, p[0]), _mul(v, p[1]))) for p in points]
    tris = _triangulate(points)
    front = [mb.vertex(_add(p, half), n, uv) for p in pts3]
    back = [mb.vertex(_sub(p, half), _mul(n, -1), uv) for p in pts3]
    for a, b, c in tris:
        mb.tri(front[a], front[b], front[c]); mb.tri(back[a], back[c], back[b])
    count = len(pts3)
    area = sum(points[k][0] * points[(k + 1) % count][1] - points[(k + 1) % count][0] * points[k][1] for k in range(count))
    for i in range(count):
        j = (i + 1) % count
        edge = _sub(pts3[j], pts3[i])
        # edge x n points outward for a counter-clockwise outline
        side = _mul(_cross(edge, n), 1 if area > 0 else -1)
        if _len(side) < 1e-9:
            continue
        ids = [mb.vertex(p, side, uv) for p in (_add(pts3[i], half), _sub(pts3[i], half), _sub(pts3[j], half), _add(pts3[j], half))]
        mb.quad(*ids)


# ----------------------------------------------------------------- the spec

def _swap_side(name):
    if isinstance(name, str):
        if name.startswith("r_"): return "l_" + name[2:]
        if name.startswith("l_"): return "r_" + name[2:]
    return name


def _mirror_value(value):
    if isinstance(value, str):
        return _swap_side(value)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ("offset",) and isinstance(v, list):
                out[k] = [-v[0], v[1], v[2]]
            elif k in ("at", "segment"):
                out[k] = _swap_side(v)
            elif k == "lerp":
                out[k] = [_swap_side(v[0]), _swap_side(v[1]), v[2]]
            else:
                out[k] = _mirror_value(v)
        return out
    if isinstance(value, list) and len(value) == 3 and all(isinstance(x, (int, float)) for x in value):
        return [-value[0], value[1], value[2]]
    if isinstance(value, list):
        return [_mirror_value(x) for x in value]
    return value


def mirror_part(part):
    out = {}
    for k, v in part.items():
        if k in ("from", "to", "center", "origin", "hint"):
            out[k] = _mirror_value(v)
        elif k == "segment":
            out[k] = _swap_side(v)
        elif k in ("u", "v") and isinstance(v, list):
            out[k] = [-v[0], v[1], v[2]]
        elif k == "rotate":
            out[k] = [v[0], -v[1], -v[2]]
        elif k == "faces" and isinstance(v, dict):
            out[k] = {({"left": "right", "right": "left"}.get(f, f)): d for f, d in v.items()}
        elif k != "mirror":
            out[k] = v
    return out


def complete_landmarks(landmarks):
    """Fill missing ``l_*`` landmarks by mirroring ``r_*`` across X."""
    lm = {k: tuple(float(c) for c in v) for k, v in landmarks.items()}
    for name in LANDMARKS:
        if name not in lm and name.startswith("l_") and "r_" + name[2:] in lm:
            x, y, z = lm["r_" + name[2:]]; lm[name] = (-x, y, z)
    missing = [n for n in LANDMARKS if n not in lm]
    if missing:
        raise ModelKitError(f"missing landmarks: {missing}")
    return lm


def _point(value, lm):
    if isinstance(value, str):
        if value not in lm:
            raise ModelKitError(f"unknown landmark {value!r}")
        return lm[value]
    if isinstance(value, dict):
        if "lerp" in value:
            a, b, t = value["lerp"]; base = _add(_point(a, lm), _mul(_sub(_point(b, lm), _point(a, lm)), float(t)))
        else:
            base = _point(value["at"], lm)
        return _add(base, tuple(float(c) for c in value.get("offset", (0, 0, 0))))
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(float(c) for c in value)
    raise ModelKitError(f"bad point {value!r}")


def build_model(spec):
    """Return (MeshBuilder, Atlas, landmarks, segment_ranges) for a spec dict."""
    lm = complete_landmarks(spec["landmarks"])
    atlas = Atlas(spec.get("palette", {}), spec.get("decals", {}))
    mb = MeshBuilder(); ranges = []
    parts = []
    for part in spec["parts"]:
        parts.append(part)
        if part.get("mirror"):
            parts.append(mirror_part(part))
    for index, part in enumerate(parts):
        start = len(mb.positions)
        shape = part.get("shape")
        try:
            _build_part(mb, atlas, lm, part)
        except (KeyError, TypeError) as exc:
            raise ModelKitError(f"part {index} ({shape}): missing or bad field {exc}") from exc
        segment = part.get("segment")
        if segment:
            if segment not in SEGMENTS:
                raise ModelKitError(f"part {index}: unknown segment {segment!r}")
            if len(mb.positions) > start:
                ranges.append([start, len(mb.positions) - 1, segment])
    return mb, atlas, lm, ranges


def _build_part(mb, atlas, lm, part):
    shape = part["shape"]
    tri_start = len(mb.triangles); vert_start = len(mb.positions)
    uv = atlas.colour(part.get("color", "#ff00ff"))
    hint = _point(part["hint"], lm) if "hint" in part and not isinstance(part["hint"], list) else (tuple(part["hint"]) if "hint" in part else None)
    if shape in ("capsule", "cylinder", "cone"):
        a, b = _point(part["from"], lm), _point(part["to"], lm)
        r = part.get("radius", 1.0)
        ra, rb = (float(r), float(r)) if not isinstance(r, list) else (float(r[0]), float(r[1]))
        if shape == "cone":
            ra, rb = float(r if not isinstance(r, list) else r[0]), 0.0
        sides = int(part.get("sides", 10 if shape != "cone" else 8))
        if shape == "capsule":
            build_capsule(mb, a, b, ra, rb, uv, sides=sides, twist=part.get("twist", 0.0), hint=hint)
        else:
            build_cylinder(mb, a, b, ra, max(rb, 0.0), uv, sides=sides, twist=part.get("twist", 0.0), hint=hint, caps=part.get("caps", True))
    elif shape == "sphere":
        r = part.get("radius", 1.0)
        radii = (float(r),) * 3 if not isinstance(r, list) else tuple(float(x) for x in r)
        rot = _euler(part["rotate"]) if "rotate" in part else None
        build_sphere(mb, _point(part["center"], lm), radii, uv, rot=rot, rings=int(part.get("rings", 8)), sides=int(part.get("sides", 12)))
    elif shape == "box":
        faces = part.get("faces", {})
        if "from" in part:
            a, b = _point(part["from"], lm), _point(part["to"], lm)
            axis = _sub(b, a); length = _len(axis)
            # box frame: y along the bone (pointing up when it can), x along
            # the hint (towards +X, so a mirrored copy gets the same frame),
            # z = x cross y; a vertical box's "front" then faces +Z.
            y = _norm(axis)
            if y[1] < 0:
                y = _mul(y, -1)
            x, _, _ = _basis(y, hint or (1.0, 0.0, 0.0))
            if x[0] < 0 or (abs(x[0]) < 1e-9 and x[2] < 0):
                x = _mul(x, -1)
            z = _cross(x, y)
            w, d = part["size"]; pad = float(part.get("extend", 0.0))
            centre = _add(a, _mul(axis, 0.5)); half = (w / 2, length / 2 + pad, d / 2)
            axes = (x, y, z)
        else:
            centre = _point(part["center"], lm); half = tuple(float(s) / 2 for s in part["size"])
            rot = _euler(part.get("rotate", (0, 0, 0)))
            axes = tuple(_apply(rot, e) for e in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        # a mirrored part arrives with mirrored centre/ends and rotation
        # (x, -y, -z), which is exactly the reflected box; _fix_winding
        # re-orients the triangles afterwards.

        def uv_for_face(name):
            decal = faces.get(name)
            if decal is None:
                return [uv] * 4
            if decal not in atlas.regions:
                raise ModelKitError(f"unknown decal {decal!r}")
            u0, v0, u1, v1 = atlas.regions[decal]
            return [(u0, v1), (u1, v1), (u1, v0), (u0, v0)]
        build_box(mb, centre, axes, half, uv_for_face)
    elif shape == "extrude":
        origin = _point(part["origin"], lm)
        u = _norm(tuple(float(c) for c in part.get("u", (1, 0, 0))))
        v = _norm(tuple(float(c) for c in part.get("v", (0, 1, 0))))
        pts = [tuple(map(float, p)) for p in part["points"]]
        build_extrude(mb, origin, u, v, pts, float(part.get("thickness", 0.3)), uv)
    else:
        raise ModelKitError(f"unknown shape {shape!r}")
    _fix_winding(mb, tri_start, vert_start)


def _fix_winding(mb, tri_start, vert_start):
    """Make every new triangle agree with its vertex normals (outward-facing)."""
    for t in range(tri_start, len(mb.triangles)):
        a, b, c = mb.triangles[t]
        pa, pb, pc = mb.positions[a], mb.positions[b], mb.positions[c]
        face = _cross(_sub(pb, pa), _sub(pc, pa))
        if _len(face) < 1e-14:
            continue
        n = _add(_add(mb.normals[a], mb.normals[b]), mb.normals[c])
        if _dot(face, n) < 0:
            mb.triangles[t] = (a, c, b)


# ---------------------------------------------------------------- writers

def write_gltf(mb, atlas, directory, name="model"):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    pos = b"".join(struct.pack("<3f", *p) for p in mb.positions)
    nrm = b"".join(struct.pack("<3f", *n) for n in mb.normals)
    uvs = b"".join(struct.pack("<2f", *uv) for uv in mb.uvs)
    idx = b"".join(struct.pack("<3I", *t) for t in mb.triangles)
    blob = pos + nrm + uvs + idx
    lo = [min(p[k] for p in mb.positions) for k in range(3)]; hi = [max(p[k] for p in mb.positions) for k in range(3)]
    n = len(mb.positions)
    doc = {
        "asset": {"version": "2.0", "generator": "melee-character-studio model_kit"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": 3, "material": 0}]}],
        "materials": [{"name": "atlas", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0.0, "roughnessFactor": 0.9}}],
        "textures": [{"source": 0, "sampler": 0}], "samplers": [{"magFilter": 9728, "minFilter": 9728}],
        "images": [{"uri": f"{name}.png"}],
        "buffers": [{"uri": f"{name}.bin", "byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(nrm), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos) + len(nrm), "byteLength": len(uvs), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos) + len(nrm) + len(uvs), "byteLength": len(idx), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3", "min": lo, "max": hi},
            {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": n, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5125, "count": 3 * len(mb.triangles), "type": "SCALAR"},
        ],
    }
    (directory / f"{name}.bin").write_bytes(blob)
    (directory / f"{name}.png").write_bytes(atlas.png())
    (directory / f"{name}.gltf").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return directory / f"{name}.gltf"


def generate_project_model(project, *, preview=True):
    """Build ``project/model.json`` into ``project/model/`` and update ``rig.json``."""
    project = Path(project)
    spec = json.loads((project / "model.json").read_text(encoding="utf-8"))
    mb, atlas, lm, ranges = build_model(spec)
    if len(mb.positions) > 0xFFFF:
        raise ModelKitError("model exceeds 65535 vertices")
    gltf = write_gltf(mb, atlas, project / "model", "model")
    rig_path = project / "rig.json"
    rig = json.loads(rig_path.read_text(encoding="utf-8")) if rig_path.is_file() else {}
    rig["model"] = "model/model.gltf"
    rig["landmarks"] = {k: [round(c, 4) for c in lm[k]] for k in LANDMARKS}
    rig["segment_ranges"] = ranges
    rig.setdefault("rotate_y_degrees", 0)
    rig_path.write_text(json.dumps(rig, indent=2) + "\n", encoding="utf-8")
    result = {"model": str(gltf), "vertices": len(mb.positions), "triangles": len(mb.triangles),
              "colours": len(atlas.cells), "decals": len(atlas.regions)}
    if preview:
        from .model_preview import render_views
        result["preview"] = str(render_views(mb, atlas, project / "model" / "preview.png", landmarks=lm))
    return result
