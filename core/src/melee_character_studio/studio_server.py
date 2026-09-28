"""Character Studio: the desktop studio for Melee characters.

``melee-character-studio app`` opens the studio in a window of its own, on the start screen
(new project from a model, recent projects, open); ``melee-character studio <project>`` opens
one project straight in the editor. Either way this serves a three.js editor on 127.0.0.1: the
browser does rendering and interaction, and this module does all file work with the existing
pipeline (model import, retarget, costume writer, slot composition, PascalPatch build/launch).
Only requests whose Host and Origin are the studio's own are served, so other web pages cannot
drive it.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .special_graft import KINDS, NEEDS_ARTICLES, SLOTS as SPECIAL_SLOTS, Dol
from .anim_bake import AnimationLibrary
from .exporter import export_project
from .fighter_import import (BASE_SEGMENTS, _rotate_y, base_bind_positions, load_gltf_mesh, resolve_base_rig, retarget_mesh)
from .attributes import validate_attribute_scales, validate_attributes
from .cast import cast_data, review as review_against_cast
from .fighter_moves import ELEMENTS, FighterData, find_action, find_actions, move_frame_data, resolve_alias
from .game_source import cache_root, extract_base_files
from .hsd_model import HsdReader, _hsd_world_matrices, _inverse_affine4
from .package_runtime import INT_ATTRIBUTES, SLOT_ATTRIBUTE_OFFSETS, compose_fighter_slot
from .move_transplant import borrow_moves, list_moves, retarget_clip, encode_figatree, Skeleton, bake_clip
from .game_source import FIGHTER_CODES
from .hsd_texture import rgba_png
from . import projects as P
from .portrait import HEIGHT as PORTRAIT_H, ICON_FILE, ICON_HEIGHT, ICON_WIDTH, PORTRAIT_FILE, STOCK_FILE, STOCK_HEIGHT, STOCK_WIDTH, WIDTH as PORTRAIT_W, portrait_image

WEB = Path(__file__).with_name("web")
CONFIG = P.CONFIG
ATTR_NAMES = {5: "tx", 6: "ty", 7: "tz"}

FIGHTER_NAMES = {"dr-mario": "Dr. Mario", "mr-game-and-watch": "Mr. Game & Watch", "captain-falcon": "Captain Falcon",
                 "donkey-kong": "Donkey Kong", "ice-climbers": "Ice Climbers", "young-link": "Young Link"}
# Move slots offered in the editor: (id, label, the base actions a new move in that slot starts from)
MOVE_SLOTS = [
    ("jab", "Jab", ["Attack11"]), ("attack_dash", "Dash attack", ["AttackDash"]),
    ("tilt_forward", "Forward tilt", ["AttackS3*"]), ("tilt_up", "Up tilt", ["AttackHi3"]), ("tilt_down", "Down tilt", ["AttackLw3"]),
    ("smash_forward", "Forward smash", ["AttackS4*"]), ("smash_up", "Up smash", ["AttackHi4"]), ("smash_down", "Down smash", ["AttackLw4"]),
    ("attack_air_neutral", "Neutral air", ["AttackAirN"]), ("attack_air_forward", "Forward air", ["AttackAirF"]),
    ("attack_air_back", "Back air", ["AttackAirB"]), ("attack_air_up", "Up air", ["AttackAirHi"]), ("attack_air_down", "Down air", ["AttackAirLw"]),
    ("special_neutral", "Neutral B", ["SpecialN*", "SpecialAirN*"]), ("special_side", "Side B", ["SpecialS*", "SpecialAirS*"]),
    ("special_up", "Up B", ["SpecialHi*", "SpecialAirHi*"]), ("special_down", "Down B", ["SpecialLw*", "SpecialAirLw*"]),
]


def fighter_label(fighter):
    return FIGHTER_NAMES.get(fighter, fighter.replace("-", " ").title())


def editor_options(base_fighter):
    """Everything the editor's dropdowns offer, so the browser never needs free text for a choice."""
    fighters = sorted(FIGHTER_CODES, key=fighter_label)
    return {
        "fighters": [{"id": f, "label": fighter_label(f)} for f in fighters],
        "slots": [{"id": i, "label": l, "actions": a, "special": i in SPECIAL_SLOTS} for i, l, a in MOVE_SLOTS],
        "elements": sorted(ELEMENTS, key=ELEMENTS.get),
        # which fighters' specials can be grafted into each special slot (and why not)
        "grafts": {slot: [{"id": f, "label": fighter_label(f),
                           "blocked": "spawns projectiles or items, which grafts can't carry yet" if (f, slot) in NEEDS_ARTICLES else None}
                          for f in fighters if f in KINDS and f != base_fighter] for slot in SPECIAL_SLOTS},
    }


PORTRAIT_KEYS = ("portrait", "portrait_source", "portrait_crop", "portrait_mode", "icon", "stock")
SOURCE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


def _data_url(value, allowed):
    """(mime, bytes) of a base64 ``data:`` URL whose type is in ``allowed``."""
    head, _, body = str(value).partition(",")
    mime = head[5:].split(";")[0] if head.startswith("data:") and head.endswith(";base64") else ""
    if mime not in allowed:
        raise ValueError(f"expected a {' / '.join(allowed)} image")
    return mime, base64.b64decode(body, validate=True)


load_config = P.load_config
save_config = P.save_config


