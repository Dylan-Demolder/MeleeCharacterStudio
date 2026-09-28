import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from melee_character_studio import base_skeleton
from melee_character_studio.attributes import validate_attribute_scales, validate_attributes
from melee_character_studio.fighter_import import Mesh, load_gltf_mesh, retarget_mesh
from melee_character_studio.fighter_moves import ELEMENTS, FighterData, FighterMoveError, apply_moveset, find_actions
from melee_character_studio.hsd_archive import build_hsd_archive
from melee_character_studio.hsd_model import HsdJoint
from melee_character_studio.model_kit import ModelKitError, build_model, complete_landmarks, mirror_part, write_gltf
from melee_character_studio.package_runtime import compose_fighter_slot

REPO = Path(__file__).parents[2]

# depth-first humanoid test skeleton: (part name, parent index, position)
SKELETON = (
    ("TopN", None, (0, 0, 0)), ("TransN", 0, (0, 0, 0)), ("XRotN", 1, (0, 0, 0)), ("YRotN", 2, (0, 0, 0)),
    ("HipN", 3, (0, 10, 0)), ("WaistN", 4, (0, 11, 0)),
    ("LLegJ", 4, (1, 9.5, 0)), ("LKneeJ", 6, (1, 5, 0)), ("LFootJ", 7, (1, 1, 0)),
    ("RLegJ", 4, (-1, 9.5, 0)), ("RKneeJ", 9, (-1, 5, 0)), ("RFootJ", 10, (-1, 1, 0)),
    ("BustN", 5, (0, 13, 0)), ("NeckN", 12, (0, 16, 0)), ("HeadN", 13, (0, 17, 0)),
    ("LShoulderN", 12, (0.5, 15.5, 0)), ("LShoulderJ", 15, (2, 15.5, 0)), ("LArmJ", 16, (4, 15.5, 0)), ("LHandN", 17, (6, 15.5, 0)),
    ("RShoulderN", 12, (-0.5, 15.5, 0)), ("RShoulderJ", 19, (-2, 15.5, 0)), ("RArmJ", 20, (-4, 15.5, 0)), ("RHandN", 21, (-6, 15.5, 0)),
)
VIRTUAL_PART = 23  # one extra parts entry with no JObj, listed after the joints


def _joints():
    joints = []
    for i, (name, parent, pos) in enumerate(SKELETON):
        children = tuple(k for k, s in enumerate(SKELETON) if s[1] == i)
        envelope = () if i < 4 else (1, 0, 0, -pos[0], 0, 1, 0, -pos[1], 0, 0, 1, -pos[2])
        joints.append(HsdJoint(i, name, 2 if i == 0 else 0, (0, 0, 0), (1, 1, 1), pos, parent, children, envelope))
    return joints


def _plco(kind):
    """ftLoadCommonData with a parts table (x10) and virtual-part list (x14) for ``kind``."""
    body = bytearray(0x800)
    tables, entry, p2j, j2p, skips, skip, items = 0x40, 0x100, 0x200, 0x280, 0x300, 0x400, 0x420
    struct.pack_into(">II", body, 0x10, tables, skips)
    struct.pack_into(">I", body, tables + 4 * kind, entry)
    struct.pack_into(">III", body, entry, j2p, p2j, len(SKELETON) + 1)
    mapping = [0xFF] * len(base_skeleton.PART_NAMES)
    for index, (name, _, _) in enumerate(SKELETON):
        mapping[base_skeleton.PART[name]] = index
    mapping[base_skeleton.PART["ThrowN"]] = VIRTUAL_PART
    body[p2j:p2j + len(mapping)] = bytes(mapping)
    struct.pack_into(">I", body, skips + 4 * kind, skip)
    struct.pack_into(">II", body, skip, items, 1)
    body[items] = VIRTUAL_PART
    relocs = (0x10, 0x14, tables + 4 * kind, entry, entry + 4, skips + 4 * kind, skip)
    return build_hsd_archive(body, relocations=relocs, publics=((0, "ftLoadCommonData"),))


