from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .special_graft import Dol
from .authoring import ProjectEditor
from .exporter import export_project
from .gltf import validate_model
from .preview import render_svg
from .move_library import load_library, validate_library
from .calibration import validate_calibration
from .hsd_archive import validate_hsd_archive, extract_hsd_symbols, extract_hsd_publics, validate_hsd_relocations
from .game_assets import discover_fighter_assets, stage_fighter_assets
from .hsd_model import inspect_hsd_model
from .hsd_to_gltf import export_hsd_gltf
from .hsd_patch import patch_hsd_streams
from .hsd_animation import export_hsd_animation
from .package_runtime import compose_fighter_hsd, compose_fighter_slot


def inspect_skeleton(a):
    """Derived segment table for a fighter, compared with the measured one when there is one."""
    from . import base_skeleton
    from .fighter_import import BASE_SEGMENTS, base_bind_positions, resolve_base_rig
    if a.iso:
        from .game_source import extract_base_files
        files = extract_base_files(a.iso, a.fighter); costume, data, common = files["costume"], files["data"], files["common"]
    else:
        costume, data, common = a.costume, a.fighter_data, a.common_data
    if not (costume and common):
        raise ValueError("pass --iso, or --costume and --common-data")
    rig = {"base_fighter": a.fighter, "segments": "auto"}
    table, joints, root, hosts, template, notes = resolve_base_rig(rig, costume, fighter_data=data, common_data=common)
    parts = base_skeleton.read_parts_map(common, a.fighter)
    result = {"fighter": a.fighter, "root_symbol": root, "joints": len(joints), "hosts": list(hosts), "template_dobj": template, "notes": notes,
              "parts": {name: parts.joint(name) for name in base_skeleton.PART_NAMES if parts.joint(name) is not None},
              "segments": [dict(zip(("name", "from", "to", "joint_start", "joint_end", "owner", "parent", "mode"), row)) for row in table.rows],
              "torso": table.torso}
    measured = BASE_SEGMENTS.get(a.fighter)
    if measured:
        derived = {row[0]: row for row in table.rows}
        result["versus_measured"] = [{"segment": m[0], "measured": list(m[3:6]), "derived": list(derived[m[0]][3:6]) if m[0] in derived else None,
                                      "match": m[0] in derived and tuple(m[3:6]) == tuple(derived[m[0]][3:6])} for m in measured]
    return result


