"""Build one character, or a whole roster, from the user's own disc.

``build_character`` is the pipeline the studio's Export button runs:
package -> borrowed moves (``PlXxAJ.dat``) -> slot composition (``PlXx.dat``,
stats + hitbox tuning) -> costume import (``PlXxNr.dat``). ``build_roster``
runs it for every project in a ``roster.json`` and writes a PascalPatch profile
that overlays all of them. Everything read from the disc stays in the user's
cache and output folders; nothing is written back into a repository.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .exporter import export_project
from .fighter_import import BASE_SEGMENTS, FighterImportError, import_project_model, resolve_base_rig
from .game_source import extract_base_files
from .package_runtime import compose_fighter_slot
from .portrait import HEIGHT as PORTRAIT_H, ICON_HEIGHT, ICON_WIDTH, WIDTH as PORTRAIT_W, STOCK_HEIGHT, STOCK_WIDTH, build_icon, build_portrait, build_stock
from .special_graft import Dol, graft_specials, grafts_of


class RosterError(ValueError):
    pass


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def segment_drivers(rig, files):
    """Joints that drive the body (used to retarget borrowed animations)."""
    table, *_ = resolve_base_rig(rig, files["costume"], fighter_data=files["data"], common_data=files.get("common"))
    rows = table.rows if table is not None else BASE_SEGMENTS[rig["base_fighter"]]
    return sorted({j for s in rows for j in (s[3], s[4], s[5])})


def borrows_of(moveset):
    out = []
    for move in moveset.get("moves", []):
        b = move.get("borrow")
        if b:
            out.append({"fighter": b["fighter"], "action": b["action"], "target_action": b.get("target_action") or b["action"]})
    return out


def build_character(project, files, out, *, source_files_for=None, require_model=False, dol=None):
    """Build ``project`` against ``files`` (from ``extract_base_files``) into ``out``.

    Returns a PascalPatch profile ``characters`` entry plus a report. A missing
    model is a warning (the slot keeps its vanilla look) unless
    ``require_model``.
    """
    from .move_transplant import borrow_moves
    project = Path(project).resolve(); out = Path(out); out.mkdir(parents=True, exist_ok=True)
    character = _read(project / "character.json"); moveset = _read(project / "moveset.json"); rig = _read(project / "rig.json")
    slot = rig["base_fighter"]
    if moveset.get("base_fighter", slot) != slot:
        raise RosterError(f"{character['id']}: moveset base_fighter {moveset['base_fighter']!r} differs from rig {slot!r}")
    code = Path(files["data"]).name[:-4]; steps = []; warnings = []
    package = export_project(project, out / f"{character['id']}.melee-character"); steps.append(f"package {package.name}")
    base_data = Path(files["data"]); anim_file = out / f"{code}AJ.dat"; borrows = borrows_of(moveset)
    if borrows:
        if source_files_for is None:
            raise RosterError("borrowed moves need access to the source fighters' files")
        new_data, new_aj, report = borrow_moves(files, borrows, source_files_for, segment_drivers(rig, files))
        base_data = out / f"{code}.borrowed.tmp"; base_data.write_bytes(new_data); anim_file.write_bytes(new_aj)
        (out / f"{code}AJ.dat.anim.json").write_text(json.dumps({"character": character["id"], "borrowed": report,
            "output_sha256": hashlib.sha256(new_aj).hexdigest(), "game_integration": False}, indent=2) + "\n", encoding="utf-8")
        steps += [f"borrowed {r['from']} -> {r['to']} ({r['animation_bytes']} B animation)" for r in report]
    else:
        for stale in (anim_file, out / f"{code}AJ.dat.anim.json"):
            if stale.exists():
                stale.unlink()
    grafts = grafts_of(moveset); graft_file = out / "move-graft.json"
    if grafts:
        if source_files_for is None or dol is None:
            raise RosterError("grafted specials need the disc (donor fighters' files and main.dol)")
        aj_now = anim_file.read_bytes() if anim_file.exists() else Path(files["animations"]).read_bytes()
        new_data, new_aj, config, report = graft_specials(slot, files, Path(base_data).read_bytes(), aj_now, grafts,
                                                          source_files_for, segment_drivers(rig, files), dol)
        grafted = out / f"{code}.grafted.tmp"; grafted.write_bytes(new_data); anim_file.write_bytes(new_aj)
        if base_data != Path(files["data"]):
            Path(base_data).unlink()
        base_data = grafted
        graft_file.write_text(json.dumps({"character": character["id"], "grafts": config}, indent=1) + "\n", encoding="utf-8")
        sidecar = out / f"{code}AJ.dat.anim.json"
        previous = json.loads(sidecar.read_text(encoding="utf-8")).get("borrowed", []) if borrows and sidecar.exists() else []
        sidecar.write_text(json.dumps({"character": character["id"], "borrowed": previous, "grafted": report,
            "output_sha256": hashlib.sha256(new_aj).hexdigest(), "game_integration": False}, indent=2) + "\n", encoding="utf-8")
        steps += [f"grafted {r['move']}: {r['from']} -> action {r['action']} ({r['animation_bytes']} B animation)" for r in report]
    elif graft_file.exists():
        graft_file.unlink()
    fighter = compose_fighter_slot(package, base_data, out / f"{code}.dat")
    if Path(base_data) != Path(files["data"]):
        Path(base_data).unlink()
    slot_report = _read(str(fighter) + ".slot.json")
    steps.append(f"fighter data {code}.dat ({len(slot_report['attributes'])} attributes, {len(slot_report['moves'])} hitbox edits)")
    warnings += [f"move {s['move']!r}: {s['action']} {s['reason']}" for s in slot_report.get("skipped_moves", [])]
    # Added to the game as a new fighter (its base fighter stays) unless the project says to
    # take the base fighter's place.
    entry = {"package": str(package), "slot": slot, "fighter_file": str(fighter),
             "install": "replace" if character.get("install") == "replace" else "new"}
    model = Path(rig.get("model", "")).expanduser()
    model = model if model.is_absolute() else project / model
    costume = out / f"{code}Nr.dat"
    if rig.get("model") and model.is_file():
        _, report = import_project_model(project, files["costume"], costume, fighter_data=files["data"], common_data=files.get("common"))
        steps.append(f"costume {code}Nr.dat ({report['vertices']} vertices, {report['pobjs']} PObjs, {report['segment_source']} skeleton map)")
        warnings += report.get("notes", [])
        entry["costume_file"] = str(costume)
    else:
        message = f"model {model} not found; {slot} keeps its vanilla costume"
        if require_model:
            raise FighterImportError(message)
        warnings.append(message)
        for stale in (costume, Path(str(costume) + ".costume.json")):
            if stale.exists():
                stale.unlink()
    if anim_file.exists():
        entry["animation_file"] = str(anim_file)
    if graft_file.exists():
        entry["move_graft"] = str(graft_file)
    portrait = build_portrait(project, out, character["id"])
    if portrait:
        entry["portrait"] = str(portrait)
        steps.append(f"portrait {portrait.name} ({PORTRAIT_W} x {PORTRAIT_H} select-screen door picture)")
    icon = build_icon(project, out, character["id"])
    if icon:
        entry["icon"] = str(icon)
        steps.append(f"icon {icon.name} ({ICON_WIDTH} x {ICON_HEIGHT} select-screen grid icon)")
    stock = build_stock(project, out, character["id"])
    if stock:
        entry["stock"] = str(stock)
        steps.append(f"stock icon {stock.name} ({STOCK_WIDTH} x {STOCK_HEIGHT}, shown above the damage in a match)")
    return entry, {"character": character["id"], "display_name": character.get("display_name"), "slot": slot,
                   "steps": steps, "warnings": warnings}


def load_roster(path):
    path = Path(path).resolve(); doc = _read(path)
    projects = [(path.parent / name).resolve() for name in doc.get("characters", [])]
    if not projects:
        raise RosterError("roster.json lists no characters")
    slots = {}
    for project in projects:
        if _read(project / "character.json").get("install") != "replace":
            continue   # new fighters: any number can share a base fighter
        slot = _read(project / "rig.json")["base_fighter"]
        if slot in slots:
            raise RosterError(f"{project.name} and {slots[slot].name} both replace {slot}; one character per slot")
        slots[slot] = project
    return doc, projects


def build_roster(roster, iso, out, *, profile=None, only=None, log=print):
    """Build every project in ``roster`` (a ``roster.json``) against ``iso``.

    Writes ``<out>/<id>/`` per character and, if ``profile`` is given, a
    PascalPatch profile JSON with one ``characters`` entry per built slot.
    """
    doc, projects = load_roster(roster)
    out = Path(out).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
    cache = {}

    def files_for(fighter):
        if fighter not in cache:
            cache[fighter] = extract_base_files(iso, fighter)
        return cache[fighter]

    dol = Dol(iso)
    entries = []; reports = []; failures = []
    for project in projects:
        if only and project.name not in only:
            continue
        slot = _read(project / "rig.json")["base_fighter"]
        try:
            entry, report = build_character(project, files_for(slot), out / project.name, source_files_for=files_for, dol=dol)
        except Exception as exc:  # keep building the rest; report what failed
            failures.append({"character": project.name, "slot": slot, "error": str(exc)}); log(f"FAILED {project.name}: {exc}")
            continue
        entries.append(entry); reports.append(report)
        log(f"{report['display_name']} -> {slot}: " + "; ".join(report["steps"]))
        for w in report["warnings"]:
            log(f"  warning: {w}")
    result = {"roster": doc.get("id", "roster"), "output": str(out), "characters": reports, "failures": failures}
    if profile and entries:
        pid = doc.get("id", "custom-roster")
        profile_doc = {"id": pid, "name": doc.get("name", pid), "game_version": "GALE01-1.02",
                       "base_game": str(Path(iso).expanduser().resolve()).replace("\\", "/"), "plugins": [], "mods": [],
                       "mode": "offline", "online_safe": False,
                       "characters": [{k: v.replace("\\", "/") if isinstance(v, str) and k != "slot" else v for k, v in e.items()} for e in entries]}
        profile = Path(profile).expanduser().resolve(); profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(json.dumps(profile_doc, indent=2) + "\n", encoding="utf-8")
        result["profile"] = str(profile)
    (out / "roster-report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