class BaseSkeletonTests(unittest.TestCase):
    def test_parts_map_reads_joints_and_skips_virtual_parts(self):
        parts = base_skeleton.read_parts_map(_plco(base_skeleton.FIGHTER_KINDS["jigglypuff"]), "jigglypuff")
        self.assertEqual(parts.virtual_parts, (VIRTUAL_PART,))
        self.assertEqual(parts.joint("HipN"), 4)
        self.assertEqual(parts.joint("RHandN"), 22)
        self.assertIsNone(parts.joint("ThrowN"))   # virtual
        self.assertIsNone(parts.joint("L1stNa"))   # absent
        with self.assertRaises(base_skeleton.BaseSkeletonError):
            base_skeleton.read_parts_map(_plco(base_skeleton.FIGHTER_KINDS["jigglypuff"]), "fox")

    def test_part_numbering_matches_the_disc(self):
        # GALE01 PlCo.dat has one more part than the decomp enum, after RFootJ
        # (e.g. Captain Falcon: part 17 -> chest joint 19, 21 -> elbow joint 24).
        self.assertEqual([base_skeleton.PART[n] for n in ("RFootJ", "SpineN", "BustN", "LArmJ", "NeckN", "HeadN", "RHandN", "ThrowN", "TransN2")],
                         [15, 16, 17, 21, 34, 35, 40, 52, 53])

    def test_head_is_found_from_the_bind_pose_when_the_table_lacks_it(self):
        joints = _joints(); positions = [s[2] for s in SKELETON]
        mapping = [0xFF] * len(base_skeleton.PART_NAMES)
        for index, (name, _, _) in enumerate(SKELETON):
            if name not in ("NeckN", "HeadN"):
                mapping[base_skeleton.PART[name]] = index
        parts = base_skeleton.PartsMap(25, len(SKELETON), tuple(mapping), ())
        table = base_skeleton.auto_segments(parts, joints, positions)
        rows = {r[0]: r for r in table.rows}
        self.assertEqual(rows["head"][3:5], (13, 14))
        self.assertEqual(rows["chest"][4], 13)
        self.assertTrue(any("head found from bind pose" in n for n in table.notes))

    def test_auto_segments_build_a_humanoid_table_with_synthetic_ends(self):
        parts = base_skeleton.read_parts_map(_plco(15), "jigglypuff")
        joints = _joints(); positions = [s[2] for s in SKELETON]
        table = base_skeleton.auto_segments(parts, joints, positions)
        rows = {r[0]: r for r in table.rows}
        self.assertEqual(len(rows), 17)
        self.assertEqual(rows["r_upper"][3:6], (20, 21, 20))
        self.assertEqual(rows["pelvis"][3:5], (4, 12))
        self.assertEqual(table.torso, {"up": (4, 13), "right": (9, 6)})
        # no finger or toe joints: hands and feet end at synthetic points
        for name in ("r_hand", "l_hand", "r_foot", "l_foot"):
            self.assertGreaterEqual(rows[name][4], len(joints))
        toe = table.points[rows["r_foot"][4]]
        self.assertGreater(toe[2], 0)
        self.assertTrue(any("synthetic toe" in n for n in table.notes))

    def test_derived_table_retargets_a_matching_mesh_in_place(self):
        parts = base_skeleton.read_parts_map(_plco(15), "jigglypuff")
        joints = _joints(); positions = [s[2] for s in SKELETON]
        table = base_skeleton.auto_segments(parts, joints, positions)
        lm = {"pelvis": (0, 10, 0), "chest": (0, 13, 0), "neck": (0, 16, 0), "head": (0, 17, 0),
              "r_shoulder": (-2, 15.5, 0), "r_elbow": (-4, 15.5, 0), "r_wrist": (-6, 15.5, 0), "r_hand": (-7, 15.5, 0),
              "l_shoulder": (2, 15.5, 0), "l_elbow": (4, 15.5, 0), "l_wrist": (6, 15.5, 0), "l_hand": (7, 15.5, 0),
              "r_hip": (-1, 9.5, 0), "r_knee": (-1, 5, 0), "r_ankle": (-1, 1, 0), "r_toe": (-1, 0.2, 1),
              "l_hip": (1, 9.5, 0), "l_knee": (1, 5, 0), "l_ankle": (1, 1, 0), "l_toe": (1, 0.2, 1)}
        points = [(-3, 15.6, 0.2), (3, 15.4, 0.1), (-1, 7, 0.1), (0, 12, 0.3)]
        mesh = Mesh(points, [(0, 0, 1)] * 4, [(0, 0)] * 4, [(0, 1, 2), (1, 2, 3)], None)
        rigged, info = retarget_mesh(mesh, lm, positions, "jigglypuff", smoothing=0, table=table)
        self.assertAlmostEqual(info["scale"], 1.0, places=6)
        for before, after in zip(points, rigged.positions):
            self.assertTrue(all(abs(before[k] - after[k]) < 1e-6 for k in range(3)), (before, after))
        owners = {j for w in rigged.weights for j, _ in w}
        self.assertTrue(all(joints[j].envelope_matrix for j in owners))

    def test_rigid_mode_keeps_shape_and_uses_one_owner(self):
        parts = base_skeleton.read_parts_map(_plco(1), "fox")
        joints = _joints(); positions = [s[2] for s in SKELETON]
        table = base_skeleton.auto_segments(parts, joints, positions)
        lm = complete_landmarks({"pelvis": (0, 10, 0), "chest": (0, 13, 0), "neck": (0, 16, 0), "head": (0, 17, 0),
                                 "r_shoulder": (-2, 15.5, 0), "r_elbow": (-4, 15.5, 0), "r_wrist": (-6, 15.5, 0), "r_hand": (-7, 15.5, 0),
                                 "r_hip": (-1, 9.5, 0), "r_knee": (-1, 5, 0), "r_ankle": (-1, 1, 0), "r_toe": (-1, 0.2, 1)})
        car = [(-3, 1, -5), (3, 1, -5), (3, 1, 5), (-3, 4, 5)]
        mesh = Mesh(car, [(0, 1, 0)] * 4, [(0, 0)] * 4, [(0, 1, 2), (0, 2, 3)], None)
        rigged, _ = retarget_mesh(mesh, lm, positions, "fox", smoothing=2, table=table, rigid="pelvis", scale_factor=0.5)
        owner = next(r[5] for r in table.rows if r[0] == "pelvis")   # WaistN carries the envelope
        self.assertEqual({w for w in rigged.weights}, {((owner, 1.0),)})
        d_src = math.dist(car[0], car[2]); d_dst = math.dist(rigged.positions[0], rigged.positions[2])
        self.assertAlmostEqual(d_dst / d_src, 0.5, places=6)

    def test_referenced_dobjs_reads_visibility_lookups(self):
        body = bytearray(0x200)
        desc, vis, lookup, variants, idx = 0x40, 0x60, 0x80, 0xA0, 0xC0
        struct.pack_into(">I", body, 0x08, desc)
        struct.pack_into(">II", body, desc, 1, vis)            # model_num 1, vis_table
        struct.pack_into(">I", body, vis, lookup)              # row 0, lookup 0
        struct.pack_into(">II", body, lookup, 2, variants)     # two variants
        struct.pack_into(">IIII", body, variants, 1, idx, 2, idx + 4)
        body[idx] = 3; body[idx + 4:idx + 6] = bytes((5, 6))
        raw = build_hsd_archive(body, relocations=(0x08, desc + 4, vis, lookup + 4, variants + 4, variants + 12), publics=((0, "ftDataTest"),))
        self.assertEqual(base_skeleton.referenced_dobjs(raw), {3, 5, 6})


