"""Borrow normal attacks from other fighters.

A normal attack (jab, tilts, smashes, aerials, dash attack) is driven by the
engine's shared attack states, so its behaviour is its action script plus its
animation. Both can be moved between fighters without code changes:

* the animation is retargeted from the source skeleton onto the target one by
  transferring each mapped bone's world-space rotation change from rest, then
  encoded as a new figatree (16-bit keys, reduced to linear segments, within
  the game's 0x8000-byte animation buffer, ``fighter.c`` ``HSD_ObjAllocInit``);
* the action script is copied with hitbox bones and offsets remapped onto the
  target skeleton, and fighter-specific commands (sounds, effects, model
  visibility, texture animation, per-bone collision) replaced by no-ops.

Specials are driven by each fighter's own code in the DOL (charge logic,
projectiles such as Shadow Ball), so they are listed but cannot be borrowed
without DOL-level changes.
"""
from __future__ import annotations

import math
import struct

from .anim_bake import bake_clip
from .fighter_moves import FighterData, FighterMoveError, command_words, find_action, move_frame_data, resolve_alias
from .hsd_animation import scan_figatree
from .hsd_archive import build_hsd_archive, validate_hsd_archive
from .hsd_model import HsdReader, _hsd_world_matrices

ANIM_BUFFER = 0x8000
ACTION_ENTRY = 0x18
# Opcodes kept verbatim: script control, hitbox management, auto-cancel,
# reverse direction, interrupt window, body collision state.
KEEP = {0, 1, 2, 3, 4, 8, 9, 11, 12, 13, 14, 15, 16, 19, 20, 23, 26, 27}
OP_HITBOX, OP_HITBOX_SIZE = 11, 13


def is_normal_attack(short):
    return short.startswith("Attack") and not short.startswith("AttackDashCatch")


class Skeleton:
    def __init__(self, costume):
        reader = HsdReader(costume)
        symbol = next(k for k in reader.publics if k.endswith("_joint") and "matanim" not in k)
        self.report = reader.joints(symbol); self.joints = self.report.joints
        self.rest = [{"position": list(j.position), "rotation": list(j.rotation), "scale": list(j.scale)} for j in self.joints]
        self.world, _ = _hsd_world_matrices(self.joints, self.rest)
        self.pos = [(m[3], m[7], m[11]) for m in self.world]
        self.depth = []
        for j in self.joints:
            self.depth.append(0 if j.parent is None else self.depth[j.parent] + 1)
        self.hip_height = self._hip_height()

    def _hip_height(self):
        # lowest common ancestor of the lowest joint on each side = pelvis
        left = min((i for i, p in enumerate(self.pos) if p[0] > 0.3), key=lambda i: self.pos[i][1])
        right = min((i for i, p in enumerate(self.pos) if p[0] < -0.3), key=lambda i: self.pos[i][1])
        chain = set(); i = left
        while i is not None: chain.add(i); i = self.joints[i].parent
        i = right
        while i not in chain: i = self.joints[i].parent
        self.hip = i
        path = []
        while i is not None: path.append(i); i = self.joints[i].parent
        self.root_chain = list(reversed(path))[1:]   # TopN ... pelvis, without the skeleton root
        return max(self.pos[self.hip][1], 1e-3)

    def normalized(self, i):
        return tuple(c / self.hip_height for c in self.pos[i])

    def usable(self, i):
        return bool(self.joints[i].envelope_matrix)


def _rot(m):
    """Rotation part of a row-major 4x4 (columns normalized to strip scale)."""
    cols = []
    for c in range(3):
        v = (m[c], m[4 + c], m[8 + c]); n = math.sqrt(sum(x * x for x in v)) or 1.0
        cols.append(tuple(x / n for x in v))
    return [[cols[c][r] for c in range(3)] for r in range(3)]


def _mm(a, b): return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
def _mt(a): return [[a[c][r] for c in range(3)] for r in range(3)]
def _mv(a, v): return tuple(sum(a[r][k] * v[k] for k in range(3)) for r in range(3))


def _euler_xyz(m):
    """HSD local rotation is Rz*Ry*Rx; return (x, y, z)."""
    sy = max(-1.0, min(1.0, -m[2][0])); y = math.asin(sy)
    if abs(sy) < 0.99999:
        return math.atan2(m[2][1], m[2][2]), y, math.atan2(m[1][0], m[0][0])
    return math.atan2(-m[1][2], m[1][1]), y, 0.0


def _euler_matrix(x, y, z):
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]; ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]; rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _mm(rz, _mm(ry, rx))


