"""Give a fighter another fighter's special moves (with PascalPatch's move-graft plugin).

A special move is the donor's own game code, so the character file alone can't
carry it. The build does the data half, and PascalPatch's native ``move-graft``
plugin does the code half at run time:

* here, the donor's actions for that special (animation retargeted onto the
  host skeleton, action script with hitboxes remapped) are appended to the
  host's action table, and the donor's special attribute block is copied out;
* in game, the plugin points the host's special entry at the donor's code and
  gives it a motion-state table whose animation ids point at those new actions
  (see PascalPatch ``plugins/move-graft``).

Everything is read from the user's own disc: which motion states make up a
special (the move id each state carries: 0x12 neutral, 0x13 side, 0x14 up,
0x15 down) and which action each plays come from the DOL's per-fighter motion
state tables, so no game data is kept in this repository.
"""
from __future__ import annotations

import struct
from pathlib import Path

from .fighter_moves import ACTION_ENTRY, FighterData, command_words
from .hsd_animation import scan_figatree
from .move_transplant import (OP_HITBOX, OP_HITBOX_SIZE, Skeleton, _mt, _mv, _rot, bone_map, encode_figatree,
                              retarget_clip)

# Internal fighter kinds (decomp ft/forward.h FighterKind) for the playable slots.
KINDS = {
    "mario": 0, "fox": 1, "captain-falcon": 2, "donkey-kong": 3, "kirby": 4, "bowser": 5, "link": 6, "sheik": 7,
    "ness": 8, "peach": 9, "ice-climbers": 10, "pikachu": 12, "samus": 13, "yoshi": 14, "jigglypuff": 15,
    "mewtwo": 16, "luigi": 17, "marth": 18, "zelda": 19, "young-link": 20, "dr-mario": 21, "falco": 22,
    "pichu": 23, "mr-game-and-watch": 24, "ganondorf": 25, "roy": 26,
}
SLOTS = {"special_neutral": ("n", 0x12), "special_side": ("s", 0x13), "special_up": ("hi", 0x14), "special_down": ("lw", 0x15)}
STATE_TABLES = 0x803C12E0     # ftData_CharacterStateTables: MotionState* per kind
STATE_BASE, STATE_SIZE = 341, 0x20
NO_ANIM = 0xFFFFFFFF

# Script opcodes kept when a special's script is copied: control flow, hitboxes, the flags and
# variables special-move code reads (cmd_vars, throw flags, interrupt), airborne/ledge state,
# jab flags, throw hitboxes, rumble and camera, smash charge. Effects, sounds, model and
# texture visibility, per-bone hurtbox state and wind are fighter-specific and become no-ops.
SPECIAL_KEEP = {0, 1, 2, 3, 4, 8, 9} | set(range(11, 17)) | set(range(19, 28)) | {29, 30, 34, 35} \
    | set(range(42, 50)) | {51, 53, 56, 57}
OP_SUBROUTINE, OP_RETURN, OP_GOTO = 7, 6, 5
OP_SET_CMD_VAR = 19

# Specials that spawn fighter items (projectiles, props) need the donor's article data, which is
# only in memory when the donor itself is in the match; spawning one without it hangs the game.
# Grafts don't carry articles yet, so these are refused at build time.
NEEDS_ARTICLES = {
    ("mario", "special_neutral"), ("dr-mario", "special_neutral"), ("luigi", "special_neutral"),
    ("fox", "special_neutral"), ("falco", "special_neutral"),
    ("link", "special_neutral"), ("link", "special_side"), ("link", "special_down"),
    ("young-link", "special_neutral"), ("young-link", "special_side"), ("young-link", "special_down"),
    ("samus", "special_neutral"), ("samus", "special_down"), ("peach", "special_neutral"),
    ("peach", "special_down"), ("ness", "special_neutral"), ("ness", "special_side"), ("ness", "special_up"),
    ("pikachu", "special_side"), ("pikachu", "special_down"), ("pichu", "special_down"),
    ("yoshi", "special_up"), ("yoshi", "special_neutral"), ("kirby", "special_down"),
    ("sheik", "special_neutral"), ("sheik", "special_side"), ("zelda", "special_side"),
    ("mewtwo", "special_neutral"), ("mr-game-and-watch", "special_neutral"), ("mr-game-and-watch", "special_side"),
    ("ice-climbers", "special_neutral"), ("ice-climbers", "special_up"), ("bowser", "special_neutral"),
    ("donkey-kong", "special_neutral"),
}
# Script commands dropped from a donor's special because the host can't honour them. Fox's and
# Falco's side special set cmd_vars[2] to spawn the afterimage item (a model of the donor).
DROP_CMD_VARS = {("fox", "special_side"): {2}, ("falco", "special_side"): {2}}


class SpecialGraftError(ValueError):
    pass