def _moves_archive(alias=False):
    """Two special actions (SpecialNStart with a hitbox, SpecialNLoop without, or a Goto into it)."""
    body = bytearray(0x300)
    struct.pack_into(">I", body, 0x14, 0x80)
    names = {0: (0x100, b"PlyTest_Share_ACTION_SpecialNStart_figatree\0", 0x200), 1: (0x140, b"PlyTest_Share_ACTION_SpecialNLoop_figatree\0", 0x240)}
    relocs = [0x14]
    for i, (name_at, name, script) in names.items():
        struct.pack_into(">I", body, 0x80 + 0x18 * i, name_at); struct.pack_into(">I", body, 0x80 + 0x18 * i + 0x0C, script)
        body[name_at:name_at + len(name)] = name; relocs += [0x80 + 0x18 * i, 0x80 + 0x18 * i + 0x0C]
    words = [(11 << 26) | (5 << 11) | 10, (800 << 16), 0, (45 << 23) | (100 << 14), (20 << 23)]
    for i, w in enumerate(words):
        struct.pack_into(">I", body, 0x200 + 4 * i, w)
    if alias:
        struct.pack_into(">II", body, 0x240, 7 << 26, 0x200); relocs.append(0x244)
    return build_hsd_archive(body, relocations=tuple(relocs), publics=((0, "ftDataTest"),))