def main(argv=None):
    p = argparse.ArgumentParser(prog="melee-character")
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("validate-model"); x.add_argument("path")
    x = sub.add_parser("export"); x.add_argument("project"); x.add_argument("output")
    x = sub.add_parser("validate-project"); x.add_argument("project")
    x = sub.add_parser("edit-move"); x.add_argument("project"); x.add_argument("output"); x.add_argument("name"); x.add_argument("slot"); x.add_argument("--reference"); x.add_argument("--transition", action="append", default=[])
    x = sub.add_parser("set-attribute"); x.add_argument("project"); x.add_argument("output"); x.add_argument("name"); x.add_argument("value", type=float)
    x = sub.add_parser("gui"); x.add_argument("project"); x.add_argument("--output")
    x = sub.add_parser("qt-gui"); x.add_argument("project"); x.add_argument("--source"); x.add_argument("--texture-source")
    x = sub.add_parser("preview-model"); x.add_argument("model"); x.add_argument("output")
    x = sub.add_parser("validate-library"); x.add_argument("path")
    x = sub.add_parser("validate-calibration"); x.add_argument("path")
    x = sub.add_parser("inspect-hsd"); x.add_argument("path")
    x = sub.add_parser("inspect-hsd-model"); x.add_argument("path"); x.add_argument("--symbol")
    x = sub.add_parser("convert-hsd"); x.add_argument("path"); x.add_argument("output"); x.add_argument("--symbol"); x.add_argument("--animation-source"); x.add_argument("--clip", type=int, default=0); x.add_argument("--texture-source")
    x = sub.add_parser("patch-hsd"); x.add_argument("path"); x.add_argument("output"); x.add_argument("edits")
    x = sub.add_parser("export-hsd-animation"); x.add_argument("path"); x.add_argument("output"); x.add_argument("edits"); x.add_argument("--clip", type=int, default=0)
    x = sub.add_parser("compose-fighter-hsd"); x.add_argument("package"); x.add_argument("base_hsd"); x.add_argument("output")
    x = sub.add_parser("compose-fighter-slot"); x.add_argument("package"); x.add_argument("base_hsd"); x.add_argument("output")
    x = sub.add_parser("import-fighter-model"); x.add_argument("project"); x.add_argument("base_costume"); x.add_argument("output")
    x.add_argument("--fighter-data", help="the base PlXx.dat (for automatic host/visibility selection)"); x.add_argument("--common-data", help="the disc's PlCo.dat (skeleton map for any base fighter)")
    x = sub.add_parser("generate-model", help="build a project's model.json into model/model.gltf and update rig.json"); x.add_argument("project"); x.add_argument("--no-preview", action="store_true")
    x = sub.add_parser("inspect-skeleton", help="show the body-part map derived from PlCo.dat for a base fighter")
    x.add_argument("fighter"); x.add_argument("--iso"); x.add_argument("--costume"); x.add_argument("--fighter-data"); x.add_argument("--common-data")
    x = sub.add_parser("build-character", help="package + compose + costume one project against your disc"); x.add_argument("project"); x.add_argument("--iso", required=True); x.add_argument("--out", required=True)
    x = sub.add_parser("build-roster", help="build every project in a roster.json and write a PascalPatch profile")
    x.add_argument("roster"); x.add_argument("--iso", required=True); x.add_argument("--out", required=True); x.add_argument("--profile"); x.add_argument("--only", action="append")
    x = sub.add_parser("studio", help="open the 3D web editor for a project")
    x.add_argument("project"); x.add_argument("--iso", help="your GALE01 Rev.02 disc image (remembered)"); x.add_argument("--http-port", type=int, default=8765)
    x.add_argument("--no-browser", action="store_true"); x.add_argument("--pascalpatch-repo", "--meleemod-repo"); x.add_argument("--pascalpatch-root", "--meleemod-root"); x.add_argument("--pascalpatch-data", "--meleemod-data")
    x.add_argument("--port", help="melee_port.exe for Launch"); x.add_argument("--port-cwd")
    x = sub.add_parser("app", help="open Character Studio: the start screen, projects, and the roster")
    x.add_argument("--project", help="open straight into this project"); x.add_argument("--http-port", type=int, default=8766)
    x.add_argument("--no-window", action="store_true", help="serve for a browser instead of opening a window")
    x.add_argument("--pascalpatch-repo"); x.add_argument("--pascalpatch-root"); x.add_argument("--pascalpatch-data")
    x.add_argument("--pascalpatch-iso", help="a Melee disc PascalPatch uses, taken when the studio has none set")
    x.add_argument("--port", help="melee_port.exe for Play"); x.add_argument("--port-cwd")
    x = sub.add_parser("discover-assets"); x.add_argument("root"); x.add_argument("--fighter")
    x = sub.add_parser("stage-assets"); x.add_argument("root"); x.add_argument("output"); x.add_argument("--fighter")
    a = p.parse_args(argv)
    try:
        if a.cmd == "validate-model":
            r = validate_model(a.path); print(json.dumps({"valid": r.valid, "diagnostics": [d.__dict__ for d in r.diagnostics], "meshes": r.meshes, "joints": r.joints}, indent=2)); return 0 if r.valid else 2
        if a.cmd == "export": print(export_project(a.project, a.output)); return 0
        if a.cmd == "validate-project":
            d = ProjectEditor(a.project).validate(); print(json.dumps({"valid": not d, "diagnostics": [x.__dict__ for x in d]}, indent=2)); return 0 if not d else 2
        if a.cmd in {"edit-move", "set-attribute"}:
            editor = ProjectEditor(a.project)
            if a.cmd == "edit-move": editor.add_move(a.name, a.slot, a.reference, a.transition)
            else: editor.set_attribute(a.name, a.value)
            d = editor.validate()
            if d: print(json.dumps({"valid": False, "diagnostics": [x.__dict__ for x in d]}, indent=2), file=sys.stderr); return 2
            print(editor.save(a.output)); return 0
        if a.cmd in {"gui", "qt-gui"}:
            print(f"The {a.cmd} desktop editors were replaced by the 3D web editor: melee-character studio {a.project} --iso <GALE01.iso>", file=sys.stderr); return 2
        if a.cmd == "preview-model": print(render_svg(a.model, a.output)); return 0
        if a.cmd == "validate-library":
            document = json.loads(Path(a.path).read_text(encoding="utf-8")); diagnostics = validate_library(document); print(json.dumps({"valid": not diagnostics, "diagnostics": [x.__dict__ for x in diagnostics]}, indent=2)); return 0 if not diagnostics else 2
        if a.cmd == "validate-calibration":
            document = json.loads(Path(a.path).read_text(encoding="utf-8")); diagnostics = validate_calibration(document); print(json.dumps({"valid": not diagnostics, "diagnostics": [x.__dict__ for x in diagnostics]}, indent=2)); return 0 if not diagnostics else 2
        if a.cmd == "discover-assets":
            assets = discover_fighter_assets(a.root, a.fighter); print(json.dumps([{"fighter_id": x.fighter_id, "path": str(x.path), "kind": x.kind, "size": x.size} for x in assets], indent=2)); return 0
        if a.cmd == "stage-assets":
            copied = stage_fighter_assets(a.root, a.output, a.fighter); print(json.dumps({"output": str(Path(a.output).expanduser().resolve()), "files": [str(x) for x in copied]}, indent=2)); return 0
        if a.cmd == "convert-hsd": print(export_hsd_gltf(a.path, a.output, a.symbol, a.animation_source, a.clip, a.texture_source)); return 0
        if a.cmd == "patch-hsd":
            edits = json.loads(Path(a.edits).read_text(encoding="utf-8")); output, count = patch_hsd_streams(a.path, a.output, edits); print(json.dumps({"output": str(output), "patched_streams": count}, indent=2)); return 0
        if a.cmd == "export-hsd-animation":
            edits = json.loads(Path(a.edits).read_text(encoding="utf-8")); output, count = export_hsd_animation(a.path, a.output, a.clip, edits); print(json.dumps({"output": str(output), "patched_tracks": count}, indent=2)); return 0
        if a.cmd == "studio":
            from .studio_server import serve
            serve(a.project, a.iso, a.http_port, not a.no_browser,
                  {"repo": a.pascalpatch_repo, "root": a.pascalpatch_root, "data": a.pascalpatch_data, "port": a.port, "port_cwd": a.port_cwd}); return 0
        if a.cmd == "app":
            from .studio_server import serve
            serve(a.project, None, a.http_port, False,
                  {"repo": a.pascalpatch_repo, "root": a.pascalpatch_root, "data": a.pascalpatch_data, "port": a.port, "port_cwd": a.port_cwd,
                   "iso": a.pascalpatch_iso},
                  window=not a.no_window); return 0
        if a.cmd == "import-fighter-model":
            from .fighter_import import import_project_model
            _, report = import_project_model(a.project, a.base_costume, a.output, fighter_data=a.fighter_data, common_data=a.common_data); print(json.dumps(report, indent=2, sort_keys=True)); return 0
        if a.cmd == "generate-model":
            from .model_kit import generate_project_model
            print(json.dumps(generate_project_model(a.project, preview=not a.no_preview), indent=2)); return 0
        if a.cmd == "inspect-skeleton":
            print(json.dumps(inspect_skeleton(a), indent=2)); return 0
        if a.cmd == "build-character":
            from .game_source import extract_base_files
            from .roster import build_character
            rig = json.loads((Path(a.project) / "rig.json").read_text(encoding="utf-8"))
            entry, report = build_character(a.project, extract_base_files(a.iso, rig["base_fighter"]), a.out, source_files_for=lambda f: extract_base_files(a.iso, f), dol=Dol(a.iso))
            print(json.dumps({"entry": entry, **report}, indent=2)); return 0
        if a.cmd == "build-roster":
            from .roster import build_roster
            result = build_roster(a.roster, a.iso, a.out, profile=a.profile, only=a.only, log=lambda m: print(m, file=sys.stderr))
            print(json.dumps(result, indent=2)); return 0 if not result["failures"] else 2
        if a.cmd == "compose-fighter-slot": print(compose_fighter_slot(a.package, a.base_hsd, a.output)); return 0
        if a.cmd == "compose-fighter-hsd": print(compose_fighter_hsd(a.package, a.base_hsd, a.output)); return 0
        if a.cmd == "inspect-hsd-model":
            r = inspect_hsd_model(a.path, a.symbol); print(json.dumps({"path": str(r.path), "root_symbol": r.root_symbol, "root_offset": r.root_offset, "joints": [x.__dict__ for x in r.joints]}, indent=2)); return 0
        if a.cmd == "inspect-hsd":
            raw = Path(a.path).read_bytes(); info = validate_hsd_archive(raw); print(json.dumps({"file_size": info.file_size, "data_size": info.data_size, "relocations": info.relocations, "publics": info.publics, "externs": info.externs, "version": info.version.decode("ascii", "replace"), "symbols": extract_hsd_symbols(raw), "public_roots": extract_hsd_publics(raw), "relocation_values_validated": len(validate_hsd_relocations(raw))}, indent=2)); return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr); return 2
    return 2


if __name__ == "__main__": raise SystemExit(main())
