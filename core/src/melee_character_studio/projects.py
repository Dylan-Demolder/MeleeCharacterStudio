"""Character Studio projects: create them from a model, open, save as, autosave, and rosters.

A project is a folder with ``character.json``, ``moveset.json``, ``rig.json`` and the model
under ``model/``. This module is the desktop studio's file layer:

* ``import_model`` brings a model into a project as a plain ``.gltf`` with external buffers and
  PNG textures, which is what the pipeline reads. It accepts ``.gltf`` (with its files),
  ``.glb``, ``.obj`` (with ``.mtl`` and textures) and ``.zip`` archives holding any of those.
* ``estimate_landmarks`` makes a first guess at the 20 joint landmarks from the mesh's shape,
  which the Fit step then refines.
* ``create_project`` puts the two together; ``save_as`` copies a project under a new name.
* Autosave keeps unsaved edits in ``.studio/autosave.json`` inside the project, so a crash
  or a closed window loses nothing; saving clears it.
* The studio's own settings, recent projects and roster live in its home folder.
"""
from __future__ import annotations

import base64
import json
import math
import re
import shutil
import struct
import time
import zipfile
from pathlib import Path

from .game_source import FIGHTER_CODES, cache_root
from .hsd_texture import decode_png_rgba, rgba_png
from .model_kit import LANDMARKS

HOME = cache_root().parent
CONFIG = HOME / "config.json"
MODEL_TYPES = (".gltf", ".glb", ".obj", ".zip")
MAX_MODEL_BYTES = 256 * 1024 * 1024
AUTOSAVE = Path(".studio") / "autosave.json"
EXAMPLES = Path(__file__).resolve().parents[3] / "examples"   # the checkout's (or the release's) examples/


class ProjectError(ValueError):
    pass


# ------------------------------------------------------------------ settings and recents
def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG)


def projects_dir(cfg=None):
    """Where new projects go unless the user picks another folder.

    Not PascalPatch's folder: a release unzips to a new folder on each update, and the
    projects must not stay behind in the old one.
    """
    cfg = cfg if cfg is not None else load_config()
    if cfg.get("projects_dir"):
        return Path(cfg["projects_dir"])
    return Path.home() / "Documents" / "Character Studio"


def is_project(path):
    p = Path(path)
    return (p / "character.json").is_file() and (p / "rig.json").is_file()


def remember(path, cfg=None):
    """Put a project at the top of the recent list (and return the config)."""
    cfg = cfg if cfg is not None else load_config()
    path = str(Path(path).resolve())
    recent = [p for p in cfg.get("recent", []) if p != path]
    cfg["recent"] = [path] + recent[:19]
    save_config(cfg)
    return cfg


def forget(path, cfg=None):
    cfg = cfg if cfg is not None else load_config()
    path = str(Path(path).resolve())
    cfg["recent"] = [p for p in cfg.get("recent", []) if p != path]
    save_config(cfg)
    return cfg