def pascalpatch_cli(cfg, args, wait=True):
    """Runs a PascalPatch command with the studio's PascalPatch settings."""
    mm = cfg["pascalpatch"]; env = dict(os.environ, PYTHONPATH=str(Path(mm["repo"]) / "host" / "src"))
    cmd = [sys.executable, "-m", "pascalpatch.cli", "--root", mm["root"], "--data", mm.get("data") or str(Path(mm["root"]) / "data")] + args
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not wait:
        subprocess.Popen(cmd, env=env, cwd=mm["repo"], creationflags=flags); return {"command": cmd, "started": True}
    run = subprocess.run(cmd, env=env, cwd=mm["repo"], capture_output=True, text=True, timeout=1800, creationflags=flags)
    return {"command": cmd, "exit": run.returncode, "stdout": run.stdout[-4000:], "stderr": run.stderr[-4000:]}


def _flat(m):
    return [round(float(x), 6) for x in m]


class Session:
    def __init__(self, project, iso, config):
        self.project = Path(project).resolve(); self.iso = Path(iso).resolve(); self.config = config
        self.lock = threading.Lock(); self.log = []
        self.reload()

    # ------------------------------------------------------------------ files
    def _read(self, name):
        return json.loads((self.project / name).read_text(encoding="utf-8"))

    def reload(self):
        self.character = self._read("character.json"); self.moveset = self._read("moveset.json"); self.rig = self._read("rig.json")
        self.base_fighter = self.rig["base_fighter"]
        self.files = extract_base_files(self.iso, self.base_fighter)
        self.table, _, self.root_symbol, _, _, self.rig_notes = resolve_base_rig(
            self.rig, self.files["costume"], fighter_data=self.files["data"], common_data=self.files.get("common"))
        self.report, self.bind = base_bind_positions(self.files["costume"], self.root_symbol)
        local = [{"position": list(j.position), "rotation": list(j.rotation), "scale": list(j.scale)} for j in self.report.joints]
        self.rest_local = local
        self.bind_world, _ = _hsd_world_matrices(self.report.joints, local)
        self.inv_bind = [tuple(j.envelope_matrix) + (0, 0, 0, 1) if j.envelope_matrix else _inverse_affine4(self.bind_world[i])
                         for i, j in enumerate(self.report.joints)]
        self.anims = AnimationLibrary(self.files["animations"])
        self._load_model()

    def _load_model(self):
        model = Path(self.rig["model"]); model = model if model.is_absolute() else self.project / model
        mesh = load_gltf_mesh(model); turn = float(self.rig.get("rotate_y_degrees", 0.0))
        if turn:
            mesh.positions = _rotate_y(mesh.positions, turn); mesh.normals = _rotate_y(mesh.normals, turn)
        self.mesh = mesh

    def save(self, payload):
        with self.lock:
            for name in ("character", "moveset", "rig"):
                if name in payload:
                    doc = payload[name]
                    if name == "character":   # the photo is managed by /api/portrait alone
                        doc = {k: v for k, v in doc.items() if k not in PORTRAIT_KEYS}
                        doc.update({k: v for k, v in self._read("character.json").items() if k in PORTRAIT_KEYS})
                    (self.project / f"{name}.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            self.character = self._read("character.json"); self.moveset = self._read("moveset.json")
            old_model = (self.rig.get("model"), self.rig.get("rotate_y_degrees")); self.rig = self._read("rig.json")
            if (self.rig.get("model"), self.rig.get("rotate_y_degrees")) != old_model:
                self._load_model()
            P.clear_autosave(self.project)   # the files are current again
        return {"saved": True}

    # --------------------------------------------------------------- queries
    def _rows(self):
        return self.table.rows if self.table is not None else BASE_SEGMENTS[self.base_fighter]

    def state(self):
        table = self._rows()
        return {
            "project": self.project.name, "path": str(self.project), "autosave": P.autosave_info(self.project),
            "character": self.character, "moveset": self.moveset, "rig": self.rig,
            "base": {"fighter": self.base_fighter, "root_symbol": self.root_symbol,
                     "joints": [{"index": j.index, "parent": j.parent, "bind": _flat(self.bind_world[j.index]),
                                 "inverse": _flat(self.inv_bind[j.index]), "envelope": bool(j.envelope_matrix)} for j in self.report.joints],
                     "segments": [{"name": s[0], "start": s[1], "end": s[2], "joint_start": s[3], "joint_end": s[4],
                                   "owner": s[5], "parent": s[6], "mode": (self.rig.get("segment_modes") or {}).get(s[0], s[7])} for s in table],
                     "notes": self.rig_notes},
            "model": {"positions": [c for p in self.mesh.positions for c in (round(p[0], 5), round(p[1], 5), round(p[2], 5))],
                      "normals": [round(c, 4) for n in self.mesh.normals for c in n],
                      "uvs": [round(c, 6) for uv in self.mesh.uvs for c in uv],
                      "indices": [i for t in self.mesh.triangles for i in t],
                      "texture": "/api/texture" if self.mesh.texture else None},
            "animations": [n.split("ACTION_")[-1].replace("_figatree", "") for n in self.anims.names()],
            "attribute_names": sorted(SLOT_ATTRIBUTE_OFFSETS),
            "base_attributes": self._base_attributes(),
            "pascalpatch": bool(self.config.get("pascalpatch", {}).get("repo")),
            "options": editor_options(self.base_fighter),
        }

    def _effective_attributes(self, character=None):
        """The fighter's attributes after this project's overrides and scales."""
        character = character or self.character
        attrs = self._base_attributes()
        for k, scale in (character.get("attribute_scales") or {}).items():
            if k in attrs:
                attrs[k] = attrs[k] * float(scale)
        attrs.update({k: v for k, v in (character.get("attributes") or {}).items() if k in attrs and isinstance(v, (int, float))})
        return attrs

    def review(self, doc=None):
        """Check the project: errors that would stop a build, and stats and moves ranked against Melee's cast."""
        doc = doc or {}
        character = doc.get("character") or self.character; moveset = doc.get("moveset") or self.moveset
        base = self._base_attributes()
        result = review_against_cast(cast_data(self.iso), self.base_fighter, self._effective_attributes(character), moveset, base)
        errors = []
        for d in validate_attributes(character.get("attributes") or {}, base, scaled=(character.get("attribute_scales") or {}).keys()):
            errors.append({"level": "error", "area": "stats", "key": d.path.split(".")[-1], "message": f"{d.path}: {d.message}"})
        for d in validate_attribute_scales(character.get("attribute_scales") or {}):
            errors.append({"level": "error", "area": "stats", "key": d.path.split(".")[-1], "message": f"{d.path}: {d.message}"})
        for move in self.moves(moveset)["moves"]:
            for row in move["base"]:
                if row.get("error"):
                    errors.append({"level": "error", "area": "moves", "key": move["name"], "message": f"{move['name']}: {row['action']}: {row['error']}"})
        slots = [m.get("slot") for m in moveset.get("moves", [])]
        for slot in sorted({x for x in slots if slots.count(x) > 1}):
            errors.append({"level": "error", "area": "moves", "key": slot, "message": f"Two moves use the {slot.replace('_', ' ')} slot; only one can."})
        result["findings"] = errors + result["findings"]
        return result

    def _base_attributes(self):
        import struct
        data = FighterData(self.files["data"]); table = data.u32(data.root)
        return {k: round(struct.unpack_from(">i" if k in INT_ATTRIBUTES else ">f", data.raw, 0x20 + table + off)[0], 4)
                for k, off in SLOT_ATTRIBUTE_OFFSETS.items()}

    def retarget(self, rig):
        rigged, info = retarget_mesh(self.mesh, rig["landmarks"], self.bind, self.base_fighter,
                                     segment_overrides=rig.get("segment_overrides"), segment_scale=rig.get("segment_scale"),
                                     vertex_offsets=None, joint_rotations=rig.get("joint_rotations"), table=self.table,
                                     segment_modes=rig.get("segment_modes"), segment_ranges=rig.get("segment_ranges"),
                                     rigid=rig.get("rigid"), scale_factor=float(rig.get("scale_factor", 1.0)))
        return {"positions": [round(c, 5) for p in rigged.positions for c in p],
                "normals": [round(c, 4) for n in rigged.normals for c in n],
                "weights": [[[j, w] for j, w in ws] for ws in rigged.weights],
                "segments": rigged.segments, "scale": info["scale"]}

    def animation(self, short):
        name = next(n for n in self.anims.names() if n.split("ACTION_")[-1].replace("_figatree", "") == short)
        return self._frames_from_baked(self.anims.bake(name), short)

    def drivers(self):
        return sorted({j for s in self._rows() for j in (s[3], s[4], s[5])})

    def borrows(self):
        out = []
        for move in self.moveset.get("moves", []):
            b = move.get("borrow")
            if b:
                out.append({"fighter": b["fighter"], "action": b["action"], "target_action": b.get("target_action") or b["action"]})
        return out

    def fighter_moves(self, fighter):
        files = extract_base_files(self.iso, fighter)
        return {"fighter": fighter, "moves": list_moves(files["data"], files["animations"])}

    def borrow_preview(self, fighter, action):
        from .hsd_animation import scan_figatree
        src_files = extract_base_files(self.iso, fighter); src = Skeleton(src_files["costume"]); dst = Skeleton(self.files["costume"])
        clip = next(c for c in scan_figatree(src_files["animations"]) if c.name.endswith(f"_{action}_figatree"))
        frames, mapping = retarget_clip(src, dst, src_files["animations"].read_bytes(), clip, self.drivers())
        archive, _ = encode_figatree("preview_figatree", frames, len(dst.joints), len(frames))
        # decode what the game will actually read back
        from .hsd_animation import scan_figatree as scan
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as fh:
            fh.write(archive); tmp = fh.name
        try:
            c2 = scan(tmp)[0]; baked = bake_clip(archive, c2)
        finally:
            os.unlink(tmp)
        return self._frames_from_baked(baked, f"{fighter}:{action}") | {"bytes": len(archive), "mapped_joints": len(mapping)}

    def _frames_from_baked(self, baked, name):
        frames = []
        for values in baked:
            local = [{"position": list(r["position"]), "rotation": list(r["rotation"]), "scale": list(r["scale"])} for r in self.rest_local]
            for node, channels in enumerate(values):
                if node >= len(local): break
                for t, v in channels.items():
                    if 1 <= t <= 3: local[node]["rotation"][t - 1] = v
                    elif 5 <= t <= 7: local[node]["position"][t - 5] = v
                    elif 8 <= t <= 10: local[node]["scale"][t - 8] = v
            world, _ = _hsd_world_matrices(self.report.joints, local)
            frames.append([round(x, 5) for m in world for x in m])
        return {"name": name, "frames": frames, "joints": len(self.report.joints)}

    def moves(self, moveset=None):
        """Base hitboxes for each move of ``moveset`` (default: the saved one), plus the action names."""
        data = FighterData(self.files["data"]); out = []
        frames = {c.name: c.frames for c in self.anims.clips}
        attrs = self._effective_attributes()
        for move in (moveset or self.moveset).get("moves", []):
            variants = move.get("actions") or ([{"action": move["action"], "tuning": move.get("tuning", {})}] if move.get("action") else [])
            rows = []
            for v in variants:
                try:
                    found = find_actions(data, v["action"]) if any(c in v["action"] for c in "*?[") else [find_action(data, v["action"])]
                    if not found:
                        raise ValueError("no matching action on this base fighter")
                    for _, _, name, script in found:
                        short = name.split("ACTION_")[-1].replace("_figatree", "")
                        try:
                            timing = move_frame_data(data, resolve_alias(data, script), frames.get(name), short, attrs)
                        except Exception:
                            timing = None
                        rows.append({"action": short, "pattern": v["action"], "timing": timing,
                                     "hitboxes": [{k: b[k] for k in ("id", "bone", "damage", "size", "element", "angle", "knockback_growth", "base_knockback", "weight_set_knockback")} for b in data.hitboxes(script)]})
                except Exception as exc:
                    rows.append({"action": v["action"], "error": str(exc)})
            out.append({"name": move["name"], "base": rows})
        actions = sorted({n.split("ACTION_")[-1].replace("_figatree", "") for _, _, n, s in data.action_tables() if s and "ACTION_" in n and data.hitboxes(s)})
        return {"moves": out, "actions": actions}

    # -------------------------------------------------------------- portrait
    def portrait_file(self, source=False, icon=False, stock=False):
        name = self.character.get("stock" if stock else "icon" if icon else "portrait_source" if source else "portrait")
        path = (self.project / name) if name else None
        if not path or not path.is_file():
            raise FileNotFoundError("this character has no portrait")
        return path

    def set_portrait(self, payload):
        """Store the select-screen pictures.

        ``image``: the door-sized picture the editor made, either a crop of a photo (``source``,
        sent when it is new, and ``crop``) or rendered from the model (``generated``).
        ``icon``: the grid icon, rendered from the model. ``stock``: the stock icon shown above
        the damage in a match, rendered from the model's head. ``remove`` drops them all.
        """
        def drop(key):
            old = doc.pop(key, None)
            if old and Path(old).name == old and (self.project / old).is_file():
                (self.project / old).unlink()

        with self.lock:
            doc = self._read("character.json")
            if payload.get("remove"):
                drop("portrait"); drop("portrait_source"); drop("icon"); drop("stock"); doc.pop("portrait_crop", None); doc.pop("portrait_mode", None)
            if payload.get("icon"):
                png = _data_url(payload["icon"], ("image/png",))[1]
                image = portrait_image(png, ICON_WIDTH, ICON_HEIGHT)
                if len(png) > 500_000 or (image["width"], image["height"]) != (ICON_WIDTH, ICON_HEIGHT):
                    png = rgba_png(image)
                (self.project / ICON_FILE).write_bytes(png); doc["icon"] = ICON_FILE
            if payload.get("stock"):
                png = _data_url(payload["stock"], ("image/png",))[1]
                image = portrait_image(png, STOCK_WIDTH, STOCK_HEIGHT)
                if len(png) > 100_000 or (image["width"], image["height"]) != (STOCK_WIDTH, STOCK_HEIGHT):
                    png = rgba_png(image)
                (self.project / STOCK_FILE).write_bytes(png); doc["stock"] = STOCK_FILE
            if payload.get("image"):
                png = _data_url(payload["image"], ("image/png",))[1]
                image = portrait_image(png)   # also rejects anything that is not a readable PNG
                if len(png) > 2_000_000 or (image["width"], image["height"]) != (PORTRAIT_W, PORTRAIT_H):
                    png = rgba_png(image)
                (self.project / PORTRAIT_FILE).write_bytes(png); doc["portrait"] = PORTRAIT_FILE
                if payload.get("generated"):   # rendered from the model: no photo behind it
                    drop("portrait_source"); doc["portrait_mode"] = "model"
                else:
                    doc["portrait_mode"] = "photo"
                if payload.get("source"):   # a new photo; a re-crop keeps the stored one
                    mime, raw = _data_url(payload["source"], tuple(SOURCE_TYPES))
                    drop("portrait_source")
                    name = "portrait_source" + SOURCE_TYPES[mime]
                    (self.project / name).write_bytes(raw); doc["portrait_source"] = name
                doc.pop("portrait_crop", None)
                if isinstance(payload.get("crop"), dict) and not payload.get("generated"):
                    doc["portrait_crop"] = {k: round(float(payload["crop"][k]), 4) for k in ("zoom", "x", "y") if k in payload["crop"]}
            (self.project / "character.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            self.character = doc
            return {"character": doc}

    # ---------------------------------------------------------------- export
    def _out_dir(self):
        mm = self.config.get("pascalpatch", {})
        base = Path(mm["root"]) / "build" if mm.get("root") else cache_root().parent / "out"
        out = base / self.character["id"]; out.mkdir(parents=True, exist_ok=True); return out

    def _build_character(self):
        from .roster import build_character
        code = self.files["data"].name[:-4]; out = self._out_dir()
        entry, report = build_character(self.project, self.files, out, source_files_for=lambda f: extract_base_files(self.iso, f), dol=Dol(self.iso))
        return out, code, {"steps": report["steps"] + [f"warning: {w}" for w in report["warnings"]], "output": str(out)}

    def export(self):
        with self.lock:
            out, code, result = self._build_character()
            mm = self.config.get("pascalpatch", {})
            if mm.get("repo") and mm.get("root"):
                profile = self._write_profile(out, code)
                build = self._pascalpatch(["build", profile])
                result["build"] = build
            return result

    TEST_STAGES = {"fd", "bf", "ys", "fod", "ps", "dl"}
    TEST_PLAYERS = {"human", "cpu1", "cpu5", "cpu9", "none"}

    def test_in_game(self, payload):
        """Build the character and start the game straight in a match with it: no menus.

        A test profile of its own (``<id>-studio-test``) puts the character in place of its base
        fighter, whatever the project's install choice, so PascalPatch's quick-match plugin can
        pick it by the base fighter's slot; player 2 is ``opponent`` (the base fighter itself
        by default) on ``stage``. A human player 2 is for Training Lab's dummy.
        """
        mm = self.config.get("pascalpatch", {})
        if not (mm.get("repo") and mm.get("root") and mm.get("port")):
            raise ValueError("configure --pascalpatch-repo, --pascalpatch-root and --port to test in game")
        opponent = payload.get("opponent") or self.base_fighter
        if opponent not in FIGHTER_CODES: raise ValueError(f"unknown opponent {opponent!r}")
        stage = payload.get("stage", "fd"); player = payload.get("p2_player", "human")
        if stage not in self.TEST_STAGES: raise ValueError(f"unknown stage {stage!r}")
        if player not in self.TEST_PLAYERS: raise ValueError(f"unknown player 2 {player!r}")
        with self.lock:
            out, code, result = self._build_character()
            match = {"p1": self.base_fighter, "p2": opponent, "p2_player": player, "stage": stage, "rules": "endless"}
            profile = self._write_profile(out, code, test=match)
            build = self._pascalpatch(["build", profile]); result["build"] = build
            if build.get("exit") != 0:
                return result
        args = ["launch", profile, "--runtime", "native", "--no-build", "--allow-unsafe", "--port", mm["port"]]
        if mm.get("port_cwd"): args += ["--port-cwd", mm["port_cwd"]]
        result["launch"] = self._pascalpatch(args, wait=False)
        result["steps"].append(f"Game starting straight in a match: {self.character['display_name']} vs {fighter_label(opponent)} (offline, Slippi off).")
        return result

    def _write_profile(self, out, code, test=None):
        mm = self.config["pascalpatch"]; pid = f"{self.character['id']}-studio" + ("-test" if test else "")
        root = Path(mm["root"]); (root / "profiles").mkdir(parents=True, exist_ok=True)
        doc = {"id": pid, "name": f"{self.character['display_name']} (Character Studio{' test' if test else ''})", "game_version": "GALE01-1.02",
               "base_game": str(self.iso).replace("\\", "/"), "plugins": [], "mods": [], "mode": "offline", "online_safe": False,
               "characters": [{"package": str(out / f"{self.character['id']}.melee-character").replace("\\", "/"), "slot": self.base_fighter,
                               "fighter_file": str(out / f"{code}.dat").replace("\\", "/")}]}
        if (out / f"{code}Nr.dat").exists():
            doc["characters"][0]["costume_file"] = str(out / f"{code}Nr.dat").replace("\\", "/")
        if (out / f"{code}AJ.dat").exists():
            doc["characters"][0]["animation_file"] = str(out / f"{code}AJ.dat").replace("\\", "/")
        if (out / "move-graft.json").exists():   # grafted specials: PascalPatch stages its move-graft plugin
            doc["characters"][0]["move_graft"] = str(out / "move-graft.json").replace("\\", "/")
        portrait = out / f"{self.character['id']}.portrait"
        if portrait.exists():   # select-screen photo: PascalPatch stages its extra-fighters plugin
            doc["characters"][0]["portrait"] = str(portrait).replace("\\", "/")
        icon = out / f"{self.character['id']}.icon"
        if icon.exists():   # select-screen grid icon
            doc["characters"][0]["icon"] = str(icon).replace("\\", "/")
        stock = out / f"{self.character['id']}.stock"
        if stock.exists():   # the stock icon above the damage in a match
            doc["characters"][0]["stock"] = str(stock).replace("\\", "/")
        # A new fighter on the select screen (the base fighter stays), or the base's replacement.
        doc["characters"][0]["install"] = "replace" if test or self.character.get("install") == "replace" else "new"
        if test:   # PascalPatch's quick-match plugin boots straight into this match
            doc["quick_match"] = test
        (root / "profiles" / f"{pid}.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return pid

    def _pascalpatch(self, args, wait=True):
        return pascalpatch_cli(self.config, args, wait)

    def launch(self):
        mm = self.config.get("pascalpatch", {})
        if not (mm.get("repo") and mm.get("port")):
            raise ValueError("configure --pascalpatch-repo, --pascalpatch-root and --port to launch")
        pid = f"{self.character['id']}-studio"
        # Offline (no Slippi) is enforced by PascalPatch's launcher: network refused, private APPDATA.
        args = ["launch", pid, "--runtime", "native", "--no-build", "--allow-unsafe", "--port", mm["port"]]
        if mm.get("port_cwd"): args += ["--port-cwd", mm["port_cwd"]]
        return self._pascalpatch(args, wait=False)


class Job:
    """A long task (a roster build) running on a thread, with its output kept for the UI."""

    def __init__(self, title, fn):
        self.id = uuid.uuid4().hex[:8]; self.title = title; self.lines = []; self.running = True; self.error = None; self.result = None
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()

    def log(self, line):
        self.lines.append(str(line)); del self.lines[:-400]

    def _run(self, fn):
        try:
            self.result = fn(self.log)
        except Exception as exc:
            self.error = str(exc); self.log(f"error: {exc}")
        finally:
            self.running = False

    def view(self):
        return {"id": self.id, "title": self.title, "running": self.running, "error": self.error, "lines": self.lines[-200:], "result": self.result}


class Studio:
    """The desktop studio: settings, the start screen's lists, and the project that is open (if any)."""

    def __init__(self, cfg):
        self.cfg = cfg; self.session = None; self.lock = threading.Lock(); self.jobs = {}
        self.uploads = P.HOME / "uploads"

    # ---- start screen
    def info(self):
        pp = self.cfg.get("pascalpatch", {})
        return {"project": P.summary(self.session.project) if self.session else None,
                "recent": P.recent_projects(self.cfg), "projects_dir": str(P.projects_dir(self.cfg)),
                "fighters": [{"id": f, "label": fighter_label(f)} for f in sorted(FIGHTER_CODES, key=fighter_label)],
                "settings": {"iso": self.cfg.get("iso", ""), "projects_dir": self.cfg.get("projects_dir", ""),
                             "pascalpatch_repo": pp.get("repo", ""), "pascalpatch_root": pp.get("root", ""),
                             "port": pp.get("port", ""), "port_cwd": pp.get("port_cwd", "")},
                "ready": bool(self.cfg.get("iso")), "pascalpatch": bool(pp.get("repo") and pp.get("root")),
                "model_types": list(P.MODEL_TYPES), "examples": P.examples()}

    def picture(self, path, which):
        """A recent or rostered project's icon or portrait, for the start screen's cards."""
        path = str(Path(path).resolve())
        known = (set(self.cfg.get("recent", [])) | set(P.load_user_roster(self.cfg)["characters"])
                 | {str(Path(e["path"]).resolve()) for e in P.examples()})
        if path not in known:
            raise P.ProjectError("not a known project")
        doc = json.loads((Path(path) / "character.json").read_text(encoding="utf-8"))
        name = doc.get(which)
        if which not in ("icon", "portrait") or not name or Path(name).name != name:
            raise FileNotFoundError("no picture")
        return Path(path) / name

    def settings(self, payload):
        if "iso" in payload:
            iso = Path(str(payload["iso"]).strip().strip('"')).expanduser()
            if not iso.is_file():
                raise P.ProjectError(f"no disc image at {iso}")
            with open(iso, "rb") as f:
                if f.read(6) != b"GALE01":
                    raise P.ProjectError("that is not a Melee NTSC (GALE01) disc image")
            self.cfg["iso"] = str(iso.resolve())
        if "projects_dir" in payload:
            self.cfg["projects_dir"] = str(payload["projects_dir"]).strip()
        pp = self.cfg.setdefault("pascalpatch", {})
        for key, name in (("pascalpatch_repo", "repo"), ("pascalpatch_root", "root"), ("port", "port"), ("port_cwd", "port_cwd")):
            if key in payload:
                pp[name] = str(payload[key]).strip()
        P.save_config(self.cfg)
        if self.session:
            self.session.config = self.cfg
        return self.info()

    # ---- projects
    def open(self, path):
        path = Path(str(path)).expanduser().resolve()
        if not P.is_project(path):
            raise P.ProjectError(f"{path} is not a Character Studio project (no character.json and rig.json)")
        if not self.cfg.get("iso"):
            raise P.ProjectError("set your Melee disc image in Settings first: the studio reads the base fighters from it")
        with self.lock:
            session = Session(path, self.cfg["iso"], self.cfg)   # reads the base fighter: may take a few seconds
            self.session = session
            self.cfg = P.remember(path, self.cfg); session.config = self.cfg
        return self.info()

    def open_example(self, example_id):
        """Open your copy of an example (copied into the projects folder the first time)."""
        return self.open(P.example_copy(example_id, self.cfg))

    def close(self):
        with self.lock:
            self.session = None
        return self.info()

    def upload(self, name, rfile, length):
        ext = Path(str(name)).suffix.lower()
        if ext not in P.MODEL_TYPES:
            raise P.ProjectError(f"unsupported model type {ext or '(none)'}: use .glb, .obj, or a .zip of a .gltf with its files")
        if length > P.MAX_MODEL_BYTES:
            raise P.ProjectError("the model is larger than 256 MB")
        self.uploads.mkdir(parents=True, exist_ok=True)
        for old in self.uploads.iterdir():   # uploads older than a day are leftovers
            if old.stat().st_mtime < time.time() - 86400:
                old.unlink(missing_ok=True)
        token = uuid.uuid4().hex
        folder = self.uploads / token; folder.mkdir()
        stem = re.sub(r"[^A-Za-z0-9 ._-]+", "_", Path(str(name)).stem)[:60] or "model"
        dest = folder / (stem + ext); left = length
        with open(dest, "wb") as out:
            while left > 0:
                chunk = rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                out.write(chunk); left -= len(chunk)
        return {"upload": token, "name": stem}

    def new(self, payload):
        folder = None
        if payload.get("upload"):
            folder = self.uploads / Path(str(payload["upload"])).name
            files = list(folder.iterdir()) if folder.is_dir() else []
            if not files:
                raise P.ProjectError("the uploaded model is gone; add it again")
            model = files[0]
        elif payload.get("model"):
            model = Path(str(payload["model"])).expanduser()
            if not model.is_file():
                raise P.ProjectError(f"no model at {model}")
        else:
            raise P.ProjectError("choose a model to start from")
        try:
            parent = Path(payload.get("folder") or P.projects_dir(self.cfg))
            project = P.create_project(parent, payload.get("name") or model.stem, payload.get("base_fighter") or "mario", model,
                                       author=payload.get("author", ""), rotate_y=int(payload.get("rotate_y") or 0),
                                       z_up=bool(payload.get("z_up")), install=payload.get("install", "new"))
        finally:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
        return self.open(project)

    def save_as(self, payload):
        if not self.session:
            raise P.ProjectError("no project is open")
        dest = P.duplicate(self.session.project, payload.get("folder") or self.session.project.parent, payload.get("name") or self.session.project.name)
        return self.open(dest)

    # ---- roster
    def roster(self):
        doc = P.load_user_roster(self.cfg)
        return {**doc, "file": str(P.roster_file(self.cfg)), "entries": [P.summary(c) for c in doc["characters"]],
                "jobs": [j.view() for j in self.jobs.values()][-5:]}

    def set_roster(self, payload):
        P.save_user_roster(payload, self.cfg)
        return self.roster()

    def add_examples(self):
        """Put every example into the roster (as your copies), except where its slot is taken."""
        doc = P.load_user_roster(self.cfg)
        taken = {P.summary(c).get("base_fighter") for c in doc["characters"]}
        chars = list(doc["characters"])
        for e in P.examples():
            if e.get("base_fighter") in taken:
                continue
            chars.append(str(P.example_copy(e["id"], self.cfg).resolve())); taken.add(e.get("base_fighter"))
        P.save_user_roster({**doc, "characters": chars}, self.cfg)
        return self.roster()

    def build_roster(self, launch=False):
        from .roster import build_roster
        doc = P.load_user_roster(self.cfg)
        if not doc["characters"]:
            raise P.ProjectError("the roster is empty: add characters first")
        if not self.cfg.get("iso"):
            raise P.ProjectError("set your Melee disc image in Settings first")
        mm = self.cfg.get("pascalpatch", {})
        out = (Path(mm["root"]) / "build" / doc["id"]) if mm.get("root") else P.HOME / "out" / doc["id"]
        profile = (Path(mm["root"]) / "profiles" / f"{doc['id']}.json") if mm.get("root") else None
        cfg = self.cfg

        def work(log):
            result = build_roster(P.roster_file(cfg), cfg["iso"], out, profile=profile, log=log)
            if profile and mm.get("repo"):
                log("PascalPatch: building the profile...")
                r = pascalpatch_cli(cfg, ["build", doc["id"]]); log((r["stdout"] or r["stderr"]).strip()[-1500:])
                if r["exit"] != 0:
                    raise RuntimeError("PascalPatch could not build the roster profile")
                if launch:
                    if not mm.get("port"):
                        raise RuntimeError("set melee_port.exe in Settings to launch")
                    args = ["launch", doc["id"], "--runtime", "native", "--no-build", "--allow-unsafe", "--port", mm["port"]]
                    if mm.get("port_cwd"): args += ["--port-cwd", mm["port_cwd"]]
                    pascalpatch_cli(cfg, args, wait=False); log("Game starting in a new window (offline).")
            return {"profile": result.get("profile"), "failures": result["failures"], "output": result["output"]}

        job = Job(f"Build roster: {doc['name']}" + (" and play" if launch else ""), work)
        self.jobs[job.id] = job
        return job.view()


class Handler(BaseHTTPRequestHandler):
    studio: Studio = None
    origin = ""

    @property
    def session(self):
        s = self.studio.session
        if s is None:
            raise P.ProjectError("no project is open")
        return s

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)

    def _json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _guard(self, fn):
        try:
            self._send(200, fn())
        except Exception as exc:
            if not isinstance(exc, (P.ProjectError, FileNotFoundError)):
                traceback.print_exc()
            self._send(400, {"error": str(exc)})

    def _local(self):
        """Only the studio's own page may call it, not another site open in a browser."""
        own = self.origin.split("//", 1)[1]; hosts = (own, own.replace("127.0.0.1", "localhost"))
        origin = self.headers.get("Origin")
        return self.headers.get("Host", "") in hosts and (origin is None or origin.split("//", 1)[-1] in hosts)

    def do_GET(self):
        if not self._local():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path); q = parse_qs(url.query); st = self.studio
        if url.path == "/api/studio": return self._guard(st.info)
        if url.path == "/api/browse": return self._guard(lambda: P.browse(q.get("folder", [None])[0]))
        if url.path == "/api/roster": return self._guard(st.roster)
        if url.path == "/api/job": return self._guard(lambda: st.jobs[q["id"][0]].view())
        if url.path == "/api/project/picture":
            try:
                path = st.picture(q["path"][0], q.get("which", ["icon"])[0])
            except Exception as exc:
                return self._send(404, {"error": str(exc)})
            return self._send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png")
        if url.path == "/api/state": return self._guard(lambda: self.session.state())
        if url.path == "/api/animation": return self._guard(lambda: self.session.animation(q["name"][0]))
        if url.path == "/api/moves": return self._guard(lambda: self.session.moves())
        if url.path == "/api/cast": return self._guard(lambda: cast_data(self.session.iso))
        if url.path == "/api/fighters": return self._guard(lambda: {"fighters": sorted(FIGHTER_CODES)})
        if url.path == "/api/fighter_moves": return self._guard(lambda: self.session.fighter_moves(q["fighter"][0]))
        if url.path == "/api/borrow_preview": return self._guard(lambda: self.session.borrow_preview(q["fighter"][0], q["action"][0]))
        if url.path == "/api/texture":
            s = st.session
            if not s or not s.mesh.texture: return self._send(404, {"error": "no texture"})
            return self._send(200, s.mesh.texture.read_bytes(), mimetypes.guess_type(s.mesh.texture.name)[0] or "image/png")
        if url.path == "/api/portrait":
            try:
                path = self.session.portrait_file(source="source" in q, icon="icon" in q, stock="stock" in q)
            except (FileNotFoundError, P.ProjectError) as exc:
                return self._send(404, {"error": str(exc)})
            return self._send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        # pages: the editor when a project is open, the start screen when none is
        name = url.path.lstrip("/")
        if name in ("", "index.html", "start.html"):
            name = "index.html" if st.session else "start.html"
        path = (WEB / name).resolve()
        if WEB.resolve() not in path.parents or not path.is_file():
            return self._send(404, {"error": "not found"})
        ctype = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
                 ".png": "image/png"}.get(path.suffix, "application/octet-stream")
        self._send(200, path.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))

    def do_POST(self):
        if not self._local():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path); q = parse_qs(url.query); st = self.studio
        if url.path == "/api/upload":
            return self._guard(lambda: st.upload(q.get("name", ["model"])[0], self.rfile, int(self.headers.get("Content-Length") or 0)))
        if url.path == "/api/project/open": return self._guard(lambda: st.open(self._json()["path"]))
        if url.path == "/api/project/new": return self._guard(lambda: st.new(self._json()))
        if url.path == "/api/example/open": return self._guard(lambda: st.open_example(self._json()["id"]))
        if url.path == "/api/roster/examples": return self._guard(st.add_examples)
        if url.path == "/api/project/save_as": return self._guard(lambda: st.save_as(self._json()))
        if url.path == "/api/project/close": return self._guard(st.close)
        if url.path == "/api/project/forget": return self._guard(lambda: (P.forget(self._json()["path"], st.cfg), st.info())[1])
        if url.path == "/api/settings": return self._guard(lambda: st.settings(self._json()))
        if url.path == "/api/roster": return self._guard(lambda: st.set_roster(self._json()))
        if url.path == "/api/roster/build": return self._guard(lambda: st.build_roster(bool(self._json().get("launch"))))
        if url.path == "/api/autosave": return self._guard(lambda: P.write_autosave(self.session.project, self._json()))
        if url.path == "/api/autosave/discard": return self._guard(lambda: (P.clear_autosave(self.session.project), {"ok": True})[1])
        if url.path == "/api/retarget": return self._guard(lambda: self.session.retarget(self._json()))
        if url.path == "/api/save": return self._guard(lambda: self.session.save(self._json()))
        if url.path == "/api/moves": return self._guard(lambda: self.session.moves(self._json().get("moveset")))
        if url.path == "/api/review": return self._guard(lambda: self.session.review(self._json()))
        if url.path == "/api/portrait": return self._guard(lambda: self.session.set_portrait(self._json()))
        if url.path == "/api/export": return self._guard(lambda: self.session.export())
        if url.path == "/api/launch": return self._guard(lambda: self.session.launch())
        if url.path == "/api/test_in_game": return self._guard(lambda: self.session.test_in_game(self._json()))
        self._send(404, {"error": "not found"})


