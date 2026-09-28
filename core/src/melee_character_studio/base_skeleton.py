"""Derive rigging data for any base fighter from the user's own disc files.

Two engine tables make a model importable onto a base fighter without
hand-measured joint indices:

* ``PlCo.dat`` ``ftLoadCommonData`` entry 4 is ``ftPartsTable`` (decomp
  ``ft/fighter.c`` ``Fighter_LoadCommonData``): per fighter kind a
  ``FighterPartsTable {u8* joint_to_part; u8* part_to_joint; u32 parts_num}``
  (``ft/types.h``). ``part_to_joint`` maps the engine's named parts
  (``Fighter_Part`` in ``ft/forward.h``: HipN, BustN, RArmJ, ...) to indices
  in ``fp->parts``. Entry 5 lists per-kind virtual parts that have no JObj
  (``ftParts_8007506C``); they are skipped when parts are matched to joints in
  depth-first order (``ftParts_SetupParts``).
* ``PlXx.dat`` ``ftData+0x8`` is ``FtPartsDesc {u32 model_num; vis_table}``.
  Row 0 of ``vis_table`` holds four ``FtPartsVisLookup`` arrays whose DObj
  indices the engine hides and shows (``ftparts.c`` ``ftParts_8007487C``).
  DObjs that no lookup references are always drawn.

``auto_segments`` turns the part map into the segment table
``fighter_import.retarget_mesh`` consumes; ``auto_hosts`` picks the DObjs the
imported mesh is hosted by. Nothing here embeds or ships game data.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

from .hsd_archive import extract_hsd_publics, validate_hsd_archive


class BaseSkeletonError(ValueError):
    pass


# decomp ft/forward.h enum FighterKind (internal order, not the CSS order).
FIGHTER_KINDS = {
    "mario": 0, "fox": 1, "captain-falcon": 2, "donkey-kong": 3, "kirby": 4, "bowser": 5,
    "link": 6, "sheik": 7, "ness": 8, "peach": 9, "ice-climbers": 10, "pikachu": 12,
    "samus": 13, "yoshi": 14, "jigglypuff": 15, "mewtwo": 16, "luigi": 17, "marth": 18,
    "zelda": 19, "young-link": 20, "dr-mario": 21, "falco": 22, "pichu": 23,
    "mr-game-and-watch": 24, "ganondorf": 25, "roy": 26,
}
KIND_COUNT = 33

# decomp ft/forward.h enum Fighter_Part, plus one part the decomp enum lacks.
# On the GALE01 disc every fighter's part_to_joint/joint_to_part pair has an
# extra entry after RFootJ: the spine joint between HipN and the chest. With
# the decomp's numbering BustN lands on that joint, LArmJ on the shoulder
# and LHandN on the elbow for every fighter, and ThrowN/TransN2 are one short
# (checked against bind positions of all 26 kinds' costumes).
PART_NAMES = (
    "TopN", "TransN", "XRotN", "YRotN", "HipN", "WaistN",
    "LLegJA", "LLegJ", "LKneeJ", "LFootJA", "LFootJ",
    "RLegJA", "RLegJ", "RKneeJ", "RFootJA", "RFootJ",
    "SpineN", "BustN", "LShoulderN", "LShoulderJA", "LShoulderJ", "LArmJ", "LHandN",
    "L1stNa", "L1stNb", "L2ndNa", "L2ndNb", "L3rdNa", "L3rdNb", "L4thNa", "L4thNb", "LThumbNa", "LThumbNb", "LHandNb",
    "NeckN", "HeadN",
    "RShoulderN", "RShoulderJA", "RShoulderJ", "RArmJ", "RHandN",
    "R1stNa", "R1stNb", "R2ndNa", "R2ndNb", "R3rdNa", "R3rdNb", "R4thNa", "R4thNb", "RThumbNa", "RThumbNb", "RHandNb",
    "ThrowN", "TransN2",
)
PART = {name: i for i, name in enumerate(PART_NAMES)}
INVALID = 0xFF
JOBJ_SKELETON_ROOT = 1 << 1


def _u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


# --------------------------------------------------------------- PlCo.dat

@dataclass(frozen=True)
class PartsMap:
    """``Fighter_Part`` -> costume joint index (depth-first) for one kind."""
    kind: int
    parts_num: int
    part_to_joint: tuple  # parts index per Fighter_Part (INVALID when absent)
    virtual_parts: tuple  # parts indices with no JObj

    def joint(self, part_name):
        """Costume joint index of a named part, or ``None`` when absent."""
        index = self.part_to_joint[PART[part_name]] if PART[part_name] < len(self.part_to_joint) else INVALID
        if index == INVALID or index >= self.parts_num or index in self.virtual_parts:
            return None
        return index - sum(1 for v in self.virtual_parts if v < index)


def read_parts_map(common_data, fighter):
    """Read ``fighter``'s part map from the user's ``PlCo.dat``."""
    if fighter not in FIGHTER_KINDS:
        raise BaseSkeletonError(f"unknown base fighter {fighter!r}")
    raw = Path(common_data).read_bytes() if not isinstance(common_data, (bytes, bytearray)) else bytes(common_data)
    info = validate_hsd_archive(raw)
    data = raw[0x20:0x20 + info.data_size]
    size = info.data_size
    root = extract_hsd_publics(raw).get("ftLoadCommonData")
    if root is None:
        raise BaseSkeletonError("PlCo.dat has no ftLoadCommonData symbol")

    def ptr(off):
        if off + 4 > size:
            raise BaseSkeletonError("PlCo.dat pointer out of range")
        value = _u32(data, off)
        return None if value == 0 else value

    kind = FIGHTER_KINDS[fighter]
    tables = ptr(root + 4 * 4)
    if tables is None:
        raise BaseSkeletonError("PlCo.dat has no ftPartsTable")
    entry = ptr(tables + 4 * kind)
    if entry is None or entry + 12 > size:
        raise BaseSkeletonError(f"ftPartsTable has no entry for {fighter}")
    part_to_joint_off = ptr(entry + 4)
    parts_num = _u32(data, entry + 8)
    if part_to_joint_off is None or not 0 < parts_num < 256:
        raise BaseSkeletonError(f"ftPartsTable entry for {fighter} is invalid")
    count = min(len(PART_NAMES), size - part_to_joint_off)
    part_to_joint = tuple(data[part_to_joint_off:part_to_joint_off + count])
    virtual = ()
    skip_tables = ptr(root + 4 * 5)
    if skip_tables is not None and skip_tables + 4 * (kind + 1) <= size:
        skip = ptr(skip_tables + 4 * kind)
        if skip is not None and skip + 8 <= size:
            items = ptr(skip); n = _u32(data, skip + 4)
            if items is not None and n and items + 4 * n <= size:
                virtual = tuple(sorted(data[items + 4 * i] for i in range(n)))
    return PartsMap(kind, parts_num, part_to_joint, virtual)


# ---------------------------------------------------------- segment table

# (segment, source start landmark, source end landmark, parent segment, mode,
#  base start part, base end part candidates, envelope part candidates)
_HUMANOID = (
    ("pelvis", "pelvis", "chest", None, "axial", ("HipN",), ("BustN", "SpineN", "WaistN", "NeckN"), ("SpineN", "WaistN", "HipN")),
    ("chest", "chest", "neck", "pelvis", "axial", ("BustN", "SpineN", "WaistN"), ("NeckN", "HeadN"), ("BustN", "SpineN", "WaistN")),
    ("head", "neck", "head", "chest", "uniform", ("NeckN", "BustN"), ("HeadN",), ("HeadN", "NeckN")),
    ("r_clav", "upper_chest", "r_shoulder", "chest", "uniform", ("RShoulderN",), ("RShoulderJ", "RShoulderJA"), ("RShoulderN",)),
    ("r_upper", "r_shoulder", "r_elbow", "r_clav", "axial", ("RShoulderJ", "RShoulderJA"), ("RArmJ",), ("RShoulderJ", "RShoulderJA")),
    ("r_fore", "r_elbow", "r_wrist", "r_upper", "axial", ("RArmJ",), ("RHandN",), ("RArmJ",)),
    ("r_hand", "r_wrist", "r_hand", "r_fore", "uniform", ("RHandN",), ("R2ndNa", "R3rdNa", "R1stNa", "R4thNa", "RHandNb"), ("RHandN",)),
    ("l_clav", "upper_chest", "l_shoulder", "chest", "uniform", ("LShoulderN",), ("LShoulderJ", "LShoulderJA"), ("LShoulderN",)),
    ("l_upper", "l_shoulder", "l_elbow", "l_clav", "axial", ("LShoulderJ", "LShoulderJA"), ("LArmJ",), ("LShoulderJ", "LShoulderJA")),
    ("l_fore", "l_elbow", "l_wrist", "l_upper", "axial", ("LArmJ",), ("LHandN",), ("LArmJ",)),
    ("l_hand", "l_wrist", "l_hand", "l_fore", "uniform", ("LHandN",), ("L2ndNa", "L3rdNa", "L1stNa", "L4thNa", "LHandNb"), ("LHandN",)),
    ("r_thigh", "r_hip", "r_knee", "pelvis", "axial", ("RLegJ", "RLegJA"), ("RKneeJ",), ("RLegJ", "RLegJA")),
    ("r_shin", "r_knee", "r_ankle", "r_thigh", "axial", ("RKneeJ",), ("RFootJ", "RFootJA"), ("RKneeJ",)),
    ("r_foot", "r_ankle", "r_toe", "r_shin", "uniform", ("RFootJ", "RFootJA"), ("<toe>",), ("RFootJ", "RFootJA")),
    ("l_thigh", "l_hip", "l_knee", "pelvis", "axial", ("LLegJ", "LLegJA"), ("LKneeJ",), ("LLegJ", "LLegJA")),
    ("l_shin", "l_knee", "l_ankle", "l_thigh", "axial", ("LKneeJ",), ("LFootJ", "LFootJA"), ("LKneeJ",)),
    ("l_foot", "l_ankle", "l_toe", "l_shin", "uniform", ("LFootJ", "LFootJA"), ("<toe>",), ("LFootJ", "LFootJA")),
)


@dataclass(frozen=True)
class SegmentTable:
    """Segment rows in ``fighter_import.BASE_SEGMENTS`` layout plus extras.

    ``points`` extends the base joint positions: indices at or beyond the
    joint count are synthetic end points (a toe or fingertip the skeleton does
    not have), never envelope owners.
    """
    rows: tuple
    torso: dict
    points: tuple
    notes: tuple = ()


def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _head_fallback(parts, joints, positions, centre=0.3):
    """NeckN/HeadN from the bind pose when the parts table has neither.

    Ganondorf's table leaves both unmapped although his costume has a neck and
    head chain under BustN. The neck is the centreline child of BustN whose
    subtree reaches highest; the head is that subtree's highest centreline joint.
    """
    if parts.joint("NeckN") is not None or parts.joint("HeadN") is not None:
        return {}
    bust = next((parts.joint(p) for p in ("BustN", "SpineN", "WaistN") if parts.joint(p) is not None), None)
    if bust is None or bust >= len(joints):
        return {}

    def centred(j):
        return abs(positions[j][0]) < centre and joints[j].envelope_matrix

    def top(j):
        best = j if centred(j) else None
        for c in joints[j].children:
            t = top(c)
            if t is not None and (best is None or positions[t][1] > positions[best][1]):
                best = t
        return best

    options = []
    for c in joints[bust].children:
        t = top(c)
        if centred(c) and t is not None and positions[t][1] > positions[bust][1]:
            options.append((positions[t][1], c, t))
    if not options:
        return {}
    _, neck, head = max(options)
    return {"NeckN": neck, "HeadN": head} if head != neck else {"NeckN": neck}


def auto_segments(parts, joints, positions, *, min_length=1e-3):
    """Build a segment table for a base fighter from its part map.

    ``joints`` are ``HsdJoint`` records (for parents, children and inverse
    binds) and ``positions`` their bind-pose world positions.
    """
    points = list(positions); notes = []
    n = len(joints)

    fallback = _head_fallback(parts, joints, positions)
    if fallback:
        notes.append(f"parts table has no NeckN/HeadN; head found from bind pose ({fallback})")

    def resolve(names):
        for name in names:
            j = parts.joint(name)
            if j is None:
                j = fallback.get(name)
            if j is not None and j < n:
                return j
        return None

    def with_envelope(j):
        while j is not None and not joints[j].envelope_matrix:
            j = joints[j].parent
        return j

    def synthetic(point):
        points.append(tuple(point)); return len(points) - 1

    rows = []; present = {}
    for name, src_a, src_b, parent, mode, start_parts, end_parts, owner_parts in _HUMANOID:
        j0 = resolve(start_parts)
        if j0 is None:
            notes.append(f"{name}: base has no {start_parts[0]}; body part folded into {parent}")
            continue
        owner = with_envelope(resolve(owner_parts) if resolve(owner_parts) is not None else j0)
        if owner is None:
            notes.append(f"{name}: no joint with an inverse bind; skipped"); continue
        if end_parts == ("<toe>",):
            kids = [c for c in joints[j0].children if _dist(positions[c], positions[j0]) > min_length]
            if kids:
                j1 = max(kids, key=lambda c: positions[c][2] - positions[j0][2])
            else:
                shin = rows[-1] if rows and rows[-1][0].endswith("shin") else None
                length = _dist(points[shin[3]], points[shin[4]]) * 0.35 if shin else 1.0
                p = positions[j0]; j1 = synthetic((p[0], p[1] - 0.6 * max(p[1], 0.0), p[2] + length))
                notes.append(f"{name}: no toe joint; synthetic toe")
        else:
            j1 = resolve(end_parts)
            if j1 is None or _dist(points[j1], positions[j0]) < min_length:
                parent_row = next((r for r in rows if r[0] == parent), None)
                if parent_row is None:
                    notes.append(f"{name}: no end joint; skipped"); continue
                a, b = points[parent_row[3]], points[parent_row[4]]
                length = max(_dist(a, b), min_length * 10)
                axis = tuple((b[i] - a[i]) / length for i in range(3))
                extent = 0.35 if mode == "uniform" else 0.5
                j1 = synthetic(tuple(positions[j0][i] + axis[i] * length * extent for i in range(3)))
                notes.append(f"{name}: end joint missing or coincident; synthetic end")
        while parent is not None and parent not in present:
            parent = next(r[3] for r in _HUMANOID if r[0] == parent)
        rows.append((name, src_a, src_b, j0, j1, owner, parent, mode))
        present[name] = True
    required = {"pelvis", "chest", "r_thigh", "l_thigh"}
    if not required <= set(present):
        raise BaseSkeletonError(f"base skeleton lacks {sorted(required - set(present))}; it cannot drive a humanoid rig")
    by = {r[0]: r for r in rows}
    up_end = by["head"][3] if "head" in by else by["chest"][4]
    right = (by["r_thigh"][3], by["l_thigh"][3])
    if abs(points[right[0]][0] - points[right[1]][0]) < min_length * 10:
        # Jigglypuff's legs both start at the centre of the body; use the knees
        right = (by["r_thigh"][4], by["l_thigh"][4])
        notes.append("hip joints coincide; torso side axis taken from the knees")
    torso = {"up": (by["pelvis"][3], up_end), "right": right}
    return SegmentTable(tuple(rows), torso, tuple(points), tuple(notes))


# ------------------------------------------------------------- host DObjs

def referenced_dobjs(fighter_data, costume_id=0):
    """DObj indices any visibility lookup of ``PlXx.dat`` references."""
    raw = Path(fighter_data).read_bytes() if not isinstance(fighter_data, (bytes, bytearray)) else bytes(fighter_data)
    info = validate_hsd_archive(raw); data = raw[0x20:0x20 + info.data_size]; size = info.data_size
    roots = [v for k, v in extract_hsd_publics(raw).items() if k.startswith("ftData")]
    if len(roots) != 1:
        raise BaseSkeletonError("fighter data must have exactly one ftData root")

    def ptr(off):
        if off + 4 > size:
            return None
        v = _u32(data, off)
        return v if 0 < v < size else None

    x8 = ptr(roots[0] + 8)
    if x8 is None:
        return set()
    model_num = _u32(data, x8)
    table = ptr(x8 + 4)
    if table is None or not 0 < model_num <= 11:
        return set()
    out = set()
    for row in {0, costume_id}:
        for idx in range(4):
            lookup = ptr(table + row * 16 + idx * 4)
            if lookup is None:
                continue
            for group in range(model_num):
                count = _u32(data, lookup + group * 8); variants = ptr(lookup + group * 8 + 4)
                if variants is None or count > 64:
                    continue
                for v in range(count):
                    k = _u32(data, variants + v * 8); dobjs = ptr(variants + v * 8 + 4)
                    if dobjs is None or k > 128:
                        continue
                    out.update(data[dobjs:dobjs + k])
    return out


def dobj_owners(reader, report):
    """(dobj index, owning joint index, dobj offset) in the engine's order."""
    out = []
    for joint, off in enumerate(report.joint_offsets):
        d = reader.ptr(off + 0x10)
        while d is not None:
            out.append((len(out), joint, d)); d = reader.ptr(d + 4)
    return out


