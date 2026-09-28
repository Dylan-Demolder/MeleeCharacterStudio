"""Read and retune fighter action scripts (subactions) in a base PlXx.dat.

Command lengths and the hitbox bit layout come from the decomp
(``src/melee/lb/lbcommand.c``, ``src/melee/ft/ftaction.c`` ``ftAction_803C0870``
and ``src/melee/lb/types.h`` ``spawn_hitbox_0..4``). Scripts are patched in
place: only hitbox fields change, so every pointer and command length stays
valid for the vanilla engine.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from .hsd_archive import extract_hsd_publics, validate_hsd_archive, validate_hsd_relocations


class FighterMoveError(ValueError):
    pass


# Words per command: generic 0-9 (lbcommand.c), fighter 10+ (ftAction_803C0870).
_GENERIC_WORDS = {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 1, 7: 2, 8: 1, 9: 1}
_FIGHTER_WORDS = (5, 5, 1, 1, 1, 1, 1, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3, 1, 1, 1, 7, 4, 1, 1, 1, 1,
                  1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3, 3, 2, 1, 4)
OP_HITBOX = 11
ACTION_ENTRY = 0x18
HITBOX_FIELDS = {  # name: (word, shift, bits)
    "damage": (0, 0, 10), "bone": (0, 11, 8), "id": (0, 23, 3),
    "size": (1, 16, 16),
    "angle": (3, 23, 9), "knockback_growth": (3, 14, 9), "weight_set_knockback": (3, 5, 9),
    "base_knockback": (4, 23, 9), "element": (4, 18, 5), "shield_damage": (4, 10, 8),
}


def command_words(opcode):
    if opcode < 10:
        return _GENERIC_WORDS[opcode]
    if opcode - 10 >= len(_FIGHTER_WORDS):
        raise FighterMoveError(f"unknown fighter command opcode {opcode}")
    return _FIGHTER_WORDS[opcode - 10]


class FighterData:
    def __init__(self, path_or_bytes):
        if isinstance(path_or_bytes, (bytes, bytearray)):
            self.path = None; self.raw = bytearray(path_or_bytes)
        else:
            self.path = Path(path_or_bytes); self.raw = bytearray(self.path.read_bytes())
        self.info = validate_hsd_archive(bytes(self.raw)); publics = extract_hsd_publics(bytes(self.raw))
        roots = [(k, v) for k, v in publics.items() if k.startswith("ftData")]
        if len(roots) != 1:
            raise FighterMoveError("fighter data must have exactly one ftData root")
        self.root_name, self.root = roots[0]

    def u32(self, off): return struct.unpack_from(">I", self.raw, 0x20 + off)[0]

    def cstring(self, off):
        end = self.raw.index(0, 0x20 + off)
        return self.raw[0x20 + off:end].decode("ascii", "replace")

    def action_tables(self):
        """Yield (table, index, name, script_offset) for common (xC) and special (x14) actions."""
        for table, field in (("common", 0x0C), ("special", 0x14)):
            base = self.u32(self.root + field); index = 0
            while base and base + (index + 1) * ACTION_ENTRY <= self.info.data_size:
                entry = base + index * ACTION_ENTRY
                name_ptr = self.u32(entry); script = self.u32(entry + 0x0C)
                if name_ptr >= self.info.data_size or script >= self.info.data_size:
                    break
                if table == "special" and index > 0 and name_ptr == 0 and script == 0:
                    break
                name = self.cstring(name_ptr) if name_ptr else ""
                if table == "common" and index > 400:
                    break
                yield table, index, name, script
                index += 1
                if table == "special" and index > 64:
                    break

    def walk(self, script):
        """Return [(offset, opcode, words)] for a linear script until End/Return/Goto."""
        out = []; cur = script
        for _ in range(4096):
            if cur + 4 > self.info.data_size:
                raise FighterMoveError("script runs past the data block")
            opcode = self.raw[0x20 + cur] >> 2; words = command_words(opcode)
            out.append((cur, opcode, words)); cur += 4 * words
            if opcode in (0, 6, 7):  # End, Return, Goto: the linear body stops here
                return out
        raise FighterMoveError("script has no End command")

    def hitboxes(self, script):
        result = []
        for off, opcode, _ in self.walk(script):
            if opcode == OP_HITBOX:
                words = [self.u32(off + 4 * i) for i in range(5)]
                result.append({"offset": off, **{k: (words[w] >> s) & ((1 << b) - 1) for k, (w, s, b) in HITBOX_FIELDS.items()}})
        return result

    def timeline(self, script, frames=None):
        """Frame timing of an action script, simulated the way the engine runs it.

        Mirrors ``ftAction_80073240``: the script first runs on frame 1 with the
        timer at -1, a synchronous timer adds to it, an asynchronous timer sets
        it to ``value - frame`` (the state change has already advanced the
        animation once, so frame_count is the frame number), and commands run
        while it is <= 0. Loops,
        subroutines and gotos are followed. ``frames`` is the animation length;
        without it the script is run for up to 300 frames. Frame numbers are
        1-based, as in community frame data. Assumes the normal animation rate.
        """
        limit = int(frames) if frames else 300
        cur = script; timer = 0.0; stack = []; live = {}; boxes = []; iasa = None
        cmd_var0 = 0; landing = []; steps = 0
        last = limit
        for f in range(1, limit + 1):
            if cur is None or timer == float("inf"):
                break
            timer -= 1
            while cur is not None and timer <= 0:
                steps += 1
                if steps > 20000 or cur + 4 > self.info.data_size:
                    raise FighterMoveError("script does not settle (runaway loop)")
                word = self.u32(cur); op = word >> 26; value = word & 0x3FFFFFF
                nxt = cur + 4 * command_words(op)
                if op == 0:
                    cur = None; last = f; break
                if op == 1:
                    timer += value
                elif op == 2:
                    timer = value - f
                elif op == 3:
                    stack.append(("loop", nxt, value))
                elif op == 4:
                    kind, start, count = stack[-1] if stack and stack[-1][0] == "loop" else (None, None, 0)
                    if kind and count > 1:
                        stack[-1] = ("loop", start, count - 1); nxt = start
                    elif kind:
                        stack.pop()
                elif op == 5:
                    stack.append(("ret", nxt, 0)); nxt = self.u32(cur + 4)
                elif op == 6:
                    while stack and stack[-1][0] != "ret":
                        stack.pop()
                    nxt = stack.pop()[1] if stack else None
                elif op == 7:
                    nxt = self.u32(cur + 4)
                elif op == 8:            # wait for the animation to end
                    timer = float("inf")
                elif op == OP_HITBOX:
                    hid = (word >> 23) & 7
                    if hid in live:
                        live.pop(hid)["end"] = f - 1
                    box = {"id": hid, "damage": word & 0x3FF, "start": f, "end": None}
                    live[hid] = box; boxes.append(box)
                elif op == 15:           # remove one hitbox
                    if value in live:
                        live.pop(value)["end"] = f - 1
                elif op == 16:           # remove every hitbox
                    for box in live.values():
                        box["end"] = f - 1
                    live.clear()
                elif op == 19 and (word >> 24) & 3 == 0:   # cmd var 0: aerials land with lag while it is set
                    if bool(value) != bool(cmd_var0):
                        landing.append((f, bool(value)))
                    cmd_var0 = value
                elif op == 23 and iasa is None:
                    iasa = f
                cur = nxt
        end = int(frames) if frames else last
        for box in live.values():
            box["end"] = end
        boxes = [b for b in boxes if b["end"] is None or b["end"] >= b["start"]]
        active = []
        for b in sorted(boxes, key=lambda b: b["start"]):
            s, e = b["start"], b["end"] if b["end"] is not None else end
            if active and s <= active[-1][1] + 1:
                active[-1][1] = max(active[-1][1], e)
            else:
                active.append([s, e])
        hitting = [b for b in boxes if b["damage"] > 0]
        return {"hitboxes": boxes, "active": active,
                "startup": min((b["start"] for b in hitting), default=None),
                "iasa": iasa, "total": int(frames) if frames else None,
                "lag_windows": _lag_windows(landing, end)}

    def set_hitbox(self, offset, **fields):
        for name, value in fields.items():
            if name not in ("damage", "angle", "knockback_growth", "weight_set_knockback", "base_knockback", "element", "shield_damage", "size"):
                raise FighterMoveError(f"hitbox field {name!r} is not editable")
            word, shift, bits = HITBOX_FIELDS[name]; value = int(round(value))
            if not 0 <= value < (1 << bits):
                raise FighterMoveError(f"{name}={value} does not fit {bits} bits")
            at = 0x20 + offset + 4 * word; current = struct.unpack_from(">I", self.raw, at)[0]
            mask = ((1 << bits) - 1) << shift
            struct.pack_into(">I", self.raw, at, (current & ~mask) | (value << shift))


AERIAL_LANDING = {"AttackAirN": "landing_lag_nair", "AttackAirF": "landing_lag_fair", "AttackAirB": "landing_lag_bair",
                  "AttackAirHi": "landing_lag_uair", "AttackAirLw": "landing_lag_dair"}


def shield_stun(damage):
    """Frames a hard shield stays stunned after hitlag (``ftCo_80092F2C`` with PlCo's constants:
    1.5 * damage * 0.3 + 2, rounded down), checked frame by frame in game."""
    return int(0.45 * damage + 2)


def move_frame_data(data, script, frames, short=None, attributes=None):
    """Timing summary of one action, as the game plays it at normal speed.

    ``ready`` is the first frame the player can act (the IASA frame, or the
    frame the animation ends). ``lag_after_hit`` counts the frames the attacker
    is still stuck after its first hit, which is what shield and hit advantage
    are measured against: on shield, advantage = shield_stun(damage) -
    lag_after_hit for grounded moves. Aerials use their landing lag instead
    (``landing``), assuming the hit lands just before touching the ground.
    """
    tl = data.timeline(script, frames)
    ready = min((x for x in (tl["iasa"], tl["total"]) if x), default=None)
    out = {"startup": tl["startup"], "active": tl["active"], "iasa": tl["iasa"], "total": tl["total"], "ready": ready,
           "lag_after_hit": ready - tl["startup"] - 1 if ready and tl["startup"] else None,
           "hitboxes": [{k: b[k] for k in ("id", "start", "end", "damage")} for b in tl["hitboxes"]]}
    if short in AERIAL_LANDING:
        lag = None
        if attributes and attributes.get(AERIAL_LANDING[short]) is not None:
            lag = int(attributes[AERIAL_LANDING[short]])
        out["landing"] = {"lag": lag, "l_cancel": max(1, int(lag / 2)) if lag is not None else None,
                          "lag_windows": tl["lag_windows"]}
    return out


def _lag_windows(changes, end):
    """Frame ranges in which an aerial lands with its full landing lag (cmd var 0 set).

    Outside them the landing is an auto-cancel (the normal 4-frame landing).
    """
    out = []; start = None
    for f, on in changes:
        if on and start is None:
            start = f
        elif not on and start is not None:
            out.append([start, f - 1]); start = None
    if start is not None:
        out.append([start, end])
    return out


# decomp lb/forward.h enum HitElement
ELEMENTS = {"normal": 0, "fire": 1, "electric": 2, "slash": 3, "coin": 4, "ice": 5, "nap": 6, "sleep": 7,
            "catch": 8, "ground": 9, "cape": 10, "inert": 11, "disable": 12, "dark": 13, "screw": 14, "lipstick": 15}


def short_name(name):
    """``PlyFox5K_Share_ACTION_SpecialNStart_figatree`` -> ``SpecialNStart``."""
    return name.split("ACTION_")[-1].replace("_figatree", "")


def find_action(data, action):
    """Resolve an action by exact name suffix (e.g. ``SpecialLw``) or ``table:index``."""
    if ":" in action:
        table, index = action.split(":"); index = int(index)
        for t, i, name, script in data.action_tables():
            if t == table and i == index:
                return t, i, name, script
        raise FighterMoveError(f"no action {action}")
    matches = [x for x in data.action_tables() if x[2].endswith("_" + action + "_figatree") or x[2].endswith("ACTION_" + action)]
    if not matches:
        raise FighterMoveError(f"no action named {action!r}")
    return matches[0]


def resolve_alias(data, script):
    """Follow scripts whose whole body is a Goto (air specials that reuse the ground script)."""
    for _ in range(8):
        body = data.walk(script)
        if len(body) != 1 or body[0][1] != 7:
            return script
        script = data.u32(body[0][0] + 4)
    return script


def find_actions(data, pattern):
    """All actions whose short name matches a glob (``SpecialN*``), deduplicated by script."""
    import fnmatch
    seen = set(); out = []
    for entry in data.action_tables():
        t, i, name, script = entry
        if not name or not script or script in seen:
            continue
        if fnmatch.fnmatchcase(short_name(name), pattern):
            seen.add(script); out.append(entry)
    return out


def apply_moveset(data, moveset, *, skipped=None):
    """Apply ``moveset.json`` hitbox tuning; returns a list of change records.

    An exact action name must exist and have a damaging hitbox. A glob
    (``*``, ``?``) tunes every matching action with damaging hitboxes; a glob
    matching nothing tunable is recorded in ``skipped`` instead of failing,
    because special-move action names differ between base fighters.
    """
    changes = []
    for move in moveset.get("moves", []):
        variants = move.get("actions") or ([{"action": move["action"], "tuning": move.get("tuning", {})}] if move.get("action") else [])
        tuned = set()   # scripts this move already tuned, so aliases are not scaled twice
        for variant in variants:
            action = variant["action"]; tuning = variant.get("tuning", {})
            if any(c in action for c in "*?["):
                found = []; covered = False
                for _, _, name, script in find_actions(data, action):
                    script = resolve_alias(data, script)
                    if script in tuned:
                        covered = True; continue
                    if any(b["damage"] > 0 for b in data.hitboxes(script)):
                        tuned.add(script)
                        found.extend(_tune_script(data, move["name"], name, script, tuning))
                if not found and not covered and skipped is not None:
                    skipped.append({"move": move["name"], "action": action,
                                    "reason": "no damaging hitboxes in the matching action scripts (set by fighter code or an item)"})
                changes.extend(found)
            else:
                changes.extend(_apply_variant(data, move["name"], action, tuning, tuned))
    return changes


def _apply_variant(data, move_name, action, tuning, tuned=None):
    _, _, name, script = find_action(data, action)
    script = resolve_alias(data, script)
    if tuned is not None:
        if script in tuned:
            return []
        tuned.add(script)
    # Zero-damage boxes are detection boxes (e.g. Raptor Boost's grab check); never tune them.
    if not any(b["damage"] > 0 for b in data.hitboxes(script)):
        raise FighterMoveError(f"action {action} has no damaging hitboxes to tune")
    return _tune_script(data, move_name, name, script, tuning)


def _tune_script(data, move_name, name, script, tuning):
    changes = []
    boxes = [b for b in data.hitboxes(script) if b["damage"] > 0]
    scale = float(tuning.get("damage_scale", 1.0))
    size_scale = float(tuning.get("size_scale", 1.0))
    kb_scale = float(tuning.get("knockback_scale", 1.0))
    element = tuning.get("element")
    if isinstance(element, str):
        if element not in ELEMENTS:
            raise FighterMoveError(f"unknown hitbox element {element!r}; use one of {sorted(ELEMENTS)}")
        element = ELEMENTS[element]
    for box in boxes:
        fields = {}
        if "damage" in tuning or scale != 1.0:
            fields["damage"] = max(1, round(tuning.get("damage", box["damage"] * scale)))
        if size_scale != 1.0:
            fields["size"] = max(1, min(0xFFFF, round(box["size"] * size_scale)))
        for key in ("angle", "knockback_growth", "base_knockback", "weight_set_knockback", "shield_damage"):
            if key in tuning:
                fields[key] = tuning[key]
        if kb_scale != 1.0:
            for key in ("knockback_growth", "base_knockback"):
                fields[key] = max(0, min(511, round(fields.get(key, box[key]) * kb_scale)))
        if element is not None:
            fields["element"] = element
        if fields:
            data.set_hitbox(box["offset"], **fields)
            changes.append({"move": move_name, "action": name, "hitbox": box["id"],
                            "before": {k: box[k] for k in fields}, "after": fields})
    return changes