class StudioServer(ThreadingHTTPServer):
    allow_reuse_address = False   # on Windows it would let a second studio share the port silently
    daemon_threads = True


def serve(project=None, iso=None, port=8765, open_browser=True, pascalpatch=None, window=False):
    """Serve the studio; with ``project`` it opens straight into the editor, without one on the start screen."""
    cfg = load_config()
    if "meleemod" in cfg:   # settings saved before MeleeMod was renamed PascalPatch
        cfg.setdefault("pascalpatch", {}).update(cfg.pop("meleemod"))
    if iso: cfg["iso"] = str(Path(iso).resolve())
    if pascalpatch:
        pascalpatch = dict(pascalpatch); disc = pascalpatch.pop("iso", None)
        if disc and not cfg.get("iso") and Path(disc).is_file():   # PascalPatch's profile disc: no need to ask for it again
            cfg["iso"] = str(Path(disc).resolve())
        cfg.setdefault("pascalpatch", {}).update({k: v for k, v in pascalpatch.items() if v})
    save_config(cfg)
    studio = Studio(cfg)
    if project:
        if not cfg.get("iso"):
            raise SystemExit("pass --iso with your own GALE01 Rev.02 disc image once; it is remembered in " + str(CONFIG))
        studio.open(project)
    Handler.studio = studio
    try:
        server = StudioServer(("127.0.0.1", port), Handler)
    except OSError:
        if not (window and not project):
            raise
        # the studio is already running (opened from PascalPatch twice): show it rather than fail
        from .window import open_window
        open_window(f"http://127.0.0.1:{port}/", "Character Studio", P.HOME, size=(1440, 900))
        return
    Handler.origin = f"http://127.0.0.1:{server.server_address[1]}"
    url = Handler.origin + "/"
    print(f"Character Studio: {url}  (Ctrl+C to stop)", flush=True)
    if window:
        from .window import open_window
        threading.Thread(target=server.serve_forever, daemon=True).start()
        open_window(url, "Character Studio", P.HOME, size=(1440, 900))
        server.shutdown()
        return
    if open_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
