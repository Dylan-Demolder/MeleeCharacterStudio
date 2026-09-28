from __future__ import annotations

import hashlib
import json
import re
import struct
import zipfile
from pathlib import Path

from .hsd_archive import extract_hsd_publics, validate_hsd_archive, validate_hsd_relocations
from .hsd_codegen import serialize_hsd_graph


class PackageRuntimeError(ValueError):
    pass


def _read_members(package):
    path = Path(package).expanduser().resolve()
    if path.is_dir():
        return {item.relative_to(path).as_posix(): item.read_bytes() for item in path.rglob("*") if item.is_file()}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise PackageRuntimeError("character package contains duplicate members")
        return {name: archive.read(name) for name in names}


def _slug(value):
    result = re.sub(r"[^A-Za-z0-9]", "", value)
    if not result:
        raise PackageRuntimeError("character id must contain an ASCII letter or digit")
    return result[0].upper() + result[1:]


def _package_metadata(package):
    members = _read_members(package)
    try:
        character = json.loads(members["character.json"])
        checksums = json.loads(members["checksums.json"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise PackageRuntimeError(f"invalid character package: {exc}") from exc
    if character.get("target_game_version") != "GALE01-1.02":
        raise PackageRuntimeError("character package must target GALE01-1.02")
    if character.get("compatibility") not in {"offline-gameplay", "training-compatible"}:
        raise PackageRuntimeError("character package must declare gameplay compatibility")
    for name, expected in checksums.items():
        if name == "checksums.json" or name not in members:
            raise PackageRuntimeError(f"invalid checksum member: {name}")
        if hashlib.sha256(members[name]).hexdigest() != expected:
            raise PackageRuntimeError(f"checksum mismatch: {name}")
    return character, members


def compose_fighter_hsd(package, base_hsd, output):
    character, members = _package_metadata(package)
    source = Path(base_hsd).expanduser().resolve()
    raw = source.read_bytes()
    info = validate_hsd_archive(raw)
    publics = extract_hsd_publics(raw)
    source_root_name = next((name for name in publics if name.startswith("ftData")), None)
    if source_root_name is None:
        raise PackageRuntimeError("base HSD has no ftData public root")
    source_root = publics[source_root_name]
    if source_root + 0x60 > info.data_size:
        raise PackageRuntimeError("base ftData root is truncated")
    data = raw[0x20:0x20 + info.data_size]
    joint = struct.unpack_from(">I", data, source_root + 0x5C)[0]
    if joint == 0 or joint >= info.data_size:
        raise PackageRuntimeError("base ftData root has no valid joint pointer")
    slug = _slug(character["id"])
    root_name = f"ftData{slug}"
    manifest_name = f"ftPackage{slug}"
    if root_name in publics or manifest_name in publics:
        raise PackageRuntimeError("base HSD already contains the generated package symbols")
    root_offset = info.data_size
    manifest_offset = root_offset + 0x60
    root = bytearray(0x60)
    struct.pack_into(">I", root, 0x5C, joint)
    manifest = json.dumps({"id": character["id"], "version": character["version"], "source_root": source_root_name, "members": sorted(members)}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\0"
    objects = {0: data, root_offset: bytes(root), manifest_offset: manifest}
    result = serialize_hsd_graph(
        raw,
        objects,
        public_symbols={root_name: root_offset, manifest_name: manifest_offset},
        relocations=(root_offset + 0x5C,),
        alignment=4,
    )
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result)
    validate_hsd_archive(result)
    validate_hsd_relocations(result)
    return destination


# Studio attribute name -> byte offset inside ftCo_DatAttrs (decomp
# src/melee/ft/types.h). ftData+0x0 points at this table.
SLOT_ATTRIBUTE_OFFSETS = {
    "walk_speed": 0x08,      # walk_max_vel
    "dash_speed": 0x1C,      # dash_initial_velocity
    "run_speed": 0x28,       # dash_max_velocity
    "jump_velocity": 0x40,   # jump_v_initial_velocity
    "gravity": 0x5C,
    "fall_speed": 0x60,      # terminal_velocity
    "air_speed": 0x6C,       # air_drift_max
    "weight": 0x88,
    "size": 0x8C,            # model_scaling
    "ground_friction": 0x18,
    "jump_squat_frames": 0x38,     # jump_startup_time
    "short_hop_velocity": 0x4C,    # hop_v_initial_velocity
    "air_jump_multiplier": 0x50,   # air_jump_v_multiplier
    "max_jumps": 0x58,             # int: ground jump + air jumps
    "air_acceleration": 0x64,      # air_drift_stick_mul
    "air_friction": 0x70,          # aerial_friction
    "fast_fall_speed": 0x74,       # fast_fall_velocity
    "shield_size": 0x90,           # initial_shield_size
    "landing_lag": 0xE4,           # normal_landing_lag
    "landing_lag_nair": 0xE8,
    "landing_lag_fair": 0xEC,
    "landing_lag_bair": 0xF0,
    "landing_lag_uair": 0xF4,
    "landing_lag_dair": 0xF8,
}
INT_ATTRIBUTES = {"max_jumps"}
FT_CO_DAT_ATTRS_SIZE = 0x184


def _slot_root(raw, info):
    publics = extract_hsd_publics(raw)
    roots = [name for name in publics if name.startswith("ftData")]
    if len(roots) != 1:
        raise PackageRuntimeError(f"base HSD must have exactly one ftData public root, found {roots}")
    name = roots[0]
    offset = publics[name]
    if offset + 0x60 > info.data_size:
        raise PackageRuntimeError("base ftData root is truncated")
    return name, offset


def compose_fighter_slot(package, base_hsd, output):
    """Compose a package into an existing fighter slot, in place.

    The vanilla DOL resolves fighters by their existing ``ftData<Base>``
    symbol, so a Tier B (DOL-untouched) character must keep that root and
    change the data it points at. Only the attribute table is rewritten; the
    archive layout, relocations and symbol tables are byte-identical to the
    base, which keeps every pointer the game follows valid.
    """
    character, members = _package_metadata(package)
    attributes = dict(character.get("attributes", {}))
    scales = character.get("attribute_scales", {})
    if not isinstance(attributes, dict) or not isinstance(scales, dict):
        raise PackageRuntimeError("character attributes and attribute_scales must be objects")
    both = sorted(set(attributes) & set(scales))
    if both:
        raise PackageRuntimeError(f"attributes set both absolutely and by scale: {both}")
    unknown = sorted((set(attributes) | set(scales)) - set(SLOT_ATTRIBUTE_OFFSETS))
    if unknown:
        raise PackageRuntimeError(f"attributes have no verified ftCo_DatAttrs mapping: {unknown}")
    for key, factor in scales.items():
        if not isinstance(factor, (int, float)) or isinstance(factor, bool) or not 0 < factor <= 10:
            raise PackageRuntimeError(f"attribute scale {key} must be a number in (0, 10]")
    source = Path(base_hsd).expanduser().resolve()
    raw = bytearray(source.read_bytes())
    info = validate_hsd_archive(bytes(raw))
    relocations = set(validate_hsd_relocations(bytes(raw)))
    root_name, root = _slot_root(bytes(raw), info)
    data_base = 0x20
    table = struct.unpack_from(">I", raw, data_base + root)[0]
    pointers = _relocated_sources(bytes(raw), info)
    if root not in pointers:
        raise PackageRuntimeError("ftData attribute pointer is not relocated")
    if table == 0 or table + FT_CO_DAT_ATTRS_SIZE > info.data_size:
        raise PackageRuntimeError("ftData attribute table is out of range")
    # scales multiply the base fighter's own value (e.g. "size": 1.2)
    for key, factor in scales.items():
        fmt = ">i" if key in INT_ATTRIBUTES else ">f"
        base_value = struct.unpack_from(fmt, raw, data_base + table + SLOT_ATTRIBUTE_OFFSETS[key])[0]
        attributes[key] = round(base_value * factor) if key in INT_ATTRIBUTES else base_value * factor
    from .attributes import RANGES
    for key, value in attributes.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise PackageRuntimeError(f"attribute {key} must be numeric")
        if key in INT_ATTRIBUTES and value != int(value):
            raise PackageRuntimeError(f"attribute {key} must be a whole number")
        low, high = RANGES[key]
        if not low <= value <= high:
            raise PackageRuntimeError(f"attribute {key} must be between {low} and {high}")
    changed = {}
    for key in sorted(attributes):
        at = data_base + table + SLOT_ATTRIBUTE_OFFSETS[key]
        if at - data_base in pointers:
            raise PackageRuntimeError(f"attribute {key} overlaps a relocated pointer")
        fmt = ">i" if key in INT_ATTRIBUTES else ">f"
        before = struct.unpack_from(fmt, raw, at)[0]
        value = int(attributes[key]) if key in INT_ATTRIBUTES else float(attributes[key])
        struct.pack_into(fmt, raw, at, value)
        changed[key] = {"offset": SLOT_ATTRIBUTE_OFFSETS[key], "base": round(before, 6), "value": round(value, 6)}
        if key in scales:
            changed[key]["scale"] = scales[key]
    moves = []; skipped = []
    moveset = json.loads(members.get("moveset.json") or b"{}")
    if any(m.get("action") or m.get("actions") for m in moveset.get("moves", [])):
        from .fighter_moves import FighterData, apply_moveset
        data = FighterData(bytes(raw)); moves = apply_moveset(data, moveset, skipped=skipped); raw = data.raw
    result = bytes(raw)
    validate_hsd_archive(result)
    if set(validate_hsd_relocations(result)) != relocations:
        raise PackageRuntimeError("slot composition changed relocation targets")
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result)
    report = {
        "character": character["id"], "version": character["version"],
        "display_name": character.get("display_name", character["id"]),
        "slot_root": root_name, "base_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(result).hexdigest(), "attributes": changed, "moves": moves, "skipped_moves": skipped,
        "delivery": "tier-b-slot-replacement", "game_integration": False,
    }
    destination.with_name(destination.name + ".slot.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _relocated_sources(raw, info):
    """Data offsets that hold a relocated pointer (the relocation table)."""
    table = 0x20 + info.data_size
    return {struct.unpack_from(">I", raw, table + 4 * i)[0] for i in range(info.relocations)}