class Dol:
    """The disc's main.dol, addressed by guest address."""

    def __init__(self, iso):
        with open(iso, "rb") as f:
            f.seek(0x420); dol_off = struct.unpack(">I", f.read(4))[0]
            f.seek(dol_off); head = f.read(0x100)
            offs = struct.unpack_from(">18I", head, 0); addrs = struct.unpack_from(">18I", head, 0x48)
            sizes = struct.unpack_from(">18I", head, 0x90)
            self.sections = []
            for off, addr, size in zip(offs, addrs, sizes):
                if size:
                    f.seek(dol_off + off); self.sections.append((addr, f.read(size)))

    def u32(self, addr):
        for base, data in self.sections:
            if base <= addr < base + len(data) - 3:
                return struct.unpack_from(">I", data, addr - base)[0]
        raise SpecialGraftError(f"{addr:08X} is outside the DOL")


def special_states(dol, kind, move_id):
    """[(motion state, action index)] of ``kind``'s states that carry ``move_id``."""
    table = dol.u32(STATE_TABLES + 4 * kind); out = []
    for i in range(96):
        entry = table + i * STATE_SIZE
        try:
            anim, mv = dol.u32(entry), dol.u32(entry + 8) >> 24
        except SpecialGraftError:
            break
        if mv == move_id:
            out.append((STATE_BASE + i, anim))
        elif out:
            break
    if not out:
        raise SpecialGraftError(f"kind {kind} has no states with move id {move_id:#x}")
    return out


def special_attributes(data):
    """The fighter's special attribute block (ftData.ext_attr) up to the next object."""
    raw = bytes(data.raw); ext = data.u32(data.root + 4)
    count = struct.unpack_from(">I", raw, 8)[0]; table = 0x20 + data.info.data_size
    targets = {data.root} | {data.u32(struct.unpack_from(">I", raw, table + 4 * i)[0]) for i in range(count)}
    end = min(t for t in targets if t > ext)
    return raw[0x20 + ext:0x20 + end]


def model_scale(data):
    """The fighter's in-game model scale (common attributes +0x8C)."""
    attrs = data.u32(data.root)
    return struct.unpack_from(">f", data.raw, 0x20 + attrs + 0x8C)[0]


def action_count(data, table):
    """Entries in an action table: every real entry's script pointer is relocated."""
    raw = bytes(data.raw); n = struct.unpack_from(">I", raw, 8)[0]; rt = 0x20 + data.info.data_size
    relocated = {struct.unpack_from(">I", raw, rt + 4 * i)[0] for i in range(n)}
    count = 0
    while table + count * ACTION_ENTRY + 12 in relocated:
        count += 1
    return count


def transplant_special_script(src_data, script, src, dst, bmap, drop_cmd_vars=()):
    """Copy a special's script onto the host skeleton, following gotos and subroutines inline."""
    k = dst.hip_height / src.hip_height
    src_rest_rot = [_rot(m) for m in src.world]; dst_rest_rot = [_rot(m) for m in dst.world]
    out = bytearray(); cur = script; stack = []; seen = set()
    for _ in range(4096):
        if cur in seen and not stack:
            break                                   # a loop back: the host action ends here
        seen.add(cur)
        opcode = src_data.raw[0x20 + cur] >> 2
        words = command_words(opcode)
        chunk = bytearray(src_data.raw[0x20 + cur:0x20 + cur + 4 * words])
        if opcode == 0:
            if stack:
                cur = stack.pop(); continue
            break
        if opcode in (OP_GOTO, OP_SUBROUTINE):
            target = src_data.u32(cur + 4)
            if opcode == OP_SUBROUTINE:
                stack.append(cur + 8)
            if target in seen:
                break
            cur = target; continue
        if opcode == OP_RETURN:
            if not stack:
                break
            cur = stack.pop(); continue
        if opcode not in SPECIAL_KEEP or (opcode == OP_SET_CMD_VAR and (chunk[0] & 3) in drop_cmd_vars):
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
            struct.pack_into(">I", chunk, 0, (w0 & 0xFFFF0000) | (int(round((w0 & 0xFFFF) * k)) & 0xFFFF))
        out += chunk; cur += 4 * words
    out += struct.pack(">I", 0)
    return bytes(out)


def grafts_of(moveset):
    """[(move name, slot key, donor fighter)] for moves with a ``graft``."""
    out = []
    for move in moveset.get("moves", []):
        g = move.get("graft")
        if not g:
            continue
        if move.get("slot") not in SLOTS:
            raise SpecialGraftError(f"move {move.get('name')!r}: a graft needs a special slot, not {move.get('slot')!r}")
        if g.get("fighter") not in KINDS:
            raise SpecialGraftError(f"move {move.get('name')!r}: unknown donor fighter {g.get('fighter')!r}")
        if (g["fighter"], move["slot"]) in NEEDS_ARTICLES:
            raise SpecialGraftError(f"move {move.get('name')!r}: {g['fighter']}'s {move['slot'].replace('_', ' ')} spawns items "
                                    "(projectiles or props) that grafts can't carry yet")
        out.append((move.get("name"), move["slot"], g["fighter"]))
    slots = [s for _, s, _ in out]
    if len(set(slots)) != len(slots):
        raise SpecialGraftError("two grafts target the same special slot")
    return out


