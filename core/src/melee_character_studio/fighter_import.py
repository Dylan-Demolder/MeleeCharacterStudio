"""Import a static glTF mesh as a fighter costume model.

The pipeline is deterministic and never embeds game data in the project:

1. ``load_gltf_mesh`` reads one textured triangle mesh (node transforms applied).
2. ``retarget_mesh`` maps the mesh from its authored pose onto the base
   fighter's bind pose using per-segment landmarks, and derives smoothed
   envelope weights for the base skeleton.
3. ``write_costume`` writes the mapped mesh into a copy of the user's base
   costume archive (for example ``PlCaNr.dat``). The new geometry is hosted by
   existing DObjs so every DObj index the fighter's visibility tables use keeps
   its meaning; all other original PObjs are replaced by an empty display list.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from .hsd_archive import validate_hsd_archive, validate_hsd_relocations
from .hsd_codegen import serialize_hsd_graph
from .hsd_model import HsdReader, _hsd_world_matrices


class FighterImportError(ValueError):
    pass


# --------------------------------------------------------------------------- glTF

def _mat_mul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def _node_matrix(node):
    if "matrix" in node:
        m = node["matrix"]  # column-major
        return [m[c * 4 + r] for r in range(4) for c in range(4)]
    t = node.get("translation", (0, 0, 0)); s = node.get("scale", (1, 1, 1)); q = node.get("rotation", (0, 0, 0, 1))
    x, y, z, w = q
    r = [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
         2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
         2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]
    return [r[0] * s[0], r[1] * s[1], r[2] * s[2], t[0], r[3] * s[0], r[4] * s[1], r[5] * s[2], t[1],
            r[6] * s[0], r[7] * s[1], r[8] * s[2], t[2], 0, 0, 0, 1]


@dataclass
class Mesh:
    positions: list
    normals: list
    uvs: list
    triangles: list  # (i, j, k) vertex indices
    texture: Path | None


def load_gltf_mesh(path):
    """Load every triangle primitive of a .gltf into one mesh in scene space."""
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    buffers = [(path.parent / b["uri"]).read_bytes() for b in doc["buffers"]]

    def accessor(index):
        a = doc["accessors"][index]; view = doc["bufferViews"][a["bufferView"]]
        comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        fmt = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}[a["componentType"]]
        size = struct.calcsize(fmt); stride = view.get("byteStride", size * comps)
        base = view.get("byteOffset", 0) + a.get("byteOffset", 0); data = buffers[view["buffer"]]
        return [struct.unpack_from("<" + fmt * comps, data, base + i * stride) for i in range(a["count"])]

    mesh = Mesh([], [], [], [], None)
    scene = doc["scenes"][doc.get("scene", 0)]

    def visit(node_index, parent):
        node = doc["nodes"][node_index]; matrix = _mat_mul(parent, _node_matrix(node))
        if "mesh" in node:
            for prim in doc["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4:
                    raise FighterImportError("only triangle primitives are supported")
                base = len(mesh.positions); attrs = prim["attributes"]
                for p in accessor(attrs["POSITION"]):
                    mesh.positions.append(tuple(matrix[r * 4] * p[0] + matrix[r * 4 + 1] * p[1] + matrix[r * 4 + 2] * p[2] + matrix[r * 4 + 3] for r in range(3)))
                normals = accessor(attrs["NORMAL"]) if "NORMAL" in attrs else [(0.0, 1.0, 0.0)] * (len(mesh.positions) - base)
                for n in normals:
                    v = tuple(matrix[r * 4] * n[0] + matrix[r * 4 + 1] * n[1] + matrix[r * 4 + 2] * n[2] for r in range(3))
                    length = math.sqrt(sum(c * c for c in v)) or 1.0
                    mesh.normals.append(tuple(c / length for c in v))
                uvs = accessor(attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else [(0.0, 0.0)] * (len(mesh.positions) - base)
                mesh.uvs.extend(tuple(map(float, uv)) for uv in uvs)
                flat = [x[0] for x in accessor(prim["indices"])] if "indices" in prim else list(range(len(mesh.positions) - base))
                mesh.triangles.extend((base + flat[i], base + flat[i + 1], base + flat[i + 2]) for i in range(0, len(flat) - 2, 3))
                if mesh.texture is None and "material" in prim:
                    pbr = doc["materials"][prim["material"]].get("pbrMetallicRoughness", {})
                    tex = pbr.get("baseColorTexture")
                    if tex is not None:
                        mesh.texture = path.parent / doc["images"][doc["textures"][tex["index"]]["source"]]["uri"]
        for child in node.get("children", ()):
            visit(child, matrix)

    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    for root in scene["nodes"]:
        visit(root, identity)
    if not mesh.triangles:
        raise FighterImportError("glTF has no triangles")
    return mesh


# ------------------------------------------------------------------ retargeting

def _sub(a, b): return tuple(a[i] - b[i] for i in range(3))
def _add(a, b): return tuple(a[i] + b[i] for i in range(3))
def _mul(a, s): return tuple(a[i] * s for i in range(3))
def _dot(a, b): return sum(a[i] * b[i] for i in range(3))
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _len(a): return math.sqrt(_dot(a, a))
def _norm(a):
    length = _len(a)
    if length < 1e-9: raise FighterImportError("degenerate segment")
    return _mul(a, 1.0 / length)
def _mat3_vec(m, v): return tuple(sum(m[r][k] * v[k] for k in range(3)) for r in range(3))
def _mat3_mul(a, b): return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
def _mat3_t(a): return [[a[c][r] for c in range(3)] for r in range(3)]


def _min_rotation(a, b):
    """Smallest rotation taking unit vector a onto unit vector b."""
    v = _cross(a, b); c = _dot(a, b)
    if c < -0.999999:
        axis = _norm(_cross(a, (1, 0, 0)) if abs(a[0]) < 0.9 else _cross(a, (0, 1, 0)))
        x, y, z = axis
        return [[2 * x * x - 1, 2 * x * y, 2 * x * z], [2 * x * y, 2 * y * y - 1, 2 * y * z], [2 * x * z, 2 * y * z, 2 * z * z - 1]]
    k = 1.0 / (1.0 + c)
    return [[v[0] * v[0] * k + c, v[0] * v[1] * k - v[2], v[0] * v[2] * k + v[1]],
            [v[1] * v[0] * k + v[2], v[1] * v[1] * k + c, v[1] * v[2] * k - v[0]],
            [v[2] * v[0] * k - v[1], v[2] * v[1] * k + v[0], v[2] * v[2] * k + c]]


def _quat_matrix(q):
    x, y, z, w = (float(c) for c in q)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def _frame(up, right):
    up = _norm(up); fwd = _norm(_cross(right, up)); right = _cross(up, fwd)
    return [[right[i], up[i], fwd[i]] for i in range(3)]  # columns: right, up, forward


# Segment tables per base fighter: (name, source start landmark, source end
# landmark, base start joint, base end joint, envelope joint, parent segment,
# mode). "axial" stretches along the bone so both ends land on base joints;
# "uniform" keeps source proportions (head, hands, feet, clavicles).
BASE_SEGMENTS = {
    "captain-falcon": (
        ("pelvis", "pelvis", "chest", 4, 19, 18, None, "axial"),
        ("chest", "chest", "neck", 19, 38, 19, "pelvis", "axial"),
        ("head", "neck", "head", 38, 39, 39, "chest", "uniform"),
        ("r_clav", "upper_chest", "r_shoulder", 42, 45, 42, "chest", "uniform"),
        ("r_upper", "r_shoulder", "r_elbow", 45, 46, 45, "r_clav", "axial"),
        ("r_fore", "r_elbow", "r_wrist", 46, 47, 46, "r_upper", "axial"),
        ("r_hand", "r_wrist", "r_hand", 47, 49, 47, "r_fore", "uniform"),
        ("l_clav", "upper_chest", "l_shoulder", 21, 23, 21, "chest", "uniform"),
        ("l_upper", "l_shoulder", "l_elbow", 23, 24, 23, "l_clav", "axial"),
        ("l_fore", "l_elbow", "l_wrist", 24, 25, 24, "l_upper", "axial"),
        ("l_hand", "l_wrist", "l_hand", 25, 27, 25, "l_fore", "uniform"),
        ("r_thigh", "r_hip", "r_knee", 13, 14, 13, "pelvis", "axial"),
        ("r_shin", "r_knee", "r_ankle", 14, 16, 14, "r_thigh", "axial"),
        ("r_foot", "r_ankle", "r_toe", 16, 17, 16, "r_shin", "uniform"),
        ("l_thigh", "l_hip", "l_knee", 7, 8, 7, "pelvis", "axial"),
        ("l_shin", "l_knee", "l_ankle", 8, 10, 8, "l_thigh", "axial"),
        ("l_foot", "l_ankle", "l_toe", 10, 11, 10, "l_shin", "uniform"),
    ),
}
# humanoid body part -> parent part (the same tree for every base fighter)
_PART_PARENT = {s[0]: s[6] for s in BASE_SEGMENTS["captain-falcon"]}
BASE_TORSO = {"captain-falcon": {"up": (4, 38), "right": (13, 7)}}


def base_bind_positions(costume, root_symbol):
    report = HsdReader(costume).joints(root_symbol)
    local = [{"position": list(j.position), "rotation": list(j.rotation), "scale": list(j.scale)} for j in report.joints]
    world, _ = _hsd_world_matrices(report.joints, local)
    return report, [(m[3], m[7], m[11]) for m in world]


@dataclass
class RiggedMesh:
    positions: list
    normals: list
    uvs: list
    triangles: list
    weights: list  # per vertex: ((joint, weight), ...)
    segments: list  # per vertex primary segment name


def _weld(positions, eps=1e-4):
    key = {}; ids = []
    for p in positions:
        k = tuple(round(c / eps) for c in p)
        ids.append(key.setdefault(k, len(key)))
    return ids


def retarget_mesh(mesh, landmarks, base_joints, base_fighter, *, smoothing=4, max_influences=3,
                  segment_overrides=None, segment_scale=None, vertex_offsets=None, joint_rotations=None,
                  table=None, segment_modes=None, segment_ranges=None, rigid=None, scale_factor=1.0):
    """Map ``mesh`` (in its own pose) onto the base fighter's bind pose.

    ``joint_rotations`` ({landmark: (qx, qy, qz, qw)}) rotates the source body
    part that starts at that landmark about it before mapping, for models whose
    hands, feet or head are modelled at a different orientation.
    ``segment_overrides`` ({vertex: segment}) pins painted vertices to a body
    part, ``segment_scale`` ({segment: factor}) resizes a part around its base
    joint, and ``vertex_offsets`` ({vertex: (dx, dy, dz)}) are sculpt deltas
    applied in bind space after mapping.

    ``table`` (a ``base_skeleton.SegmentTable``) replaces the hand-measured
    ``BASE_SEGMENTS`` rows for fighters whose table is derived from the disc.
    ``segment_modes`` ({segment: "axial"|"uniform"}) overrides a part's mode,
    for example to keep wings at their modelled length. ``segment_ranges``
    ([[first, last, segment], ...]) pins whole vertex ranges, as a model
    generator that knows its parts writes them. ``rigid`` names one segment
    that carries every vertex unchanged in shape (a vehicle or a prop), and
    ``scale_factor`` multiplies the global scale.
    """
    if table is None:
        if base_fighter not in BASE_SEGMENTS:
            raise FighterImportError(f"no segment table for base fighter {base_fighter!r}; pass the disc's PlCo.dat")
        rows, torso = BASE_SEGMENTS[base_fighter], BASE_TORSO[base_fighter]
    else:
        rows, torso, base_joints = table.rows, table.torso, list(table.points)
    lm = {k: tuple(map(float, v)) for k, v in landmarks.items()}
    if "upper_chest" not in lm:
        lm["upper_chest"] = _add(lm["neck"], _mul(_sub(lm["chest"], lm["neck"]), 0.3))
    present = {s[0] for s in rows}

    def fold(name):
        # a body part the base skeleton lacks (Jigglypuff's hands, Samus's
        # right hand) is carried by the nearest ancestor part it folded into
        while name not in present and name in _PART_PARENT:
            name = _PART_PARENT[name]
        return name

    # modes for folded parts would reshape their ancestor, so they are dropped
    modes = {k: v for k, v in (segment_modes or {}).items() if k in present or k not in _PART_PARENT}
    if rigid:
        rigid = fold(rigid)
        modes[rigid] = "uniform"
    unknown = sorted(set(modes) - present)
    if unknown:
        raise FighterImportError(f"unknown body parts in segment_modes: {unknown}")
    if any(m not in ("axial", "uniform") for m in modes.values()):
        raise FighterImportError("segment modes must be 'axial' or 'uniform'")
    table = tuple(s[:7] + (modes.get(s[0], s[7]),) for s in rows)
    names = [s[0] for s in table]
    missing = sorted(({s[1] for s in table} | {s[2] for s in table}) - set(lm))
    if missing:
        raise FighterImportError(f"missing landmarks: {missing}")
    # Global scale from the axial limb segments.
    ratios = [_len(_sub(base_joints[s[4]], base_joints[s[3]])) / _len(_sub(lm[s[2]], lm[s[1]])) for s in table if s[7] == "axial"]
    if not ratios:
        ratios = [_len(_sub(base_joints[s[4]], base_joints[s[3]])) / _len(_sub(lm[s[2]], lm[s[1]])) for s in rows if s[7] == "axial"]
    scale = sorted(ratios)[len(ratios) // 2] * float(scale_factor)
    # Root rotation from the torso frames, then chain swing rotations.
    src_frame = _frame(_sub(lm["neck"], lm["pelvis"]), _sub(lm["l_hip"], lm["r_hip"]))
    dst_frame = _frame(_sub(base_joints[torso["up"][1]], base_joints[torso["up"][0]]), _sub(base_joints[torso["right"][1]], base_joints[torso["right"][0]]))
    root = _mat3_mul(dst_frame, _mat3_t(src_frame))
    rot = {}; seg = {}
    for s in table:
        name, a, b, j0, j1, owner, parent, mode = s
        src_axis = _norm(_sub(lm[b], lm[a])); dst_axis = _norm(_sub(base_joints[j1], base_joints[j0]))
        prior = rot[parent] if parent else root
        r = _mat3_mul(_min_rotation(_mat3_vec(prior, src_axis), dst_axis), prior)
        rot[name] = r
        seg[name] = {"a": lm[a], "b": lm[b], "len": _len(_sub(lm[b], lm[a])), "axis": src_axis,
                     "base": base_joints[j0], "base_len": _len(_sub(base_joints[j1], base_joints[j0])), "dst_axis": dst_axis,
                     "owner": owner, "mode": mode, "parent": parent}

    def seg_distance(p, s):
        d = _sub(p, s["a"]); t = max(0.0, min(s["len"], _dot(d, s["axis"])))
        return _len(_sub(d, _mul(s["axis"], t)))

    part_scale = {n: float((segment_scale or {}).get(n, 1.0)) for n in names}
    part_rot = {}
    for s_ in table:
        q = (joint_rotations or {}).get(s_[1])
        if q is not None:
            part_rot[s_[0]] = _quat_matrix(q)

    def map_point(p, name):
        s = seg[name]; r = rot[name]; d = _sub(p, s["a"])
        if name in part_rot:
            d = _mat3_vec(part_rot[name], d)
        t = _dot(d, s["axis"]); perp = _sub(d, _mul(s["axis"], t))
        k = scale * part_scale[name]
        if s["mode"] == "uniform":
            return _add(s["base"], _mat3_vec(r, _mul(d, k)))
        ratio = s["base_len"] / s["len"]
        axial = t * ratio if 0 <= t <= s["len"] else (t * k if t < 0 else s["base_len"] + (t - s["len"]) * k)
        return _add(_add(s["base"], _mul(s["dst_axis"], axial)), _mat3_vec(r, _mul(perp, k)))

    # Primary segment = nearest capsule; weights smoothed over welded topology.
    weld = _weld(mesh.positions); n_weld = max(weld) + 1
    primary = [None] * n_weld; rep_pos = [None] * n_weld
    for v, w in enumerate(weld):
        rep_pos[w] = mesh.positions[v]
    for w in range(n_weld):
        primary[w] = min(names, key=lambda n: seg_distance(rep_pos[w], seg[n]))
    pinned = set()
    overrides = {int(v): name for v, name in (segment_overrides or {}).items()}
    for first, last, name in segment_ranges or ():
        for v in range(int(first), int(last) + 1):
            overrides.setdefault(v, name)
    if rigid:
        if rigid not in seg:
            raise FighterImportError(f"unknown rigid body part {rigid!r}")
        overrides = {v: rigid for v in range(len(mesh.positions))}
    for v, name in overrides.items():
        name = fold(name)
        if name not in seg:
            raise FighterImportError(f"unknown body part {name!r}")
        primary[weld[int(v)]] = name; pinned.add(weld[int(v)])
    neighbours = [set() for _ in range(n_weld)]
    for tri in mesh.triangles:
        a, b, c = (weld[i] for i in tri)
        neighbours[a] |= {b, c}; neighbours[b] |= {a, c}; neighbours[c] |= {a, b}
    related = {n: {n} | ({seg[n]["parent"]} if seg[n]["parent"] else set()) | {m for m in names if seg[m]["parent"] == n} for n in names}
    weights = [{primary[w]: 1.0} for w in range(n_weld)]
    for _ in range(smoothing):
        new = []
        for w in range(n_weld):
            acc = dict((k, 0.5 * v) for k, v in weights[w].items())
            if neighbours[w]:
                share = 0.5 / len(neighbours[w])
                for nb in neighbours[w]:
                    for k, v in weights[nb].items():
                        if k in related[primary[w]]:
                            acc[k] = acc.get(k, 0.0) + share * v
            if w in pinned:
                # painted vertices keep their part as the dominant influence
                acc[primary[w]] = acc.get(primary[w], 0.0) + 1.0
            total = sum(acc.values()); new.append({k: v / total for k, v in acc.items()})
        weights = new
    out_pos, out_nrm, out_w, out_seg = [], [], [], []
    for v, w in enumerate(weld):
        items = sorted(weights[w].items(), key=lambda kv: -kv[1])[:max_influences]
        total = sum(x for _, x in items); items = [(k, x / total) for k, x in items]
        # quantize to tenths so envelopes are shared between vertices
        q = [(k, round(x * 10) / 10) for k, x in items]; q = [(k, x) for k, x in q if x > 0]
        diff = round(1.0 - sum(x for _, x in q), 1)
        q[0] = (q[0][0], round(q[0][1] + diff, 1))
        p = mesh.positions[v]
        mapped = (0.0, 0.0, 0.0); normal = (0.0, 0.0, 0.0)
        for k, x in q:
            n_src = _mat3_vec(part_rot[k], mesh.normals[v]) if k in part_rot else mesh.normals[v]
            mapped = _add(mapped, _mul(map_point(p, k), x)); normal = _add(normal, _mul(_mat3_vec(rot[k], n_src), x))
        length = _len(normal) or 1.0
        if vertex_offsets and str(v) in vertex_offsets:
            mapped = _add(mapped, tuple(map(float, vertex_offsets[str(v)])))
        elif vertex_offsets and v in vertex_offsets:
            mapped = _add(mapped, tuple(map(float, vertex_offsets[v])))
        out_pos.append(mapped); out_nrm.append(_mul(normal, 1.0 / length))
        merged = {}
        for k, x in q:
            merged[seg[k]["owner"]] = round(merged.get(seg[k]["owner"], 0.0) + x, 1)
        out_w.append(tuple(sorted(merged.items()))); out_seg.append(primary[w])
    return RiggedMesh(out_pos, out_nrm, list(mesh.uvs), list(mesh.triangles), out_w, out_seg), {"scale": scale}


# --------------------------------------------------------------------- textures

def _to565(c):
    return ((int(c[0]) >> 3) << 11) | ((int(c[1]) >> 2) << 5) | (int(c[2]) >> 3)


def _from565(v):
    r = (v >> 11) & 31; g = (v >> 5) & 63; b = v & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _cmpr_palette(c0, c1):
    e0 = _from565(c0); e1 = _from565(c1)
    return [e0, e1, tuple((2 * e0[k] + e1[k]) // 3 for k in range(3)), tuple((e0[k] + 2 * e1[k]) // 3 for k in range(3))]


def _cmpr_block(block):
    """Encode 16 RGB texels: principal-axis endpoints, then one least-squares refit."""
    mean = [sum(c[k] for c in block) / 16.0 for k in range(3)]
    cov = [[sum((c[i] - mean[i]) * (c[j] - mean[j]) for c in block) for j in range(3)] for i in range(3)]
    axis = [1.0, 1.0, 1.0]
    for _ in range(8):
        axis = [sum(cov[i][j] * axis[j] for j in range(3)) for i in range(3)]
        n = math.sqrt(sum(a * a for a in axis))
        if n < 1e-9:
            axis = [0.577, 0.577, 0.577]; break
        axis = [a / n for a in axis]
    proj = [sum((c[k] - mean[k]) * axis[k] for k in range(3)) for c in block]
    lo, hi = min(proj), max(proj)
    clamp = lambda v: max(0, min(255, int(round(v))))
    ends = [tuple(clamp(mean[k] + hi * axis[k]) for k in range(3)), tuple(clamp(mean[k] + lo * axis[k]) for k in range(3))]
    best = None
    for _ in range(2):
        c0, c1 = _to565(ends[0]), _to565(ends[1])
        if c0 < c1: c0, c1 = c1, c0
        if c0 == c1:
            return struct.pack(">HHI", c0, c1, 0), 0
        pal = _cmpr_palette(c0, c1); bits = 0; err = 0; picks = []
        for i, c in enumerate(block):
            dists = [sum((c[k] - pal[j][k]) ** 2 for k in range(3)) for j in range(4)]
            j = min(range(4), key=dists.__getitem__); picks.append(j); err += dists[j]; bits |= j << (30 - 2 * i)
        if best is None or err < best[1]:
            best = (struct.pack(">HHI", c0, c1, bits), err)
        # least-squares refit of the two endpoints for the chosen interpolation weights
        wts = {0: 1.0, 1: 0.0, 2: 2 / 3, 3: 1 / 3}
        a2 = b2 = ab = 0.0; ax = [0.0] * 3; bx = [0.0] * 3
        for c, j in zip(block, picks):
            a = wts[j]; b = 1 - a; a2 += a * a; b2 += b * b; ab += a * b
            for k in range(3): ax[k] += a * c[k]; bx[k] += b * c[k]
        det = a2 * b2 - ab * ab
        if abs(det) < 1e-9:
            break
        ends = [tuple(clamp((ax[k] * b2 - bx[k] * ab) / det) for k in range(3)), tuple(clamp((bx[k] * a2 - ax[k] * ab) / det) for k in range(3))]
    return best


def encode_cmpr(pixels, width, height):
    """Encode RGB(A) rows (list of (r,g,b[,a]) tuples, row-major) as GX CMPR."""
    if width % 8 or height % 8:
        raise FighterImportError("CMPR textures need dimensions that are multiples of 8")
    out = bytearray()
    for by in range(0, height, 8):
        for bx in range(0, width, 8):
            for sub in range(4):
                sx = bx + (sub & 1) * 4; sy = by + (sub >> 1) * 4
                out += _cmpr_block([pixels[(sy + y) * width + sx + x][:3] for y in range(4) for x in range(4)])[0]
    return bytes(out)


# ----------------------------------------------------------------- costume file

GX_VA_PNMTXIDX, GX_VA_POS, GX_VA_NRM, GX_VA_TEX0, GX_VA_NULL = 0, 9, 10, 13, 0xFF
GX_DIRECT, GX_INDEX16, GX_F32, GX_TRIANGLES = 1, 3, 4, 0x90
POBJ_ENVELOPE = 0x2000
MAX_ENVELOPES_PER_POBJ = 10


class _Builder:
    """Collects new objects at virtual offsets beyond the source data block."""

    def __init__(self, data_size):
        self.cursor = (data_size + 31) // 32 * 32 + 32
        self.objects = {}; self.relocations = []

    def add(self, payload, align=32):
        self.cursor = (self.cursor + align - 1) // align * align
        offset = self.cursor; self.objects[offset] = bytes(payload); self.cursor += len(payload) + 32
        return offset

    def pointer(self, obj, field, target):
        """Record a pointer at ``obj+field`` (bytes patched by caller)."""
        self.relocations.append(obj + field)


def _inverse_transpose_apply(m, n):
    """Transform a normal by the inverse-transpose of the 3x3 part of a 3x4 matrix."""
    a, b, c, d, e, f, g, h, i = m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        return n
    # inverse-transpose == cofactor matrix / det
    cof = ((e * i - f * h), -(d * i - f * g), (d * h - e * g),
           -(b * i - c * h), (a * i - c * g), -(a * h - b * g),
           (b * f - c * e), -(a * f - c * d), (a * e - b * d))
    v = tuple((cof[r * 3] * n[0] + cof[r * 3 + 1] * n[1] + cof[r * 3 + 2] * n[2]) / det for r in range(3))
    length = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / length for x in v)


def _collect_dobjs(reader, report):
    dobjs = []
    for off in report.joint_offsets:
        d = reader.ptr(off + 0x10)
        while d is not None:
            dobjs.append(d); d = reader.ptr(d + 4)
    return dobjs


def _pobjs_of(reader, dobj):
    out = []; p = reader.ptr(dobj + 0xC)
    while p is not None:
        out.append(p); p = reader.ptr(p + 4)
    return out


def _copy_tobj_head(b, tobj_off):
    """A private copy of the new diffuse TObj, so one host's padding chain does not leak into another's."""
    off = b.add(b.objects[tobj_off], 4)
    for field in (0x4C, 0x58):   # image, TEV (pointer values are already in the bytes)
        if tobj_off + field in b.relocations:
            b.pointer(off, field, None)
    return off


def write_costume(base_costume, output, rigged, texture_rgba, texture_size, *, root_symbol, host_dobjs=(0,), template_dobj=0):
    """Write ``rigged`` into a copy of ``base_costume``; returns a report dict."""
    reader = HsdReader(base_costume); report = reader.joints(root_symbol)
    raw = bytearray(reader.raw); data_size = reader.info.data_size; base = 0x20
    joint_off = report.joint_offsets
    for joint in {j for w in rigged.weights for j, _ in w}:
        if not report.joints[joint].envelope_matrix:
            raise FighterImportError(f"joint {joint} has no inverse bind matrix; it cannot drive an envelope")
    dobjs = _collect_dobjs(reader, report)
    if any(h >= len(dobjs) for h in host_dobjs):
        raise FighterImportError("host DObj index out of range")
    b = _Builder(data_size)

    # HSD SetupEnvelopeModelMtx: with no model-node matrix (skeleton-root
    # owner), a single-joint envelope uses jobj->mtx without envelopemtx, so
    # those vertices are stored in that joint's bind-local space. Blended
    # envelopes use sum(w * mtx * envelopemtx) and stay in model space.
    positions, normals = [], []
    for p, n, w in zip(rigged.positions, rigged.normals, rigged.weights):
        if len(w) == 1:
            e = report.joints[w[0][0]].envelope_matrix  # 3x4 row-major inverse bind
            p = tuple(e[r * 4] * p[0] + e[r * 4 + 1] * p[1] + e[r * 4 + 2] * p[2] + e[r * 4 + 3] for r in range(3))
            n = _inverse_transpose_apply(e, n)
        positions.append(p); normals.append(n)
    # Shared vertex streams (float, indexed16).
    pos_off = b.add(b"".join(struct.pack(">fff", *p) for p in positions))
    nrm_off = b.add(b"".join(struct.pack(">fff", *n) for n in normals))
    uv_off = b.add(b"".join(struct.pack(">ff", u, v) for u, v in rigged.uvs))
    if len(rigged.positions) > 0xFFFF:
        raise FighterImportError("too many vertices for 16-bit indices")

    def desc_entry(attr, typ, cnt, comp, stride, target):
        return struct.pack(">IIIIBBHI", attr, typ, cnt, comp, 0, 0, stride, target or 0)
    vtx = (desc_entry(GX_VA_PNMTXIDX, GX_DIRECT, 0, 0, 0, 0) + desc_entry(GX_VA_POS, GX_INDEX16, 1, GX_F32, 12, pos_off)
           + desc_entry(GX_VA_NRM, GX_INDEX16, 0, GX_F32, 12, nrm_off) + desc_entry(GX_VA_TEX0, GX_INDEX16, 1, GX_F32, 8, uv_off)
           + desc_entry(GX_VA_NULL, 0, 0, 0, 0, 0))
    vtx_off = b.add(vtx)
    for i, target in ((1, pos_off), (2, nrm_off), (3, uv_off)):
        b.pointer(vtx_off, i * 24 + 20, target)

    # Envelope lists (shared across PObjs).
    env_lists = {}
    def envelope_list(weights):
        if weights not in env_lists:
            payload = b"".join(struct.pack(">If", joint_off[j], w) for j, w in weights) + b"\0" * 8
            off = b.add(payload, 4)
            for k in range(len(weights)):
                b.pointer(off, k * 8, joint_off[weights[k][0]])
            env_lists[weights] = off
        return env_lists[weights]

    # Group triangles into PObjs of at most 10 distinct envelopes.
    groups = []; current = []; envs = []
    order = sorted(range(len(rigged.triangles)), key=lambda t: rigged.weights[rigged.triangles[t][0]])
    for t in order:
        need = [rigged.weights[v] for v in rigged.triangles[t]]
        merged = envs + [e for e in dict.fromkeys(need) if e not in envs]
        if len(merged) > MAX_ENVELOPES_PER_POBJ and current:
            groups.append((current, envs)); current, envs = [], []
            merged = list(dict.fromkeys(need))
        current.append(t); envs = merged
    if current:
        groups.append((current, envs))

    pobj_offsets = []
    for tris, group_envs in groups:
        slot = {e: i for i, e in enumerate(group_envs)}
        dl = bytearray()
        for start in range(0, len(tris), 0x5000):
            chunk = tris[start:start + 0x5000]
            dl += bytes((GX_TRIANGLES,)) + struct.pack(">H", 3 * len(chunk))
            for t in chunk:
                for v in rigged.triangles[t]:
                    dl += bytes((slot[rigged.weights[v]] * 3,)) + struct.pack(">HHH", v, v, v)
        dl.append(0); blocks = (len(dl) + 31) // 32; dl += b"\0" * (blocks * 32 - len(dl))
        if blocks > 0xFFFF:
            raise FighterImportError("display list too large")
        dl_off = b.add(dl)
        table = b"".join(struct.pack(">I", envelope_list(e)) for e in group_envs) + b"\0\0\0\0"
        table_off = b.add(table, 4)
        for k, e in enumerate(group_envs):
            b.pointer(table_off, k * 4, env_lists[e])
        pobj = bytearray(struct.pack(">IIIHHII", 0, 0, vtx_off, POBJ_ENVELOPE | 0x0001, blocks, dl_off, table_off))
        pobj_off = b.add(pobj, 4)
        b.pointer(pobj_off, 8, vtx_off); b.pointer(pobj_off, 0x10, dl_off); b.pointer(pobj_off, 0x14, table_off)
        pobj_offsets.append(pobj_off)
    # link the PObj chain
    for a, nxt in zip(pobj_offsets, pobj_offsets[1:]):
        b.objects[a] = b.objects[a][:4] + struct.pack(">I", nxt) + b.objects[a][8:]
        b.pointer(a, 4, nxt)

    # Material: copy of the template DObj's MObj/TObj with a new CMPR image.
    template_mobj = reader.ptr(dobjs[template_dobj] + 8)
    template_tobj = reader.ptr(template_mobj + 8) if template_mobj is not None else None
    if template_tobj is None:
        raise FighterImportError("template DObj has no textured material")
    width, height = texture_size
    image_data_off = b.add(encode_cmpr(texture_rgba, width, height))
    image_off = b.add(struct.pack(">IHHIIff", image_data_off, width, height, 14, 0, 0.0, 0.0), 4)
    b.pointer(image_off, 0, image_data_off)
    tobj = bytearray(reader.data[template_tobj:template_tobj + 0x5C])
    struct.pack_into(">I", tobj, 0, 0); struct.pack_into(">I", tobj, 4, 0)
    struct.pack_into(">II", tobj, 0x34, 1, 1)            # wrap REPEAT/REPEAT
    tobj[0x3C] = 1; tobj[0x3D] = 1                        # repeat 1x1
    struct.pack_into(">III", tobj, 0x4C, image_off, 0, 0)  # image, no tlut, no lod
    tev = reader.ptr(template_tobj + 0x58)
    tobj_off = b.add(tobj, 4)
    b.pointer(tobj_off, 0x4C, image_off)
    if tev is not None:
        b.pointer(tobj_off, 0x58, tev)
    else:
        struct.pack_into(">I", tobj, 0x58, 0); b.objects[tobj_off] = bytes(tobj)
    material = bytearray(reader.data[reader.ptr(template_mobj + 0xC):reader.ptr(template_mobj + 0xC) + 20])
    material[0:12] = bytes((0xFF, 0xFF, 0xFF, 0xFF, 0xB3, 0xB3, 0xB3, 0xFF, 0x40, 0x40, 0x40, 0xFF))  # diffuse, ambient, specular
    material_off = b.add(material, 4)
    pe = reader.ptr(template_mobj + 0x14)
    black = None

    def host_mobj(host):
        # Costume texture animations (eyes, mouths) name their TObj by its
        # index across every DObj's TObj chain (ftparts.c ftParts_80075240),
        # so the host keeps its original TObj count: extra TObjs (DK's and
        # Mewtwo's specular maps) are kept, pointed at a black image.
        nonlocal black
        first = tobj_off; prev = tobj_off
        original = reader.ptr(reader.ptr(dobjs[host] + 8) + 8) if reader.ptr(dobjs[host] + 8) is not None else None
        extra = []
        while original is not None:
            extra.append(original); original = reader.ptr(original + 4)
        for src in extra[1:]:
            if black is None:
                black_data = b.add(b"\0" * 32)
                black = b.add(struct.pack(">IHHIIff", black_data, 8, 8, 14, 0, 0.0, 0.0), 4)
                b.pointer(black, 0, black_data)
            pad = bytearray(reader.data[src:src + 0x5C])
            struct.pack_into(">II", pad, 0, 0, 0)
            struct.pack_into(">III", pad, 0x4C, black, 0, 0)
            pad_tev = reader.ptr(src + 0x58)
            struct.pack_into(">I", pad, 0x58, pad_tev or 0)
            pad_off = b.add(pad, 4)
            b.pointer(pad_off, 0x4C, black)
            if pad_tev is not None:
                b.pointer(pad_off, 0x58, pad_tev)
            if prev == tobj_off:
                first = _copy_tobj_head(b, tobj_off)
                prev = first
            b.objects[prev] = b.objects[prev][:4] + struct.pack(">I", pad_off) + b.objects[prev][8:]
            b.pointer(prev, 4, pad_off); prev = pad_off
        mobj = bytearray(reader.data[template_mobj:template_mobj + 0x18])
        struct.pack_into(">I", mobj, 0, 0); struct.pack_into(">II", mobj, 8, first, material_off)
        mobj_off = b.add(mobj, 4)
        b.pointer(mobj_off, 8, first); b.pointer(mobj_off, 0xC, material_off)
        if pe is not None:
            b.pointer(mobj_off, 0x14, pe)
        return mobj_off

    # Empty every original PObj, then point the host DObjs at the new chain.
    empty_off = b.add(b"\0" * 32)
    for d in dobjs:
        for p in _pobjs_of(reader, d):
            struct.pack_into(">H", raw, base + p + 0xE, 1)
            struct.pack_into(">I", raw, base + p + 0x10, empty_off)
    for h in host_dobjs:
        struct.pack_into(">II", raw, base + dobjs[h] + 8, host_mobj(h), pobj_offsets[0])

    objects = {0: bytes(raw[base:base + data_size])}
    for off, payload in b.objects.items():
        buf = bytearray(payload)
        objects[off] = bytes(buf)
    # Fill in pointer values for new-object relocations (targets already encoded above).
    result = serialize_hsd_graph(bytes(raw), objects, relocations=tuple(b.relocations), alignment=32)
    validate_hsd_archive(result); validate_hsd_relocations(result)
    destination = Path(output); destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(result)
    return {"pobjs": len(pobj_offsets), "envelopes": len(env_lists), "vertices": len(rigged.positions),
            "triangles": len(rigged.triangles), "hosts": list(host_dobjs), "size": len(result)}


# ------------------------------------------------------------------ project API

def _box_resize(rgba, width, height, size):
    """Area-average an RGBA8 image down to ``size`` x ``size`` (dependency-free)."""
    out = []
    for y in range(size):
        y0, y1 = y * height // size, max(y * height // size + 1, (y + 1) * height // size)
        for x in range(size):
            x0, x1 = x * width // size, max(x * width // size + 1, (x + 1) * width // size)
            acc = [0, 0, 0, 0]; n = 0
            for yy in range(y0, y1):
                row = yy * width
                for xx in range(x0, x1):
                    i = (row + xx) * 4
                    for k in range(4): acc[k] += rgba[i + k]
                    n += 1
            out.append(tuple(v // n for v in acc))
    return out


def _rotate_y(points, degrees):
    a = math.radians(degrees); c, s = math.cos(a), math.sin(a)
    return [(x * c + z * s, y, -x * s + z * c) for x, y, z in points]


def resolve_base_rig(rig, base_costume, *, fighter_data=None, common_data=None):
    """Resolve a rig's base-fighter bindings against the user's files.

    ``root_symbol``, ``host_dobjs`` and ``template_dobj`` may be ``"auto"`` (or
    absent for ``root_symbol``). The segment table is the hand-measured
    ``BASE_SEGMENTS`` row set when one exists and ``segments`` is not
    ``"auto"``; otherwise it is derived from ``common_data`` (``PlCo.dat``).
    Returns ``(table_or_None, joints, root_symbol, hosts, template, notes)``.
    """
    from . import base_skeleton
    reader = HsdReader(base_costume)
    root_symbol = rig.get("root_symbol")
    if not root_symbol or root_symbol == "auto":
        root_symbol = next((k for k in reader.publics if k.endswith("_joint") and "matanim" not in k), None)
        if root_symbol is None:
            raise FighterImportError("costume has no *_joint public symbol")
    report, joints = base_bind_positions(base_costume, root_symbol)
    base_fighter = rig["base_fighter"]; notes = []
    table = None
    if rig.get("segments") == "auto" or base_fighter not in BASE_SEGMENTS:
        if common_data is None:
            raise FighterImportError(f"{base_fighter} needs the disc's PlCo.dat to derive its skeleton map")
        parts = base_skeleton.read_parts_map(common_data, base_fighter)
        table = base_skeleton.auto_segments(parts, report.joints, joints)
        notes.extend(table.notes)
    hosts = rig.get("host_dobjs", "auto")
    if hosts == "auto":
        referenced = base_skeleton.referenced_dobjs(fighter_data) if fighter_data is not None else set()
        if fighter_data is None:
            notes.append("no fighter data given: visibility lookups unknown")
        hosts, why = base_skeleton.auto_hosts(reader, report, referenced)
        notes.append(f"hosts {list(hosts)} ({why})")
    hosts = tuple(int(h) for h in hosts)
    template = rig.get("template_dobj", "auto")
    template = base_skeleton.auto_template(reader, report, hosts) if template == "auto" else int(template)
    return table, joints, root_symbol, hosts, template, notes


def import_project_model(project, base_costume, output, *, fighter_data=None, common_data=None):
    """Rig ``project``'s glTF (per ``rig.json``) into a copy of the base costume.

    ``fighter_data`` (the base ``PlXx.dat``) and ``common_data`` (``PlCo.dat``)
    are needed for rigs that resolve their bindings automatically.
    """
    import hashlib
    from .hsd_texture import decode_png_rgba
    project = Path(project)
    character = json.loads((project / "character.json").read_text(encoding="utf-8"))
    rig = json.loads((project / "rig.json").read_text(encoding="utf-8"))
    model = Path(rig["model"]).expanduser()
    model = model if model.is_absolute() else (project / model)
    if not model.is_file():
        raise FighterImportError(f"model not found: {model}")
    mesh = load_gltf_mesh(model)
    turn = float(rig.get("rotate_y_degrees", 0.0))
    if turn:
        mesh.positions = _rotate_y(mesh.positions, turn); mesh.normals = _rotate_y(mesh.normals, turn)
    base_fighter = rig["base_fighter"]
    table, joints, root_symbol, hosts, template, notes = resolve_base_rig(rig, base_costume, fighter_data=fighter_data, common_data=common_data)
    rigged, info = retarget_mesh(mesh, rig["landmarks"], joints, base_fighter,
                                 segment_overrides=rig.get("segment_overrides"), segment_scale=rig.get("segment_scale"),
                                 vertex_offsets=rig.get("vertex_offsets"), joint_rotations=rig.get("joint_rotations"),
                                 table=table, segment_modes=rig.get("segment_modes"), segment_ranges=rig.get("segment_ranges"),
                                 rigid=rig.get("rigid"), scale_factor=float(rig.get("scale_factor", 1.0)))
    if mesh.texture is None:
        raise FighterImportError("model has no base color texture")
    png = decode_png_rgba(mesh.texture.read_bytes())
    size = int(rig.get("texture_size", 512))
    pixels = _box_resize(png["rgba"], png["width"], png["height"], size)
    result = write_costume(base_costume, output, rigged, pixels, (size, size), root_symbol=root_symbol,
                           host_dobjs=hosts, template_dobj=template)
    raw = Path(output).read_bytes()
    report = {"character": character["id"], "base_fighter": base_fighter, "delivery": "tier-b-costume-replacement",
              "base_sha256": hashlib.sha256(Path(base_costume).read_bytes()).hexdigest(),
              "output_sha256": hashlib.sha256(raw).hexdigest(), "scale": round(info["scale"], 6), **result,
              "root_symbol": root_symbol, "template": template, "segment_source": "derived" if table is not None else "measured",
              "notes": notes, "model_credit": rig.get("credit"), "game_integration": False}
    Path(str(output) + ".costume.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return Path(output), report
