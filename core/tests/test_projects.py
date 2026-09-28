import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from melee_character_studio import projects
from melee_character_studio.fighter_import import _rotate_y, load_gltf_mesh
from melee_character_studio.model_kit import LANDMARKS

REPO = Path(__file__).parents[2]
# An original model-kit character that ships with the studio.
NOVA = REPO / "examples/nova"


def _box(v, f, lo, hi):
    """Append an axis-aligned box to OBJ vertex/face lists."""
    base = len(v) + 1
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                v.append((x, y, z))
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    f.extend(tuple(base + i for i in q) for q in quads)


def t_pose_obj(path, z_up=False):
    """A blocky T-pose figure 2 units tall, facing +Z (Y up), as OBJ text."""
    v, f = [], []
    _box(v, f, (-0.25, 1.0, -0.12), (0.25, 1.65, 0.12))        # torso
    _box(v, f, (-0.12, 1.7, -0.12), (0.12, 2.0, 0.14))         # head
    _box(v, f, (-1.0, 1.55, -0.05), (-0.25, 1.65, 0.05))       # right arm (at -X)
    _box(v, f, (0.25, 1.55, -0.05), (1.0, 1.65, 0.05))         # left arm
    _box(v, f, (-0.22, 0.0, -0.08), (-0.05, 1.0, 0.08))        # right leg
    _box(v, f, (0.05, 0.0, -0.08), (0.22, 1.0, 0.08))          # left leg
    _box(v, f, (-0.22, 0.0, 0.08), (-0.05, 0.05, 0.25))        # right foot
    _box(v, f, (0.05, 0.0, 0.08), (0.22, 0.05, 0.25))          # left foot
    lines = [f"v {x} {-z if z_up else y} {y if z_up else z}" for x, y, z in v] + ["f " + " ".join(map(str, q)) for q in f]
    Path(path).write_text("\n".join(lines) + "\n")


