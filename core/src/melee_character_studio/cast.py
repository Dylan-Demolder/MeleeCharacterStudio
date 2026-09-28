"""Compare a character with Melee's cast: every base fighter's stats and normal attacks.

The cast table is read from the user's own disc (attributes from each ``PlXx.dat``
and frame data from its action scripts, see ``fighter_moves.move_frame_data``) and
cached beside the extracted files. ``review`` ranks a project's stats and moves
against it and lists what is out of the ordinary, so a designer sees "fastest jab
in the game" or "heavier than Bowser" before playtesting.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from .fighter_moves import FighterData, FighterMoveError, find_actions, move_frame_data, resolve_alias, shield_stun
from .game_source import FIGHTER_CODES, cache_root, extract_base_files
from .hsd_animation import scan_figatree
from .knockback import ko_percent, strongest_hit, tune_hit
from .package_runtime import INT_ATTRIBUTES, SLOT_ATTRIBUTE_OFFSETS

CAST_VERSION = 2

# KO percents are measured on Fox from the centre of Final Destination, as players quote them.
KO_TARGET = "fox"
# Kill moves a character's survival is measured against: (fighter, slot)
SURVIVAL_MOVES = [("marth", "smash_forward"), ("fox", "smash_up"), ("captain-falcon", "attack_air_forward"), ("sheik", "attack_air_forward")]

# The normal attacks every fighter has: (slot id, label, action pattern)
NORMALS = [
    ("jab", "Jab", "Attack11"), ("attack_dash", "Dash attack", "AttackDash"),
    ("tilt_forward", "Forward tilt", "AttackS3*"), ("tilt_up", "Up tilt", "AttackHi3"), ("tilt_down", "Down tilt", "AttackLw3"),
    ("smash_forward", "Forward smash", "AttackS4*"), ("smash_up", "Up smash", "AttackHi4"), ("smash_down", "Down smash", "AttackLw4"),
    ("attack_air_neutral", "Neutral air", "AttackAirN"), ("attack_air_forward", "Forward air", "AttackAirF"),
    ("attack_air_back", "Back air", "AttackAirB"), ("attack_air_up", "Up air", "AttackAirHi"), ("attack_air_down", "Down air", "AttackAirLw"),
]
SLOT_LABEL = {s: l for s, l, _ in NORMALS}

# How each stat reads in a sentence, and whether bigger is "more" of the word
ATTRIBUTE_WORDS = {
    "walk_speed": ("walk speed", "faster", "slower"), "dash_speed": ("dash speed", "faster", "slower"),
    "run_speed": ("run speed", "faster", "slower"), "jump_velocity": ("jump", "higher", "lower"),
    "short_hop_velocity": ("short hop", "higher", "lower"), "gravity": ("gravity", "stronger", "weaker"),
    "fall_speed": ("fall speed", "faster", "slower"), "fast_fall_speed": ("fast fall", "faster", "slower"),
    "air_speed": ("air speed", "faster", "slower"), "weight": ("weight", "heavier", "lighter"),
    "size": ("size", "bigger", "smaller"), "ground_friction": ("traction", "more", "less"),
    "jump_squat_frames": ("jump squat", "longer", "shorter"), "air_jump_multiplier": ("double jump", "higher", "lower"),
    "max_jumps": ("jumps", "more", "fewer"), "air_acceleration": ("air acceleration", "more", "less"),
    "air_friction": ("air friction", "more", "less"), "shield_size": ("shield", "bigger", "smaller"),
    "landing_lag": ("landing lag", "more", "less"),
    "landing_lag_nair": ("nair landing lag", "more", "less"), "landing_lag_fair": ("fair landing lag", "more", "less"),
    "landing_lag_bair": ("bair landing lag", "more", "less"), "landing_lag_uair": ("uair landing lag", "more", "less"),
    "landing_lag_dair": ("dair landing lag", "more", "less"),
}
AERIAL_LAG = {"attack_air_neutral": "landing_lag_nair", "attack_air_forward": "landing_lag_fair", "attack_air_back": "landing_lag_bair",
              "attack_air_up": "landing_lag_uair", "attack_air_down": "landing_lag_dair"}


def fighter_attributes(data):
    table = data.u32(data.root)
    return {k: round(struct.unpack_from(">i" if k in INT_ATTRIBUTES else ">f", data.raw, 0x20 + table + off)[0], 4)
            for k, off in SLOT_ATTRIBUTE_OFFSETS.items()}


def _pick(data, pattern):
    """The action a slot uses: the straight version of angled tilts/smashes (AttackS3S), else the first that hits."""
    found = find_actions(data, pattern)
    straight = [x for x in found if x[2].endswith(pattern.rstrip("*") + "S_figatree")]
    for entry in straight + found:
        script = resolve_alias(data, entry[3])
        if any(b["damage"] > 0 for b in data.hitboxes(script)):
            return entry[2], script
    return None, None


def normal_moves(data, clips, attributes):
    """{slot: frame data + damage} for a fighter's normal attacks."""
    out = {}
    for slot, _, pattern in NORMALS:
        try:
            name, script = _pick(data, pattern)
            if not name:
                continue
            short = name.split("ACTION_")[-1].replace("_figatree", "")
            fd = move_frame_data(data, script, clips.get(name), short, attributes)
        except FighterMoveError:
            continue
        if not fd["startup"]:
            continue
        first = [h["damage"] for h in fd["hitboxes"] if h["start"] == fd["startup"] and h["damage"] > 0]
        out[slot] = {"action": short, "startup": fd["startup"], "active": fd["active"], "ready": fd["ready"],
                     "lag_after_hit": fd["lag_after_hit"], "total": fd["total"],
                     "damage": max(h["damage"] for h in fd["hitboxes"] if h["damage"] > 0), "first_damage": max(first),
                     "ko_hit": strongest_hit(data.hitboxes(script))}
    return out