class GlobTuningTests(unittest.TestCase):
    def test_glob_tunes_matching_actions_and_reports_misses(self):
        data = FighterData(_moves_archive())
        self.assertEqual(len(find_actions(data, "SpecialN*")), 2)
        skipped = []
        changes = apply_moveset(data, {"moves": [
            {"name": "Gamma Punch", "slot": "special_neutral", "actions": [
                {"action": "SpecialN*", "tuning": {"damage_scale": 1.5, "size_scale": 1.25, "element": "dark", "knockback_scale": 1.1}},
                {"action": "SpecialAirHi*", "tuning": {"damage": 9}}]}]}, skipped=skipped)
        self.assertEqual(len(changes), 1)
        self.assertEqual(skipped[0]["action"], "SpecialAirHi*")
        box = FighterData(bytes(data.raw)).hitboxes(0x200)[0]
        self.assertEqual((box["damage"], box["size"], box["element"], box["knockback_growth"], box["base_knockback"]),
                         (15, 1000, ELEMENTS["dark"], 110, 22))

    def test_goto_alias_is_tuned_once_and_not_reported(self):
        data = FighterData(_moves_archive(alias=True))
        skipped = []
        changes = apply_moveset(data, {"moves": [{"name": "Gamma Punch", "slot": "special_neutral", "actions": [
            {"action": "SpecialNStart", "tuning": {"damage_scale": 2.0}},
            {"action": "SpecialNLoop*", "tuning": {"damage_scale": 2.0}}]}]}, skipped=skipped)
        self.assertEqual((len(changes), skipped), (1, []))
        self.assertEqual(FighterData(bytes(data.raw)).hitboxes(0x200)[0]["damage"], 20)

    def test_unknown_element_is_rejected(self):
        data = FighterData(_moves_archive())
        with self.assertRaises(FighterMoveError):
            apply_moveset(data, {"moves": [{"name": "x", "slot": "s", "action": "SpecialNStart", "tuning": {"element": "plasma"}}]})


class AttributeScaleTests(unittest.TestCase):
    def test_scales_multiply_the_base_value(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); package = root / "p.melee-character"; package.mkdir()
            character = {"id": "t", "version": "1", "target_game_version": "GALE01-1.02", "compatibility": "offline-gameplay",
                         "attributes": {"gravity": 0.1}, "attribute_scales": {"size": 1.5, "max_jumps": 3}}
            (package / "character.json").write_text(json.dumps(character)); (package / "moveset.json").write_text('{"moves": []}')
            (package / "checksums.json").write_text(json.dumps({n: hashlib.sha256((package / n).read_bytes()).hexdigest() for n in ("character.json", "moveset.json")}))
            body = bytearray(0x60 + 0x184); struct.pack_into(">I", body, 0, 0x60)
            struct.pack_into(">f", body, 0x60 + 0x8C, 1.2); struct.pack_into(">i", body, 0x60 + 0x58, 2)
            base = root / "PlTs.dat"; base.write_bytes(build_hsd_archive(body, relocations=(0,), publics=((0, "ftDataTest"),)))
            out = compose_fighter_slot(package, base, root / "out.dat").read_bytes()
            self.assertAlmostEqual(struct.unpack_from(">f", out, 0x20 + 0x60 + 0x8C)[0], 1.8, places=5)
            self.assertEqual(struct.unpack_from(">i", out, 0x20 + 0x60 + 0x58)[0], 6)

    def test_scale_validation(self):
        self.assertEqual(validate_attribute_scales({"size": 1.2}), [])
        self.assertTrue(validate_attribute_scales({"size": 0}))
        self.assertTrue(validate_attribute_scales({"kirby_star": 1.0}))
        core = {"walk_speed": 1, "dash_speed": 1.5, "run_speed": 1.6, "gravity": 0.1, "fall_speed": 2, "weight": 100, "air_speed": 1, "jump_velocity": 3}
        self.assertEqual(validate_attributes(core, {}, scaled={"size": 1.1}), [])
        self.assertTrue(validate_attributes(dict(core, size=1), {}, scaled={"size": 1.1}))