def graft_specials(host, target_files, data_bytes, aj_bytes, grafts, source_files_for, drivers, dol):
    """Append each graft's donor actions to the host's files.

    ``data_bytes``/``aj_bytes`` are the host's current PlXx.dat/PlXxAJ.dat.
    Returns (new data, new AJ, move-graft config entries, report).
    """
    from .hsd_codegen import serialize_hsd_graph
    dst = Skeleton(target_files["costume"]); data = FighterData(data_bytes); aj = bytearray(aj_bytes)
    raw = bytearray(data.raw); info = data.info
    table = data.u32(data.root + 0x0C)
    count = action_count(data, table)
    prefix = next(n for t, _, n, _ in data.action_tables() if t == "common" and "ACTION_" in n).split("ACTION_")[0] + "ACTION_"
    entries = bytearray(raw[0x20 + table:0x20 + table + count * ACTION_ENTRY])
    cursor = (info.data_size + 31) // 32 * 32 + 64
    objects = {}; relocs = []; config = []; report = []
    new_entries = []                               # (name offset, anim off, anim size, script offset, flags, x14)
    for move_name, slot_key, donor in grafts:
        slot, move_id = SLOTS[slot_key]
        states = special_states(dol, KINDS[donor], move_id)
        files = source_files_for(donor)
        src = Skeleton(files["costume"]); src_data = FighterData(files["data"]); src_aj = Path(files["animations"]).read_bytes()
        clips = {c.archive_offset: c for c in scan_figatree(files["animations"])}
        src_table = src_data.u32(src_data.root + 0x0C)
        # the donor's root motion keeps its in-game distance (Illusion travels as far as Fox's)
        root_scale = model_scale(src_data) / model_scale(data)
        anims = {}; done = {}
        for state, anim in states:
            if anim == NO_ANIM or anim in done:
                if anim in done:
                    anims[str(state)] = done[anim]
                continue
            e = src_table + anim * ACTION_ENTRY
            name_ptr, anim_off, anim_size, script, flags, x14 = struct.unpack_from(">IIIIII", src_data.raw, 0x20 + e)
            short = src_data.cstring(name_ptr).split("ACTION_")[-1].replace("_figatree", "") if name_ptr else f"Action{anim}"
            new_name = f"{prefix}{donor.title().replace('-', '')}{short}_figatree"
            clip = clips.get(anim_off)
            if clip is not None:
                frames, _ = retarget_clip(src, dst, src_aj, clip, drivers, root_scale=root_scale)
                archive, tol = encode_figatree(new_name, frames, len(dst.joints), len(frames))
                while len(aj) % 32:
                    aj.append(0)
                new_anim_off, new_anim_size = len(aj), len(archive); aj += archive
            else:
                new_anim_off, new_anim_size, frames, tol = 0, 0, [], None
            name_obj = cursor; objects[cursor] = new_name.encode("ascii") + b"\0"; cursor += (len(new_name) + 1 + 31) // 32 * 32 + 32
            script_obj = 0
            if script:
                body = transplant_special_script(src_data, script, src, dst, bone_map(src, dst),
                                                 DROP_CMD_VARS.get((donor, slot_key), ()))
                script_obj = cursor; objects[cursor] = body; cursor += (len(body) + 31) // 32 * 32 + 32
            index = count + len(new_entries)
            # the entry's low byte is the owning fighter's kind
            new_entries.append((name_obj, new_anim_off, new_anim_size, script_obj, (flags & ~0xFF) | KINDS[host], x14))
            done[anim] = index; anims[str(state)] = index
            report.append({"move": move_name, "from": f"{donor}:{short}", "action": index, "frames": len(frames),
                           "animation_bytes": new_anim_size, "key_tolerance": tol})
        config.append({"fighter": KINDS[host], "slot": slot, "donor": KINDS[donor],
                       "states": [states[0][0], states[-1][0]], "anims": anims,
                       "attrs": special_attributes(src_data).hex(), "move": move_name})
    # the new action table: the host's entries, then the grafted ones
    new_table = cursor
    for name_obj, a_off, a_size, script_obj, flags, x14 in new_entries:
        entries += struct.pack(">IIIIII", name_obj, a_off, a_size, script_obj, flags, x14)
    objects[new_table] = bytes(entries)
    for i in range(count + len(new_entries)):
        base = i * ACTION_ENTRY
        name_ptr, script = struct.unpack_from(">I", entries, base)[0], struct.unpack_from(">I", entries, base + 12)[0]
        if name_ptr:
            relocs.append(new_table + base)
        if script:
            relocs.append(new_table + base + 12)
    struct.pack_into(">I", raw, 0x20 + data.root + 0x0C, new_table)
    objects[0] = bytes(raw[0x20:0x20 + info.data_size])
    new_data = serialize_hsd_graph(bytes(raw), objects, relocations=relocs, alignment=32)
    return new_data, bytes(aj), config, report