def cast_data(iso, progress=None):
    """Stats and normal attacks of every base fighter, cached per disc image."""
    iso = Path(iso).expanduser().resolve(); st = iso.stat()
    key = hashlib.sha1(f"{iso}|{st.st_size}|{int(st.st_mtime)}|cast{CAST_VERSION}".encode()).hexdigest()[:16]
    path = cache_root() / f"cast-{key}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    fighters = {}
    for fighter in sorted(FIGHTER_CODES):
        if progress:
            progress(fighter)
        files = extract_base_files(iso, fighter)
        data = FighterData(files["data"]); attrs = fighter_attributes(data)
        clips = {c.name: c.frames for c in scan_figatree(files["animations"])}
        fighters[fighter] = {"attributes": attrs, "moves": normal_moves(data, clips, attrs)}
    doc = {"version": CAST_VERSION, "fighters": fighters}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(doc), encoding="utf-8"); tmp.replace(path)
    return doc


def shield_of(move, attributes=None, slot=None):
    """On-shield advantage of a move record (aerials: L-cancelled, hitting just before landing)."""
    stun = shield_stun(move["first_damage"])
    if slot in AERIAL_LAG:
        lag = (attributes or {}).get(AERIAL_LAG[slot])
        return None if lag is None else stun - max(1, int(int(lag) / 2))
    return None if move["lag_after_hit"] is None else stun - move["lag_after_hit"]


def _tuned_damage(damage, tuning):
    if not tuning:
        return damage
    if tuning.get("damage") is not None:
        return int(tuning["damage"])
    if tuning.get("damage_scale") is not None:
        return max(1, round(damage * float(tuning["damage_scale"])))
    return damage


def _slot_tuning(move, pattern):
    for v in move.get("actions") or []:
        if v.get("action") in (pattern, pattern.rstrip("*")) or v.get("action", "").startswith(pattern.rstrip("*")):
            return v.get("tuning") or {}
    return {}