def joint_map(src, dst, dst_joints):
    """Map each target joint in ``dst_joints`` to the best source joint."""
    mapping = {}
    # Root chains (TopN -> ... -> pelvis) differ in length/index between
    # fighters (Mewtwo has an extra joint before TopN), so pair them from the
    # pelvis upward.
    chain = dict(zip(reversed(dst.root_chain), reversed(src.root_chain)))
    for t in dst_joints:
        if not 0 <= t < len(dst.joints):   # synthetic segment ends (Jigglypuff's hands) are not joints
            continue
        if t in chain:
            mapping[t] = chain[t]; continue
        tp = dst.normalized(t); side = 0 if abs(tp[0]) < 0.05 else (1 if tp[0] > 0 else -1)
        cands = []
        for s in range(len(src.joints)):
            if not src.usable(s): continue
            sp = src.normalized(s); sside = 0 if abs(sp[0]) < 0.05 else (1 if sp[0] > 0 else -1)
            if side != sside: continue
            cands.append((math.dist(tp, sp), s))
        if not cands: continue
        best = min(c[0] for c in cands)
        mapping[t] = max((s for d, s in cands if d <= best + 0.03), key=lambda s: src.depth[s])
    return mapping


def bone_map(src, dst):
    """Map every source joint to the nearest usable target joint (same side)."""
    out = {}
    for s in range(len(src.joints)):
        sp = src.normalized(s); side = 0 if abs(sp[0]) < 0.05 else (1 if sp[0] > 0 else -1); best = None
        for t in range(len(dst.joints)):
            if not dst.usable(t): continue
            tp = dst.normalized(t); tside = 0 if abs(tp[0]) < 0.05 else (1 if tp[0] > 0 else -1)
            if side and tside and side != tside: continue
            d = math.dist(sp, tp)
            if best is None or d < best[0]: best = (d, t)
        if best: out[s] = best[1]
    return out


# ------------------------------------------------------------------ animation