def auto_hosts(reader, report, referenced):
    """DObjs to host an imported mesh: always-visible, skeleton-root-owned.

    Envelope vertices are written for a skeleton-root owner
    (``_HSD_mkEnvelopeModelNodeMtx`` returns NULL only for
    ``JOBJ_SKELETON_ROOT``), so only such DObjs can host the mesh. When every
    candidate is under visibility control, all of them host it: whichever the
    engine shows draws the same mesh.
    """
    owned = [d for d, j, _ in dobj_owners(reader, report) if report.joints[j].flags & JOBJ_SKELETON_ROOT]
    if not owned:
        raise BaseSkeletonError("no DObj is owned by a skeleton-root joint")
    always = [d for d in owned if d not in referenced]
    return (always[:1] if always else owned), ("always-visible" if always else "all skeleton-root DObjs")


TEX_COORD_MASK = 0xF
TEX_LIGHTMAP_DIFFUSE = 1 << 4


def auto_template(reader, report, hosts):
    """A DObj whose material has a plain UV-mapped diffuse texture."""
    entries = dobj_owners(reader, report)
    order = list(hosts) + [d for d, _, _ in entries if d not in hosts]
    fallback = None
    for d in order:
        mobj = reader.ptr(entries[d][2] + 8)
        tobj = reader.ptr(mobj + 8) if mobj is not None else None
        if tobj is None:
            continue
        flags = reader.u32(tobj + 0x40)
        if flags & TEX_COORD_MASK == 0 and flags & TEX_LIGHTMAP_DIFFUSE:
            return d
        fallback = d if fallback is None else fallback
    if fallback is None:
        raise BaseSkeletonError("no DObj has a textured material to copy")
    return fallback