def glb_from_gltf(gltf, out):
    doc = json.loads(gltf.read_text())
    blob = (gltf.parent / doc["buffers"][0]["uri"]).read_bytes()
    for i, img in enumerate(doc.get("images", [])):   # embed the textures as buffer views, like exporters do
        data = (gltf.parent / img.pop("uri")).read_bytes()
        while len(blob) % 4: blob += b"\0"
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}); blob += data
        img["bufferView"] = len(doc["bufferViews"]) - 1; img["mimeType"] = "image/png"
    while len(blob) % 4: blob += b"\0"
    doc["buffers"] = [{"byteLength": len(blob)}]
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    out.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._config = projects.CONFIG
        projects.CONFIG = self.home / "config.json"

    def tearDown(self):
        projects.CONFIG = self._config
        self.tmp.cleanup()

    def test_obj_import_and_landmarks(self):
        obj = self.home / "figure.obj"; t_pose_obj(obj)
        p = projects.create_project(self.home / "projects", "Block Guy", "mario", obj, author="me")
        self.assertTrue(projects.is_project(p)); self.assertEqual(p.name, "block-guy")
        rig = json.loads((p / "rig.json").read_text())
        mesh = load_gltf_mesh(p / rig["model"])
        self.assertEqual(len(mesh.triangles), 8 * 12)
        self.assertIsNotNone(mesh.texture)   # a flat stand-in texture, since the OBJ has none
        lm = rig["landmarks"]; self.assertEqual(set(lm), set(LANDMARKS))
        self.assertLess(lm["r_hand"][0], -0.8); self.assertGreater(lm["l_hand"][0], 0.8)   # T-pose hands at the tips
        self.assertAlmostEqual(lm["r_hand"][1], 1.6, delta=0.1)
        self.assertLess(lm["r_knee"][0], 0); self.assertGreater(lm["l_knee"][0], 0)
        self.assertGreater(lm["head"][1], lm["neck"][1]); self.assertGreater(lm["neck"][1], lm["chest"][1])
        self.assertGreater(lm["r_toe"][2], lm["r_ankle"][2])   # toes point forward (+Z)

    def test_obj_z_up_is_turned_upright(self):
        obj = self.home / "figure.obj"; t_pose_obj(obj, z_up=True)
        p = projects.create_project(self.home, "Zed", "fox", obj, z_up=True)
        mesh = load_gltf_mesh(p / "model/model.gltf")
        ys = [q[1] for q in mesh.positions]
        self.assertAlmostEqual(max(ys) - min(ys), 2.0, places=4)

    def test_gltf_glb_and_zip_import(self):
        gltf = NOVA / "model/model.gltf"
        a = projects.create_project(self.home, "Nova A", "marth", gltf, rotate_y=0)
        glb = self.home / "nova.glb"; glb_from_gltf(gltf, glb)
        b = projects.create_project(self.home, "Nova B", "marth", glb, rotate_y=0)
        zpath = self.home / "nova.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            for f in (NOVA / "model").rglob("*"):
                if f.is_file(): z.write(f, "nova/" + f.relative_to(NOVA / "model").as_posix())
        c = projects.create_project(self.home, "Nova C", "marth", zpath, rotate_y=0)
        counts = {len(load_gltf_mesh(x / "model/model.gltf").triangles) for x in (a, b, c)}
        self.assertEqual(len(counts), 1)
        # the estimate lands near the hand-fitted landmarks of the example (same model, same turn)
        fitted = json.loads((NOVA / "rig.json").read_text())["landmarks"]
        guess = json.loads((b / "rig.json").read_text())["landmarks"]
        mesh = load_gltf_mesh(b / "model/model.gltf"); ys = [q[1] for q in _rotate_y(mesh.positions, 0)]
        h = max(ys) - min(ys)
        for name in ("pelvis", "chest", "head", "r_knee", "l_knee"):
            self.assertLess(abs(guess[name][1] - fitted[name][1]) / h, 0.12, name)

    def test_zip_cannot_escape(self):
        zpath = self.home / "evil.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("../../escape.obj", "v 0 0 0\n")
        with self.assertRaises(projects.ProjectError):
            projects.create_project(self.home / "p", "Evil", "mario", zpath)
        self.assertFalse((self.home / "escape.obj").exists())
        self.assertEqual(list((self.home / "p").iterdir()), [])   # the half-made project is removed

    def test_rejects_unknown_fighter_and_type(self):
        obj = self.home / "figure.obj"; t_pose_obj(obj)
        with self.assertRaises(projects.ProjectError): projects.create_project(self.home, "X", "waluigi", obj)
        fbx = self.home / "figure.fbx"; fbx.write_bytes(b"x")
        with self.assertRaises(projects.ProjectError): projects.create_project(self.home, "X", "mario", fbx)

    def test_projects_outlive_a_pascalpatch_update(self):
        # a PascalPatch release unzips to a new folder each update: projects must not live in it
        self.assertEqual(projects.projects_dir({"pascalpatch": {"root": str(self.home / "PascalPatch-0.6.0")}}),
                         Path.home() / "Documents" / "Character Studio")
        self.assertEqual(projects.projects_dir({"projects_dir": str(self.home)}), self.home)

    def test_save_as_autosave_recent_and_roster(self):
        obj = self.home / "figure.obj"; t_pose_obj(obj)
        p = projects.create_project(self.home / "projects", "Block Guy", "mario", obj)
        copy = projects.duplicate(p, self.home / "projects", "Block Guy II")
        self.assertEqual(json.loads((copy / "character.json").read_text())["id"], "block-guy-ii")
        self.assertIsNone(projects.autosave_info(p))
        projects.write_autosave(p, {"character": {"id": "block-guy", "display_name": "Edited"}})
        self.assertEqual(projects.autosave_info(p)["character"]["display_name"], "Edited")
        self.assertTrue(projects.summary(p)["autosave"])
        later = (p / "character.json").stat().st_mtime + 5
        os.utime(p / "character.json", (later, later))   # a save after the autosave makes it stale
        self.assertIsNone(projects.autosave_info(p))
        projects.clear_autosave(p); self.assertFalse((p / ".studio/autosave.json").exists())
        cfg = {"projects_dir": str(self.home / "projects")}
        projects.remember(p, cfg); projects.remember(copy, cfg); projects.remember(p, cfg)
        self.assertEqual([r["path"] for r in projects.recent_projects(cfg)], [str(p.resolve()), str(copy.resolve())])
        roster = projects.save_user_roster({"name": "Mine", "characters": [str(p), str(copy), str(p)]}, cfg)
        self.assertEqual(roster["characters"], [str(p.resolve()), str(copy.resolve())])
        self.assertEqual(json.loads(projects.roster_file(cfg).read_text())["characters"], ["block-guy", "block-guy-ii"])
        listing = projects.browse(self.home / "projects")
        self.assertEqual({e["name"]: e["project"] for e in listing["entries"]}, {"block-guy": True, "block-guy-ii": True})


if __name__ == "__main__":
    unittest.main()