def project_moves(cast, base_fighter, moveset, attributes):
    """The normal attack each slot ends up with in this project: the base fighter's (maybe retuned) or a borrowed one."""
    base = cast["fighters"][base_fighter]["moves"]; out = {}
    by_slot = {m.get("slot"): m for m in moveset.get("moves", [])}
    for slot, label, pattern in NORMALS:
        move = by_slot.get(slot); source = base_fighter; rec = base.get(slot)
        if move and move.get("borrow"):
            source = move["borrow"]["fighter"]
            src = cast["fighters"].get(source, {}).get("moves", {})
            rec = next((m for m in src.values() if m["action"] == move["borrow"]["action"]), None)
        if not rec:
            continue
        rec = dict(rec)
        tuning = _slot_tuning(move, pattern) if move else {}
        rec["damage"] = _tuned_damage(rec["damage"], tuning); rec["first_damage"] = _tuned_damage(rec["first_damage"], tuning)
        rec["ko_hit"] = tune_hit(rec.get("ko_hit"), tuning)
        rec.update({"slot": slot, "label": label, "source": source, "name": (move or {}).get("name") or label,
                    "changed": bool(move and (move.get("borrow") or tuning))})
        rec["shield"] = shield_of(rec, attributes, slot)
        out[slot] = rec
    return out


def _rank(value, values, low_is_first=False):
    """1-based place of ``value`` among ``values`` (1 = biggest, or smallest with ``low_is_first``)."""
    return 1 + sum(1 for v in values if (v < value if low_is_first else v > value))


def _label(fighter):
    from .studio_server import fighter_label   # local: studio_server imports this module
    return fighter_label(fighter)