def summary(path):
    """What the start screen shows for a project."""
    p = Path(path)
    try:
        character = json.loads((p / "character.json").read_text(encoding="utf-8"))
        rig = json.loads((p / "rig.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"path": str(p), "missing": True, "error": str(exc), "name": p.name}
    stamp = max((f.stat().st_mtime for f in (p / "character.json", p / "moveset.json", p / "rig.json") if f.is_file()), default=0)
    picture = next((character[k] for k in ("icon", "portrait") if character.get(k) and (p / character[k]).is_file()), None)
    return {"path": str(p), "id": character.get("id", p.name), "name": character.get("display_name", p.name),
            "base_fighter": rig.get("base_fighter"), "updated": int(stamp), "picture": picture,
            "install": character.get("install", "new"), "autosave": autosave_info(p) is not None}


def recent_projects(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return [summary(p) if is_project(p) else {"path": p, "missing": True, "name": Path(p).name} for p in cfg.get("recent", [])]


def browse(folder=None):
    """A folder listing for the Open dialog: sub-folders, marking which are projects."""
    folder = Path(folder).expanduser() if folder else projects_dir()
    if not folder.is_dir():
        folder = Path.home()
    folder = folder.resolve()
    entries = []
    try:
        for child in sorted(folder.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            try:
                entries.append({"name": child.name, "path": str(child), "project": is_project(child)})
            except OSError:
                continue
    except OSError as exc:
        raise ProjectError(f"cannot read {folder}: {exc}") from exc
    roots = [str(Path.home()), str(projects_dir())] + [f"{d}:\\" for d in "CDEFGH" if Path(f"{d}:\\").exists()]
    return {"folder": str(folder), "parent": str(folder.parent) if folder.parent != folder else None,
            "is_project": is_project(folder), "entries": entries, "places": list(dict.fromkeys(roots))}


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return slug[:40] or "character"


def _unique_folder(parent, slug):
    parent = Path(parent); candidate = parent / slug; n = 2
    while candidate.exists():
        candidate = parent / f"{slug}-{n}"; n += 1
    return candidate


# ------------------------------------------------------------------ model import
def _safe_extract(archive, dest):
    """Extract a ZIP without letting any member escape ``dest``."""
    dest = Path(dest).resolve(); total = 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = (dest / info.filename).resolve()
            if dest not in target.parents:
                raise ProjectError(f"unsafe path in archive: {info.filename}")
            total += info.file_size
            if total > MAX_MODEL_BYTES:
                raise ProjectError("the archive is too large")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _find_model(folder):
    for ext in (".gltf", ".glb", ".obj"):
        found = sorted(Path(folder).rglob(f"*{ext}"), key=lambda p: (len(p.parts), p.name))
        if found:
            return found[0]
    raise ProjectError("no .gltf, .glb or .obj model found")


def _to_png(src, dest):
    """Copy a texture as an 8-bit PNG the pipeline can read (converting other formats if PIL is there)."""
    raw = Path(src).read_bytes()
    try:
        decode_png_rgba(raw); Path(dest).write_bytes(raw); return True
    except Exception:
        pass
    try:
        from PIL import Image
    except ImportError:
        return False
    with Image.open(src) as im:
        im.convert("RGBA").save(dest, "PNG")
    return True


def _solid_png(dest, rgba=(200, 200, 205, 255), size=8):
    Path(dest).write_bytes(rgba_png({"width": size, "height": size, "rgba": bytes(rgba) * (size * size)}))


def _unpack_glb(path, out_dir):
    raw = Path(path).read_bytes()
    if raw[:4] != b"glTF" or len(raw) < 20:
        raise ProjectError("not a GLB file")
    version, length = struct.unpack_from("<II", raw, 4)
    if version != 2:
        raise ProjectError("only glTF 2.0 GLB files are supported")
    offset = 12; doc = None; binary = b""
    while offset + 8 <= min(length, len(raw)):
        size, kind = struct.unpack_from("<II", raw, offset); chunk = raw[offset + 8: offset + 8 + size]
        if kind == 0x4E4F534A: doc = json.loads(chunk.decode("utf-8"))
        elif kind == 0x004E4942: binary = chunk
        offset += 8 + size   # chunk lengths include their padding
    if doc is None:
        raise ProjectError("GLB has no JSON chunk")
    for b in doc.get("buffers", []):
        if "uri" not in b:
            b["uri"] = "__glb__"
    (out_dir / "model.bin").write_bytes(binary)
    for b in doc.get("buffers", []):
        if b["uri"] == "__glb__":
            b["uri"] = "model.bin"
    return doc


def _inline_uris(doc, base, out_dir):
    """Write data: URIs out as files, and make every image a PNG beside the model."""
    for i, b in enumerate(doc.get("buffers", [])):
        uri = b.get("uri", "")
        if uri.startswith("data:"):
            name = f"buffer{i}.bin"; (out_dir / name).write_bytes(base64.b64decode(uri.split(",", 1)[1])); b["uri"] = name
        elif uri and not (out_dir / uri).exists():
            src = (base / uri)
            if not src.is_file():
                raise ProjectError(f"the model's buffer {uri} is missing (bring the whole model folder, or a .glb or .zip)")
            (out_dir / uri).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, out_dir / uri)
    buffers = None
    for i, img in enumerate(doc.get("images", [])):
        name = f"texture{i}.png"
        if "bufferView" in img:   # embedded (GLB): pull the bytes out of the buffer
            if buffers is None:
                buffers = [(out_dir / b["uri"]).read_bytes() for b in doc["buffers"]]
            view = doc["bufferViews"][img.pop("bufferView")]
            data = buffers[view["buffer"]][view.get("byteOffset", 0): view.get("byteOffset", 0) + view["byteLength"]]
            tmp = out_dir / f"texture{i}.src"; tmp.write_bytes(data); src = tmp
        elif img.get("uri", "").startswith("data:"):
            tmp = out_dir / f"texture{i}.src"; tmp.write_bytes(base64.b64decode(img["uri"].split(",", 1)[1])); src = tmp
        else:
            src = base / img.get("uri", "")
        if not Path(src).is_file() or not _to_png(src, out_dir / name):
            _solid_png(out_dir / name)   # unreadable: a neutral stand-in keeps the model usable
        if Path(src).suffix == ".src":
            Path(src).unlink(missing_ok=True)
        img.clear(); img["uri"] = name
    return doc


def _obj_to_gltf(path, out_dir, z_up=False):
    """Convert an OBJ (with its MTL's first diffuse texture) to a one-primitive glTF."""
    path = Path(path); v = []; vt = []; vn = []; faces = []; mtllib = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        tag = parts[0]
        if tag == "v" and len(parts) >= 4:
            x, y, zc = map(float, parts[1:4]); v.append((x, zc, -y) if z_up else (x, y, zc))
        elif tag == "vt" and len(parts) >= 3:
            vt.append((float(parts[1]), 1.0 - float(parts[2])))
        elif tag == "vn" and len(parts) >= 4:
            x, y, zc = map(float, parts[1:4]); vn.append((x, zc, -y) if z_up else (x, y, zc))
        elif tag == "f" and len(parts) >= 4:
            corner = []
            for token in parts[1:]:
                idx = token.split("/")
                def ref(k, n):
                    if len(idx) <= k or not idx[k]: return None
                    i = int(idx[k]); return i - 1 if i > 0 else n + i
                corner.append((ref(0, len(v)), ref(1, len(vt)), ref(2, len(vn))))
            for i in range(1, len(corner) - 1):   # fan triangulation
                faces.append((corner[0], corner[i], corner[i + 1]))
        elif tag == "mtllib" and mtllib is None:
            mtllib = line.split(None, 1)[1].strip()
    if not faces:
        raise ProjectError("the OBJ has no faces")
    # one glTF vertex per distinct (v, vt, vn) corner
    index = {}; pos = []; uv = []; nrm = []; tris = []
    for tri in faces:
        for c in tri:
            if c not in index:
                index[c] = len(pos); pos.append(v[c[0]]); uv.append(vt[c[1]] if c[1] is not None else (0.0, 0.0))
                nrm.append(vn[c[2]] if c[2] is not None else None)
            tris.append(index[c])
    if any(n is None for n in nrm):   # missing normals: area-weighted face normals per vertex
        acc = [[0.0, 0.0, 0.0] for _ in pos]
        for i in range(0, len(tris), 3):
            a, b, c = (pos[tris[i + k]] for k in range(3))
            e1 = [b[k] - a[k] for k in range(3)]; e2 = [c[k] - a[k] for k in range(3)]
            n = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
            for k in range(3):
                for j in range(3): acc[tris[i + k]][j] += n[j]
        nrm = [tuple(c / (math.sqrt(sum(x * x for x in a)) or 1) for c in a) if nrm[i] is None else nrm[i] for i, a in enumerate(acc)]
    texture = None
    if mtllib and (path.parent / mtllib).is_file():
        for line in (path.parent / mtllib).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split(None, 1)
            if parts and parts[0].lower() == "map_kd" and len(parts) > 1:
                cand = path.parent / parts[1].strip().split()[-1]
                if cand.is_file():
                    texture = cand; break
    if not texture or not _to_png(texture, out_dir / "texture0.png"):
        _solid_png(out_dir / "texture0.png")
    blob = bytearray()
    def put(fmt, rows):
        start = len(blob)
        for r in rows: blob.extend(struct.pack(fmt, *r))
        while len(blob) % 4: blob.append(0)
        return start, len(blob) - start
    p_off, p_len = put("<3f", pos); n_off, n_len = put("<3f", nrm); t_off, t_len = put("<2f", uv); i_off, i_len = put("<I", [(i,) for i in tris])
    lo = [min(p[k] for p in pos) for k in range(3)]; hi = [max(p[k] for p in pos) for k in range(3)]
    (out_dir / "model.bin").write_bytes(bytes(blob))
    return {
        "asset": {"version": "2.0", "generator": "Character Studio OBJ import"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": path.stem}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": 3, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0}}],
        "textures": [{"source": 0}], "images": [{"uri": "texture0.png"}],
        "buffers": [{"uri": "model.bin", "byteLength": len(blob)}],
        "bufferViews": [{"buffer": 0, "byteOffset": p_off, "byteLength": p_len}, {"buffer": 0, "byteOffset": n_off, "byteLength": n_len},
                        {"buffer": 0, "byteOffset": t_off, "byteLength": t_len}, {"buffer": 0, "byteOffset": i_off, "byteLength": i_len}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3", "min": lo, "max": hi},
                      {"bufferView": 1, "componentType": 5126, "count": len(nrm), "type": "VEC3"},
                      {"bufferView": 2, "componentType": 5126, "count": len(uv), "type": "VEC2"},
                      {"bufferView": 3, "componentType": 5125, "count": len(tris), "type": "SCALAR"}],
    }


def _ensure_texture(doc, out_dir):
    """The costume writer needs a base colour texture: give untextured materials a flat one."""
    if not doc.get("materials"):
        doc["materials"] = [{}]
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                prim.setdefault("material", 0)
    for m in doc["materials"]:
        pbr = m.setdefault("pbrMetallicRoughness", {})
        if "baseColorTexture" in pbr:
            continue
        colour = pbr.get("baseColorFactor", [0.8, 0.8, 0.82, 1.0])
        name = f"flat{len(doc.setdefault('images', []))}.png"
        _solid_png(out_dir / name, tuple(int(max(0, min(1, c)) * 255) for c in colour[:3]) + (255,))
        doc["images"].append({"uri": name}); doc.setdefault("textures", []).append({"source": len(doc["images"]) - 1})
        pbr["baseColorTexture"] = {"index": len(doc["textures"]) - 1}


def import_model(source, project, *, z_up=False):
    """Copy a model into ``<project>/model/`` as ``model.gltf``; returns its path relative to the project."""
    source = Path(source); project = Path(project)
    if source.suffix.lower() not in MODEL_TYPES:
        raise ProjectError(f"unsupported model type {source.suffix or '(none)'}: use .glb, .gltf, .obj or a .zip of one")
    if source.is_file() and source.stat().st_size > MAX_MODEL_BYTES:
        raise ProjectError("the model is larger than 256 MB")
    out = project / "model"; out.mkdir(parents=True, exist_ok=True)
    work = project / ".studio" / "import"; shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    try:
        if source.suffix.lower() == ".zip":
            _safe_extract(source, work); source = _find_model(work)
        ext = source.suffix.lower()
        if ext == ".glb":
            doc = _inline_uris(_unpack_glb(source, out), source.parent, out)
        elif ext == ".gltf":
            doc = _inline_uris(json.loads(source.read_text(encoding="utf-8")), source.parent, out)
        else:
            doc = _obj_to_gltf(source, out, z_up=z_up)
        _ensure_texture(doc, out)
        doc.pop("extensionsRequired", None)
        (out / "model.gltf").write_text(json.dumps(doc) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    from .fighter_import import load_gltf_mesh
    load_gltf_mesh(out / "model.gltf")   # proves the pipeline can read it
    return "model/model.gltf"


# ------------------------------------------------------------------ landmarks
def estimate_landmarks(positions):
    """A first guess at the joint landmarks of a standing humanoid facing +Z.

    Heights come from average human proportions; the arms follow the mesh's lateral extremes
    (so T-, A- and arms-down poses all land near the hands), the legs follow each side's lower
    half, and depths follow the mesh at each height. The Fit step refines them.
    """
    pts = positions
    if len(pts) < 8:
        raise ProjectError("the model has too few vertices")
    ys = [p[1] for p in pts]; lo, hi = min(ys), max(ys); h = hi - lo or 1.0
    xs = sorted(p[0] for p in pts); cx = xs[len(xs) // 2]

    def band(y0, y1, pred=lambda p: True):
        return [p for p in pts if lo + y0 * h <= p[1] <= lo + y1 * h and pred(p)]

    def median(values, default):
        values = sorted(values)
        return values[len(values) // 2] if values else default

    def depth(y):
        return median([p[2] for p in band(y - 0.03, y + 0.03)], 0.0)

    def mid(y):
        return [round(cx, 4), round(lo + y * h, 4), round(depth(y), 4)]

    lm = {"pelvis": mid(0.53), "chest": mid(0.70), "neck": mid(0.84), "head": mid(0.91)}
    half = max(0.08 * h, median([abs(p[0] - cx) for p in band(0.84, 0.88)], 0.06 * h) * 1.9)
    for side, sign in (("r", -1), ("l", 1)):   # the character's right is at -X when it faces +Z
        shoulder = [cx + sign * max(half, 0.11 * h), lo + 0.81 * h, depth(0.81)]
        upper = [p for p in pts if p[1] > lo + 0.45 * h and (p[0] - cx) * sign > 0]
        hand = list(max(upper, key=lambda p: (p[0] - cx) * sign)) if upper else shoulder
        # arms-down: the "extreme" is barely past the shoulder, so drop the hand to hip height
        if abs(hand[0] - cx) < abs(shoulder[0] - cx) + 0.12 * h:
            hand = [shoulder[0] + sign * 0.03 * h, lo + 0.47 * h, shoulder[2]]
        d = [hand[k] - shoulder[k] for k in range(3)]
        wrist = [shoulder[k] + d[k] * 0.86 for k in range(3)]
        elbow = [shoulder[k] + d[k] * 0.45 for k in range(3)]; elbow[2] -= 0.02 * h
        lm[f"{side}_shoulder"] = [round(c, 4) for c in shoulder]
        lm[f"{side}_elbow"] = [round(c, 4) for c in elbow]
        lm[f"{side}_wrist"] = [round(c, 4) for c in wrist]
        lm[f"{side}_hand"] = [round(c, 4) for c in hand]
        leg = [p for p in pts if p[1] < lo + 0.45 * h and (p[0] - cx) * sign > 0]
        lx = median([p[0] for p in leg if lo + 0.2 * h < p[1] < lo + 0.35 * h], cx + sign * 0.09 * h)
        feet = [p for p in leg if p[1] < lo + 0.07 * h]
        toe = max(feet, key=lambda p: p[2]) if feet else [lx, lo, depth(0.03) + 0.06 * h]
        lm[f"{side}_hip"] = [round(cx + (lx - cx) * 0.9, 4), round(lo + 0.50 * h, 4), round(depth(0.5), 4)]
        lm[f"{side}_knee"] = [round(lx, 4), round(lo + 0.28 * h, 4), round(depth(0.28) + 0.01 * h, 4)]
        lm[f"{side}_ankle"] = [round(lx, 4), round(lo + 0.05 * h, 4), round(depth(0.05), 4)]
        lm[f"{side}_toe"] = [round(lx, 4), round(lo + 0.015 * h, 4), round(max(toe[2], depth(0.05) + 0.04 * h), 4)]
    return {k: lm[k] for k in LANDMARKS}


# ------------------------------------------------------------------ projects
def create_project(parent, name, base_fighter, model, *, author="", rotate_y=0, z_up=False, install="new"):
    """A new project folder under ``parent`` from a model file; returns its path."""
    if base_fighter not in FIGHTER_CODES:
        raise ProjectError(f"unknown base fighter {base_fighter!r}")
    name = str(name).strip() or Path(model).stem
    parent = Path(parent).expanduser(); parent.mkdir(parents=True, exist_ok=True)
    project = _unique_folder(parent, slugify(name))
    project.mkdir()
    try:
        rel = import_model(model, project, z_up=z_up)
        from .fighter_import import _rotate_y, load_gltf_mesh
        mesh = load_gltf_mesh(project / rel)
        positions = _rotate_y(mesh.positions, float(rotate_y)) if rotate_y else mesh.positions
        rig = {"model": rel, "credit": f"Imported from {Path(model).name}", "rotate_y_degrees": int(rotate_y),
               "base_fighter": base_fighter, "root_symbol": "auto", "host_dobjs": "auto", "template_dobj": "auto",
               "texture_size": 512, "landmarks": estimate_landmarks(positions), "landmarks_estimated": True}
        character = {"id": project.name, "display_name": name[:30], "version": "1.0.0", "author": author,
                     "license": "", "target_game_version": "GALE01-1.02", "compatibility": "offline-gameplay",
                     "description": "", "install": "replace" if install == "replace" else "new",
                     "attributes": {}, "attribute_scales": {}}
        moveset = {"base_fighter": base_fighter, "moves": []}
        for fname, doc in (("character.json", character), ("rig.json", rig), ("moveset.json", moveset)):
            (project / fname).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(project, ignore_errors=True)
        raise
    return project


def duplicate(source, parent, name):
    """``Save as``: a copy of ``source`` under ``parent`` named ``name``, with its own id."""
    source = Path(source).resolve()
    if not is_project(source):
        raise ProjectError(f"{source} is not a Character Studio project")
    dest = _unique_folder(Path(parent).expanduser(), slugify(name))
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".studio", "__pycache__"))
    doc = json.loads((dest / "character.json").read_text(encoding="utf-8"))
    doc["id"] = dest.name; doc["display_name"] = str(name).strip()[:30] or doc.get("display_name", dest.name)
    (dest / "character.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return dest


# ------------------------------------------------------------------ autosave
def write_autosave(project, payload):
    path = Path(project) / AUTOSAVE; path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"saved_at": time.time(), **{k: payload[k] for k in ("character", "moveset", "rig") if k in payload}}
    tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(doc), encoding="utf-8"); tmp.replace(path)
    return {"autosaved": int(doc["saved_at"])}


def autosave_info(project):
    """The autosave, if it holds edits newer than the project's saved files."""
    path = Path(project) / AUTOSAVE
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    saved = max((f.stat().st_mtime for f in (Path(project) / n for n in ("character.json", "moveset.json", "rig.json")) if f.is_file()), default=0)
    return doc if doc.get("saved_at", 0) > saved else None


def clear_autosave(project):
    (Path(project) / AUTOSAVE).unlink(missing_ok=True)


# ------------------------------------------------------------------ examples
def examples():
    """The example characters that come with the studio, in ``examples/roster.json``'s order."""
    try:
        order = json.loads((EXAMPLES / "roster.json").read_text(encoding="utf-8")).get("characters", [])
    except (OSError, ValueError):
        order = []
    found = sorted((p for p in EXAMPLES.iterdir() if is_project(p)), key=lambda p: p.name) if EXAMPLES.is_dir() else []
    found.sort(key=lambda p: order.index(p.name) if p.name in order else len(order))
    out = []
    for p in found:
        row = summary(p)
        try:
            row["description"] = json.loads((p / "character.json").read_text(encoding="utf-8")).get("description", "")
        except (OSError, ValueError):
            row["description"] = ""
        out.append(row)
    return out


def example_copy(example_id, cfg=None):
    """Your own copy of an example, in the projects folder; made the first time, reused after.

    The examples stay as they came (in a release they sit in the program folder), so opening
    or rostering one works on the copy.
    """
    source = EXAMPLES / Path(str(example_id)).name
    if not is_project(source):
        raise ProjectError(f"there is no example called {example_id!r}")
    dest = projects_dir(cfg) / source.name
    if is_project(dest):
        try:
            if json.loads((dest / "character.json").read_text(encoding="utf-8")).get("id") == source.name:
                return dest
        except (OSError, ValueError):
            pass
    dest = _unique_folder(dest.parent, source.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".studio", "__pycache__"))
    return dest


# ------------------------------------------------------------------ roster
def roster_file(cfg=None):
    """The studio's roster: which projects go into the game together."""
    return projects_dir(cfg) / "roster.json"


def load_user_roster(cfg=None):
    path = roster_file(cfg)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"id": "studio-roster", "name": "My roster", "characters": []}
    doc["characters"] = [str((path.parent / c).resolve()) for c in doc.get("characters", [])]
    return doc


def save_user_roster(doc, cfg=None):
    path = roster_file(cfg); path.parent.mkdir(parents=True, exist_ok=True)
    chars = []
    for c in doc.get("characters", []):
        p = Path(c).resolve()
        if not is_project(p):
            raise ProjectError(f"{p} is not a Character Studio project")
        try:
            chars.append(p.relative_to(path.parent).as_posix())
        except ValueError:
            chars.append(str(p))
    out = {"id": slugify(doc.get("id") or "studio-roster"), "name": doc.get("name") or "My roster", "characters": list(dict.fromkeys(chars))}
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return load_user_roster(cfg)