SPEC = {
    "palette": {"a": "#ff0000", "b": "#00ff00"},
    "decals": {"face": {"rows": ["ab", "ba"], "colors": {"a": "a", "b": "#0000ff"}}},
    "landmarks": {"pelvis": [0, 10, 0], "chest": [0, 13, 0], "neck": [0, 16, 0], "head": [0, 17, 0],
                  "r_shoulder": [-2, 15.5, 0], "r_elbow": [-4, 15.5, 0], "r_wrist": [-6, 15.5, 0], "r_hand": [-7, 15.5, 0],
                  "r_hip": [-1, 9.5, 0], "r_knee": [-1, 5, 0], "r_ankle": [-1, 1, 0], "r_toe": [-1, 0.2, 1]},
    "parts": [
        {"shape": "sphere", "center": "head", "radius": 1.2, "color": "a", "segment": "head"},
        {"shape": "box", "center": {"at": "chest", "offset": [0, 1, 0]}, "size": [3, 4, 2], "faces": {"front": "face"}, "color": "b", "segment": "chest"},
        {"shape": "capsule", "from": "r_shoulder", "to": "r_elbow", "radius": [0.6, 0.5], "color": "a", "segment": "r_upper", "mirror": True},
        {"shape": "box", "from": "r_knee", "to": "r_ankle", "size": [1, 1], "color": "b", "segment": "r_shin", "mirror": True},
        {"shape": "cylinder", "from": "r_hip", "to": "r_knee", "radius": 0.7, "sides": 6, "color": "a", "segment": "r_thigh", "mirror": True},
        {"shape": "cone", "from": {"at": "head", "offset": [0, 1, 0]}, "to": {"at": "head", "offset": [0, 3, 0]}, "radius": 0.5, "color": "b", "segment": "head"},
        {"shape": "extrude", "origin": {"at": "chest", "offset": [-1, 0, -1.2]}, "u": [-1, 0, 0], "v": [0, 1, 0],
         "points": [[0, 0], [4, 1], [5, 4], [2, 3], [0, 2]], "thickness": 0.2, "color": "a", "segment": "chest", "mirror": True},
    ],
}


def _signed_volume(mb, first, last):
    """Six times the enclosed volume of the triangles whose vertices lie in [first, last]."""
    total = 0.0
    for a, b, c in mb.triangles:
        if first <= a <= last:
            pa, pb, pc = mb.positions[a], mb.positions[b], mb.positions[c]
            total += (pa[0] * (pb[1] * pc[2] - pb[2] * pc[1]) - pa[1] * (pb[0] * pc[2] - pb[2] * pc[0]) + pa[2] * (pb[0] * pc[1] - pb[1] * pc[0]))
    return total