def review(cast, base_fighter, attributes, moveset, base_attributes=None):
    """Rank a project's stats and normal attacks against the cast and list what stands out.

    ``attributes`` are the project's effective values, ``base_attributes`` the base
    fighter's (a finding is only raised for values the project changed).
    """
    fighters = cast["fighters"]; n = len(fighters)
    base_attributes = base_attributes or fighters[base_fighter]["attributes"]
    findings = []; stats = {}
    for key, value in attributes.items():
        column = {f: d["attributes"].get(key) for f, d in fighters.items() if d["attributes"].get(key) is not None}
        if not column:
            continue
        hi = max(column, key=column.get); lo = min(column, key=column.get)
        stats[key] = {"value": value, "rank": _rank(value, column.values()), "of": n + 1,
                      "max": column[hi], "max_fighter": hi, "min": column[lo], "min_fighter": lo}
        if abs(value - base_attributes.get(key, value)) < 1e-6:
            continue
        noun, more, less = ATTRIBUTE_WORDS.get(key, (key.replace("_", " "), "more", "less"))
        if value > column[hi] + 1e-6:
            findings.append({"level": "warn", "area": "stats", "key": key,
                             "message": f"{noun.capitalize()} {_num(value)}: {more} than any fighter in Melee ({_label(hi)} has {_num(column[hi])})."})
        elif value < column[lo] - 1e-6:
            findings.append({"level": "warn", "area": "stats", "key": key,
                             "message": f"{noun.capitalize()} {_num(value)}: {less} than any fighter in Melee ({_label(lo)} has {_num(column[lo])})."})

    base_attrs_all = {f: d["attributes"] for f, d in fighters.items()}
    target = base_attrs_all.get(KO_TARGET)
    mine = project_moves(cast, base_fighter, moveset, attributes)
    moves = []
    for slot, rec in mine.items():
        others = {f: d["moves"][slot] for f, d in fighters.items() if slot in d["moves"]}
        startups = {f: m["startup"] for f, m in others.items()}
        damages = {f: m["damage"] for f, m in others.items()}
        shields = {f: s for f, s in ((f, shield_of(m, base_attrs_all[f], slot)) for f, m in others.items()) if s is not None}
        row = dict(rec)
        row["startup_rank"] = _rank(rec["startup"], startups.values(), low_is_first=True)
        row["damage_rank"] = _rank(rec["damage"], damages.values())
        row["shield_rank"] = _rank(rec["shield"], shields.values()) if rec["shield"] is not None else None
        row["of"] = len(others) + 1
        fastest = min(startups, key=startups.get); strongest = max(damages, key=damages.get)
        row["fastest"] = {"fighter": fastest, "startup": startups[fastest]}
        row["strongest"] = {"fighter": strongest, "damage": damages[strongest]}
        if shields:
            safest = max(shields, key=shields.get); row["safest"] = {"fighter": safest, "shield": shields[safest]}
        kos = {f: k for f, k in ((f, _ko(m, target)) for f, m in others.items()) if k is not None}
        row["ko"] = _ko(rec, target)
        row["ko_rank"] = _rank(row["ko"], kos.values(), low_is_first=True) if row["ko"] is not None else None
        if kos:
            deadliest = min(kos, key=kos.get); row["deadliest"] = {"fighter": deadliest, "ko": kos[deadliest]}
        moves.append(row)
        if not rec["changed"]:
            continue
        what = rec["label"].lower()
        if rec["startup"] < startups[fastest]:
            findings.append({"level": "warn", "area": "moves", "key": slot,
                             "message": f"{rec['name']} hits on frame {rec['startup']}: faster than any {what} in Melee ({_label(fastest)}'s, frame {startups[fastest]})."})
        if rec["damage"] > damages[strongest]:
            findings.append({"level": "warn", "area": "moves", "key": slot,
                             "message": f"{rec['name']} does {rec['damage']}%: more than any {what} in Melee ({_label(strongest)}'s does {damages[strongest]}%)."})
        if rec["shield"] is not None and shields and rec["shield"] > max(shields.values()) and rec["shield"] >= 0:
            findings.append({"level": "warn", "area": "moves", "key": slot,
                             "message": f"{rec['name']} is {rec['shield']:+d} on shield: safer than any {what} in Melee ({_label(row['safest']['fighter'])}'s, {row['safest']['shield']:+d})."})
        if row["ko"] is not None and row.get("deadliest") and row["ko"] < row["deadliest"]["ko"]:
            findings.append({"level": "warn", "area": "moves", "key": slot,
                             "message": f"{rec['name']} KOs Fox from {row['ko']}% at the centre of Final Destination: earlier than any {what} in Melee "
                                        f"({_label(row['deadliest']['fighter'])}'s, {row['deadliest']['ko']}%)."})
    survival = survival_of(cast, attributes, base_fighter)
    if any(abs(attributes.get(k, 0) - base_attributes.get(k, 0)) > 1e-6 for k in ("weight", "gravity", "fall_speed")):
        for s in survival:
            if s["yours"] is None or not s["cast"]:
                continue
            longest = max(s["cast"], key=s["cast"].get); shortest = min(s["cast"], key=s["cast"].get)
            if s["yours"] > s["cast"][longest]:
                findings.append({"level": "warn", "area": "stats", "key": "weight",
                                 "message": f"Survives {s['label']} until {s['yours']}%: longer than any fighter in Melee ({_label(longest)}, {s['cast'][longest]}%)."})
            elif s["yours"] < s["cast"][shortest]:
                findings.append({"level": "warn", "area": "stats", "key": "weight",
                                 "message": f"Dies to {s['label']} from {s['yours']}%: earlier than any fighter in Melee ({_label(shortest)}, {s['cast'][shortest]}%)."})
            break   # one line is enough: the kill moves agree on who lives longer
    return {"stats": stats, "moves": moves, "findings": findings, "survival": survival}


def _ko(move, target):
    return ko_percent(move["ko_hit"], target) if target and move.get("ko_hit") else None


def survival_of(cast, attributes, base_fighter=None):
    """KO percents of Melee's signature kill moves on a character (and on every fighter), centre of FD, no DI."""
    fighters = cast["fighters"]; out = []
    for owner, slot in SURVIVAL_MOVES:
        move = fighters.get(owner, {}).get("moves", {}).get(slot)
        if not move or not move.get("ko_hit"):
            continue
        cast_kos = {f: k for f, k in ((f, ko_percent(move["ko_hit"], d["attributes"])) for f, d in fighters.items()) if k is not None}
        yours = ko_percent(move["ko_hit"], attributes)
        out.append({"label": f"{_label(owner)}'s {SLOT_LABEL[slot].lower()}", "owner": owner, "slot": slot, "yours": yours,
                    "base": cast_kos.get(base_fighter), "cast": cast_kos,
                    "rank": _rank(yours, cast_kos.values()) if yours is not None else None, "of": len(cast_kos) + 1})
    return out


def _num(v):
    return f"{v:.3g}" if isinstance(v, float) and v != int(v) else f"{int(v)}"