def retarget_clip(src, dst, raw_aj, clip, drivers, root_scale=None):
    """Return per-frame target local channels {joint: {obj_type: value}}.

    Translation on the root chain is carried over scaled by the hip-height
    ratio, except the top of the chain (TransN, whose motion moves the fighter
    in game), which uses ``root_scale`` when given: a grafted special keeps
    the donor's travel distance instead of shrinking with the body.
    """
    baked = bake_clip(raw_aj, clip)
    # the root chain always follows the source, or root motion (Illusion's dash) is lost
    mapping = joint_map(src, dst, sorted(set(drivers) | set(dst.root_chain)))
    k = dst.hip_height / src.hip_height
    src_rest_rot = [_rot(m) for m in src.world]; dst_rest_rot = [_rot(m) for m in dst.world]
    dst_local_rest = [_euler_matrix(*r["rotation"]) for r in dst.rest]
    frames = []
    for values in baked:
        local = [{"position": list(r["position"]), "rotation": list(r["rotation"]), "scale": list(r["scale"])} for r in src.rest]
        for node, ch in enumerate(values):
            if node >= len(local): break
            for t, v in ch.items():
                if 1 <= t <= 3: local[node]["rotation"][t - 1] = v
                elif 5 <= t <= 7: local[node]["position"][t - 5] = v
                elif 8 <= t <= 10: local[node]["scale"][t - 8] = v
        sw, _ = _hsd_world_matrices(src.joints, local)
        world_rot = [None] * len(dst.joints); out = {}
        for j, joint in enumerate(dst.joints):
            parent = joint.parent
            prot = world_rot[parent] if parent is not None else [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            if j in mapping:
                s = mapping[j]
                delta = _mm(_rot(sw[s]), _mt(src_rest_rot[s]))
                world_rot[j] = _mm(delta, dst_rest_rot[j])
                lx, ly, lz = _euler_xyz(_mm(_mt(prot), world_rot[j]))
                ch = {1: lx, 2: ly, 3: lz}
                if j in dst.root_chain:
                    scale = root_scale if root_scale is not None and j == dst.root_chain[0] else k
                    for axis in range(3):
                        ch[5 + axis] = dst.rest[j]["position"][axis] + (local[s]["position"][axis] - src.rest[s]["position"][axis]) * scale
                out[j] = ch
            else:
                world_rot[j] = _mm(prot, dst_local_rest[j])
                if j > 0:  # pin to rest so nothing lingers from the previous action
                    out[j] = {1: dst.rest[j]["rotation"][0], 2: dst.rest[j]["rotation"][1], 3: dst.rest[j]["rotation"][2]}
        frames.append(out)
    # unwrap Euler angles so linear keys never interpolate across +-pi
    for j in {j for f in frames for j in f}:
        for t in (1, 2, 3):
            prev = None
            for f in frames:
                if j in f:
                    v = f[j][t]
                    if prev is not None:
                        while v - prev > math.pi: v -= 2 * math.pi
                        while v - prev < -math.pi: v += 2 * math.pi
                    f[j][t] = v; prev = v
    return frames, mapping


def _reduce(values, tol):
    """Greedy linear keyframe reduction; returns [(frame, value)]."""
    keys = [(0, values[0])]; start = 0; n = len(values)
    while start < n - 1:
        end = start + 1
        while end + 1 < n:
            cand = end + 1; ok = True
            for i in range(start + 1, cand):
                a = (i - start) / (cand - start)
                if abs(values[start] + (values[cand] - values[start]) * a - values[i]) > tol: ok = False; break
            if not ok: break
            end = cand
        keys.append((end, values[end])); start = end
    return keys


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n: return bytes(out)


def _encode_track(values, tol):
    keys = _reduce(values, tol)
    peak = max(abs(v) for _, v in keys) or 1.0
    frac = max(0, min(15, int(math.floor(math.log2(32767.0 / peak))))) if peak < 32767 else 0
    count = len(keys); data = bytearray()
    op = 2 if count > 1 else 1  # LIN, or CON for a constant
    header = op | (((count - 1) & 7) << 4)
    if count - 1 >= 8:
        data.append(header | 0x80); data += _varint((count - 1) >> 3)
    else:
        data.append(header)
    frames = len(values)
    for i, (f, v) in enumerate(keys):
        data += struct.pack("<h", max(-32768, min(32767, int(round(v * (1 << frac))))))
        # The final key's wait must be 0: fobj.c turns a non-zero tail wait into
        # one more linear segment (and a lone CON key into p0 = 0).
        data += _varint(keys[i + 1][0] - f if i + 1 < count else 0)
    return bytes(data), 0x20 | frac


def encode_figatree(name, frame_channels, joint_count, frames):
    """Build a figatree archive with root symbol ``name`` that fits the anim buffer."""
    for tol in (0.002, 0.005, 0.01, 0.02, 0.04):
        tracks = []
        for j in range(joint_count):
            for t in (1, 2, 3, 5, 6, 7):
                series = [f.get(j, {}).get(t) for f in frame_channels]
                if series[0] is None: continue
                data, fracv = _encode_track(series, tol if t <= 3 else tol * 4)
                tracks.append((j, t, data, fracv))
        counts = [0] * joint_count
        for j, *_ in tracks: counts[j] += 1
        body = bytearray(); reloc = []
        ad_offsets = []
        for _, _, data, _ in tracks:
            ad_offsets.append(len(body)); body += data
        while len(body) % 4: body.append(0)
        tracks_off = len(body)
        for (j, t, data, fracv), ad in zip(tracks, ad_offsets):
            reloc.append(len(body) + 8)
            body += struct.pack(">HHBBBxI", len(data), 0, t, fracv, 0, ad)
        nodes_off = len(body); body += bytes(counts) + b"\xff"
        while len(body) % 4: body.append(0)
        root = len(body)
        body += struct.pack(">IIfII", 1, 0, float(frames), nodes_off, tracks_off)
        reloc += [root + 12, root + 16]
        archive = build_hsd_archive(bytes(body), relocations=tuple(reloc), publics=((root, name),), version=b"\0\0\0\0")
        if len(archive) <= ANIM_BUFFER - 0x20:
            validate_hsd_archive(archive)
            return archive, tol
    raise FighterMoveError(f"animation {name} does not fit the 0x8000-byte animation buffer")


# ------------------------------------------------------------------ scripts

def transplant_script(src_data, script, src, dst, bmap):
    """Copy a linear action script with bones/offsets remapped; returns bytes."""
    k = dst.hip_height / src.hip_height
    src_rest_rot = [_rot(m) for m in src.world]; dst_rest_rot = [_rot(m) for m in dst.world]
    out = bytearray()
    for off, opcode, words in src_data.walk(script):
        chunk = bytearray(src_data.raw[0x20 + off:0x20 + off + 4 * words])
        if opcode in (5, 6, 7):          # subroutine/return/goto: end the copied body here
            out += struct.pack(">I", 0); return bytes(out)
        if opcode not in KEEP:
            chunk = bytearray(struct.pack(">I", 1 << 26) * words)   # synchronous timer 0 = no-op
        elif opcode == OP_HITBOX:
            w0, w1, w2 = struct.unpack_from(">III", chunk, 0)
            common = (w0 >> 10) & 1; bone = (w0 >> 11) & 0xFF
            if not common:
                tb = bmap.get(bone, bone)
                w0 = (w0 & ~(0xFF << 11)) | ((tb & 0xFF) << 11)
                size = w1 >> 16; z = struct.unpack(">h", struct.pack(">H", w1 & 0xFFFF))[0]
                y = struct.unpack(">h", struct.pack(">H", w2 >> 16))[0]; x = struct.unpack(">h", struct.pack(">H", w2 & 0xFFFF))[0]
                vec = _mv(_mt(dst_rest_rot[tb]), _mv(src_rest_rot[bone], (x, y, z))) if bone < len(src_rest_rot) else (x, y, z)
                clamp = lambda v: max(-32768, min(32767, int(round(v * k))))
                size = max(0, min(0xFFFF, int(round(size * k))))
                w1 = (size << 16) | (clamp(vec[2]) & 0xFFFF); w2 = ((clamp(vec[1]) & 0xFFFF) << 16) | (clamp(vec[0]) & 0xFFFF)
                struct.pack_into(">III", chunk, 0, w0, w1, w2)
        elif opcode == OP_HITBOX_SIZE:
            w0 = struct.unpack_from(">I", chunk, 0)[0]
            size = int(round((w0 & 0xFFFF) * k)) & 0xFFFF
            struct.pack_into(">I", chunk, 0, (w0 & 0xFFFF0000) | size)
        out += chunk
        if opcode == 0:
            return bytes(out)
    out += struct.pack(">I", 0)
    return bytes(out)


# ------------------------------------------------------------------ catalogue

def list_moves(data_path, animations_path):
    data = FighterData(data_path); clips = {c.name: c for c in scan_figatree(animations_path)}; out = []
    for table, index, name, script in data.action_tables():
        if "ACTION_" not in name: continue
        short = name.split("ACTION_")[-1].replace("_figatree", "")
        if not (short.startswith("Attack") or short.startswith("Special")): continue
        try: boxes = [b for b in data.hitboxes(script)] if script else []
        except FighterMoveError: boxes = []
        clip = clips.get(name)
        try: timing = move_frame_data(data, resolve_alias(data, script), clip.frames if clip else None, short) if script else None
        except FighterMoveError: timing = None
        out.append({"action": short, "index": index, "frames": int(round(clip.frames)) if clip else 0, "timing": timing,
                    "hitboxes": [{k: b[k] for k in ("id", "damage", "angle", "knockback_growth", "base_knockback")} for b in boxes],
                    "borrowable": is_normal_attack(short) and clip is not None,
                    "reason": None if is_normal_attack(short) else "Specials are borrowed whole: pick the fighter under Special moves."})
    return out


def borrow_moves(target_files, borrows, source_files_for, drivers):
    """Apply ``borrows`` [{fighter, action, target_action}] to target fighter files.

    Returns (new PlXx.dat bytes, new PlXxAJ.dat bytes, report list).
    """
    from .hsd_codegen import serialize_hsd_graph
    dst = Skeleton(target_files["costume"])
    data = FighterData(target_files["data"]); aj = bytearray(target_files["animations"].read_bytes())
    raw = bytearray(data.raw); info = data.info; objects = {}; relocs = []; report = []
    cursor = (info.data_size + 31) // 32 * 32 + 64
    pending_scripts = []
    for b in borrows:
        files = source_files_for(b["fighter"])
        src = Skeleton(files["costume"]); src_data = FighterData(files["data"]); src_aj = files["animations"].read_bytes()
        _, _, src_name, src_script = find_action(src_data, b["action"])
        clip = next((c for c in scan_figatree(files["animations"]) if c.name == src_name), None)
        if clip is None or not is_normal_attack(b["action"]):
            raise FighterMoveError(f"{b['fighter']} {b['action']} is not a borrowable normal attack")
        _, t_index, t_name, _ = find_action(data, b.get("target_action") or b["action"])
        frames, mapping = retarget_clip(src, dst, src_aj, clip, drivers)
        archive, tol = encode_figatree(t_name, frames, len(dst.joints), len(frames))
        while len(aj) % 32: aj.append(0)
        anim_off = len(aj); aj += archive
        script = transplant_script(src_data, src_script, src, dst, bone_map(src, dst))
        objects[cursor] = script; entry = data.u32(data.root + 0x0C) + t_index * ACTION_ENTRY
        struct.pack_into(">III", raw, 0x20 + entry + 4, anim_off, len(archive), cursor)
        pending_scripts.append(cursor); cursor += (len(script) + 31) // 32 * 32 + 32
        report.append({"from": f"{b['fighter']}:{b['action']}", "to": t_name.split("ACTION_")[-1].replace("_figatree", ""),
                       "frames": len(frames), "animation_bytes": len(archive), "key_tolerance": tol,
                       "mapped_joints": len(mapping), "script_words": len(script) // 4})
    objects[0] = bytes(raw[0x20:0x20 + info.data_size])
    new_data = serialize_hsd_graph(bytes(raw), objects, alignment=32)
    return new_data, bytes(aj), report