class ModelKitTests(unittest.TestCase):
    def test_parts_are_closed_outward_and_pinned(self):
        mb, atlas, lm, ranges = build_model(SPEC)
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], len(mb.positions) - 1)
        self.assertEqual(sum(r[1] - r[0] + 1 for r in ranges), len(mb.positions))
        self.assertEqual([r[2] for r in ranges], ["head", "chest", "r_upper", "l_upper", "r_shin", "l_shin", "r_thigh", "l_thigh", "head", "chest", "chest"])
        for first, last, name in ranges:
            self.assertGreater(_signed_volume(mb, first, last), 0, (name, first))   # closed and outward-facing
        self.assertEqual(lm["l_elbow"], (4.0, 15.5, 0.0))

    def test_mirrored_parts_are_reflections(self):
        mb, _, _, ranges = build_model(SPEC)
        right = [mb.positions[i] for i in range(ranges[2][0], ranges[2][1] + 1)]
        left = [mb.positions[i] for i in range(ranges[3][0], ranges[3][1] + 1)]
        self.assertEqual(sorted((round(-x, 5), round(y, 5), round(z, 5)) for x, y, z in right), sorted((round(x, 5), round(y, 5), round(z, 5)) for x, y, z in left))
        self.assertEqual(mirror_part({"segment": "r_fore", "faces": {"right": "x"}, "rotate": [10, 20, 30]}),
                         {"segment": "l_fore", "faces": {"left": "x"}, "rotate": [10, -20, -30]})

    def test_gltf_round_trip_and_decal_uvs(self):
        mb, atlas, _, _ = build_model(SPEC)
        with tempfile.TemporaryDirectory() as td:
            mesh = load_gltf_mesh(write_gltf(mb, atlas, td))
            self.assertEqual(len(mesh.positions), len(mb.positions))
            self.assertEqual(len(mesh.triangles), len(mb.triangles))
            self.assertIsNotNone(mesh.texture)
        u0, v0, u1, v1 = atlas.regions["face"]
        self.assertTrue(any(u0 <= uv[0] <= u1 and v0 <= uv[1] <= v1 for uv in mb.uvs))
        x, y = int(u0 * 512) + 1, int(v0 * 512) + 1
        self.assertEqual(atlas.pixels[y * 512 + x], (255, 0, 0, 255))

    def test_spec_errors_are_reported(self):
        bad = dict(SPEC, parts=[{"shape": "sphere", "center": "nose", "radius": 1}])
        with self.assertRaisesRegex(ModelKitError, "nose"):
            build_model(bad)
        with self.assertRaisesRegex(ModelKitError, "segment"):
            build_model(dict(SPEC, parts=[{"shape": "sphere", "center": "head", "segment": "tail"}]))
        with self.assertRaisesRegex(ModelKitError, "missing landmarks"):
            complete_landmarks({"pelvis": [0, 0, 0]})


class RosterProjectTests(unittest.TestCase):
    """Every example character is complete, valid and uses its own slot."""

    def test_roster_projects_validate(self):
        from melee_character_studio.exporter import validate_character
        from melee_character_studio.moveset import validate_moveset
        from melee_character_studio.roster import load_roster
        roster = REPO / "examples" / "roster.json"
        doc, projects = load_roster(roster)
        self.assertEqual(len(projects), 6)
        for project in projects:
            with self.subTest(project.name):
                character = json.loads((project / "character.json").read_text())
                moveset = json.loads((project / "moveset.json").read_text())
                rig = json.loads((project / "rig.json").read_text())
                spec = json.loads((project / "model.json").read_text())
                self.assertEqual(validate_character(character), [])
                self.assertEqual(validate_moveset(moveset), [])
                self.assertEqual(validate_attribute_scales(character.get("attribute_scales", {})), [])
                self.assertEqual(validate_attributes(character["attributes"], {}, scaled=character.get("attribute_scales", {})), [])
                self.assertEqual(moveset["base_fighter"], rig["base_fighter"])
                self.assertIn(rig["base_fighter"], base_skeleton.FIGHTER_KINDS)
                for move in moveset["moves"]:
                    for variant in move.get("actions", []):
                        element = variant.get("tuning", {}).get("element")
                        self.assertTrue(element is None or element in ELEMENTS, element)
                mb, _, lm, ranges = build_model(spec)
                self.assertLess(len(mb.positions), 0xFFFF)
                self.assertEqual(sum(r[1] - r[0] + 1 for r in ranges), len(mb.positions), "every vertex belongs to a body part")
                self.assertEqual(rig.get("segment_ranges"), ranges, "run generate-model after editing model.json")


if __name__ == "__main__":
    unittest.main()
