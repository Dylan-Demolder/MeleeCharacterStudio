import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from melee_character_studio.fighter_import import BASE_SEGMENTS, Mesh, _inverse_transpose_apply, encode_cmpr, retarget_mesh
from melee_character_studio.fighter_moves import FighterData, FighterMoveError, apply_moveset, command_words, move_frame_data, shield_stun
from melee_character_studio.hsd_archive import build_hsd_archive
from melee_character_studio.hsd_texture import _cmpr


class CmprTests(unittest.TestCase):
    def test_solid_blocks_round_trip(self):
        pixels = [(255, 0, 0, 255)] * 64
        block = encode_cmpr(pixels, 8, 8)
        self.assertEqual(len(block), 32)
        for sub in range(4):
            self.assertTrue(all(c[:3] == (255, 0, 0) for c in _cmpr(block[sub * 8:sub * 8 + 8])))

    def test_two_colour_block_keeps_both_colours(self):
        pixels = [((0, 0, 0, 255) if x < 4 else (255, 255, 255, 255)) for y in range(8) for x in range(8)]
        decoded = _cmpr(encode_cmpr(pixels, 8, 8)[0:8]) + _cmpr(encode_cmpr(pixels, 8, 8)[8:16])
        self.assertIn((0, 0, 0, 255), decoded)
        self.assertIn((255, 255, 255, 255), decoded)

    def test_rejects_unaligned_sizes(self):
        with self.assertRaises(ValueError):
            encode_cmpr([(0, 0, 0, 255)] * 36, 6, 6)


class RetargetTests(unittest.TestCase):
    def _base(self):
        # A stick skeleton whose joints are exactly the source landmarks (scaled 1:1).
        lm = {"pelvis": (0, 10, 0), "chest": (0, 13, 0), "neck": (0, 16, 0), "head": (0, 17, 0),
              "r_shoulder": (-2, 15.5, 0), "r_elbow": (-4, 15.5, 0), "r_wrist": (-6, 15.5, 0), "r_hand": (-7, 15.5, 0),
              "l_shoulder": (2, 15.5, 0), "l_elbow": (4, 15.5, 0), "l_wrist": (6, 15.5, 0), "l_hand": (7, 15.5, 0),
              "r_hip": (-1, 9.5, 0), "r_knee": (-1, 5, 0), "r_ankle": (-1, 1, 0), "r_toe": (-1, 0.2, 1),
              "l_hip": (1, 9.5, 0), "l_knee": (1, 5, 0), "l_ankle": (1, 1, 0), "l_toe": (1, 0.2, 1)}
        lm["upper_chest"] = (0, 15.1, 0)
        joints = [(0.0, 0.0, 0.0)] * 64
        joints = list(joints)
        for name, a, b, j0, j1, *_ in BASE_SEGMENTS["captain-falcon"]:
            joints[j0] = lm[a]; joints[j1] = lm[b]
        return lm, joints

    def test_identity_pose_maps_points_onto_themselves(self):
        lm, joints = self._base()
        points = [lm["r_elbow"], (-5, 15.5, 0.2), (0, 12, 0.3), (1, 3, 0.2)]
        tris = [(0, 1, 2), (1, 2, 3)]
        mesh = Mesh(points, [(0, 0, 1)] * 4, [(0, 0)] * 4, tris, None)
        rigged, info = retarget_mesh(mesh, lm, joints, "captain-falcon", smoothing=0)
        self.assertAlmostEqual(info["scale"], 1.0, places=6)
        for before, after in zip(points, rigged.positions):
            self.assertTrue(all(abs(before[k] - after[k]) < 1e-6 for k in range(3)), (before, after))
        for weights in rigged.weights:
            self.assertAlmostEqual(sum(w for _, w in weights), 1.0, places=6)

    def test_joint_rotation_turns_only_that_part(self):
        lm, joints = self._base()
        hand = (-6.5, 15.5, 0.5)   # just past the right wrist, 0.5 in front
        mesh = Mesh([hand, lm["r_elbow"], (-5, 15.5, 0)], [(0, 0, 1)] * 3, [(0, 0)] * 3, [(0, 1, 2)], None)
        half_turn_x = (1.0, 0.0, 0.0, 0.0)  # 180 degrees about X
        plain, _ = retarget_mesh(mesh, lm, joints, "captain-falcon", smoothing=0)
        turned, _ = retarget_mesh(mesh, lm, joints, "captain-falcon", smoothing=0, joint_rotations={"r_wrist": half_turn_x})
        self.assertAlmostEqual(plain.positions[0][2], 0.5, places=6)
        self.assertAlmostEqual(turned.positions[0][2], -0.5, places=6)   # hand flipped around the wrist
        self.assertEqual(plain.positions[1], turned.positions[1])        # elbow untouched

    def test_inverse_transpose_of_rotation_is_rotation(self):
        rot = (0, -1, 0, 5, 1, 0, 0, 2, 0, 0, 1, 3)  # 90 degrees about Z plus translation
        n = _inverse_transpose_apply(rot, (1, 0, 0))
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(n, (0, 1, 0))))


def _fighter_archive():
    """ftData root -> action table with one named action whose script has one hitbox."""
    body = bytearray(0x200)
    struct.pack_into(">I", body, 0x0C, 0x80)              # ftData.xC -> action table
    struct.pack_into(">I", body, 0x80, 0x100)             # entry.name
    struct.pack_into(">I", body, 0x8C, 0x140)             # entry.script
    body[0x100:0x100 + 40] = b"PlyTest_Share_ACTION_SpecialLw_figatree\0"
    words = [(11 << 26) | (1 << 23) | (5 << 11) | 15,     # hitbox id 1, bone 5, 15 damage
             (0x0400 << 16), 0,
             (361 << 23) | (70 << 14),                     # angle 361, growth 70
             (50 << 23)]                                   # base knockback 50
    for i, w in enumerate(words):
        struct.pack_into(">I", body, 0x140 + 4 * i, w)
    struct.pack_into(">I", body, 0x154, (1 << 26) | 4)     # synchronous timer
    struct.pack_into(">I", body, 0x158, 0)                 # End
    return build_hsd_archive(body, relocations=(0x0C, 0x80, 0x8C), publics=((0, "ftDataTest"),))


def _script_archive(words, extra_relocs=()):
    """One action whose script (at 0x140) is ``words``; relocations for pointer words are added."""
    body = bytearray(0x400)
    struct.pack_into(">I", body, 0x0C, 0x80)
    struct.pack_into(">I", body, 0x80, 0x100)
    struct.pack_into(">I", body, 0x8C, 0x140)
    body[0x100:0x100 + 40] = b"PlyTest_Share_ACTION_AttackAirN_figatree"
    for i, w in enumerate(words):
        struct.pack_into(">I", body, 0x140 + 4 * i, w)
    return build_hsd_archive(bytes(body), relocations=(0x0C, 0x80, 0x8C, *extra_relocs), publics=((0, "ftDataTest"),))


def _hitbox(hid, damage):
    return [(11 << 26) | (hid << 23) | damage, 0x0400 << 16, 0, 361 << 23, 0]


SYNC, ASYNC = (lambda n: (1 << 26) | n), (lambda n: (2 << 26) | n)


class FrameDataTests(unittest.TestCase):
    def test_timers_hitboxes_and_iasa_follow_the_engine(self):
        # Fox's jab: async 2, two hitboxes, async 3, clear, async 16, allow interrupt (known: 2-3, IASA 16)
        words = [ASYNC(2), *_hitbox(0, 4), *_hitbox(1, 4), ASYNC(4), 16 << 26, ASYNC(16), 23 << 26, 0]
        data = FighterData(_script_archive(words))
        tl = data.timeline(0x140, 18)
        self.assertEqual((tl["startup"], tl["active"], tl["iasa"], tl["total"]), (2, [[2, 3]], 16, 18))
        fd = move_frame_data(data, 0x140, 18)
        self.assertEqual((fd["ready"], fd["lag_after_hit"]), (16, 13))
        self.assertEqual(shield_stun(4) - fd["lag_after_hit"], -10)   # Dr. Mario's jab measured -10 in game

    def test_loops_subroutines_and_landing_lag_windows(self):
        # set cmd var 0 (landing lag on) at frame 1; loop 3x: hitbox, sync 1, remove it, sync 1; call a
        # subroutine that turns landing lag off; end
        loop = [(3 << 26) | 3, *_hitbox(0, 5), SYNC(1), 15 << 26, SYNC(1), 4 << 26]
        words = [(19 << 26) | 1, *loop, 5 << 26, 0x140 + 4 * (1 + len(loop) + 3), 0, (19 << 26) | 0, 6 << 26]
        ptr_word = 0x140 + 4 * (1 + len(loop) + 1)
        data = FighterData(_script_archive(words, extra_relocs=(ptr_word,)))
        tl = data.timeline(0x140, 20)
        # The script first runs with the timer at -1, so frame 1's "sync 1" does not wait: the first
        # hitbox is removed on the frame it appears and never hits.
        self.assertEqual(tl["active"], [[2, 2], [4, 4]])
        self.assertEqual(tl["lag_windows"], [[1, 5]])
        fd = move_frame_data(data, 0x140, 20, "AttackAirN", {"landing_lag_nair": 18})
        self.assertEqual(fd["landing"]["lag"], 18); self.assertEqual(fd["landing"]["l_cancel"], 9)

    def test_runaway_scripts_are_refused(self):
        data = FighterData(_script_archive([7 << 26, 0x140], extra_relocs=(0x144,)))   # goto itself
        with self.assertRaises(FighterMoveError):
            data.timeline(0x140, 10)


class FighterMoveTests(unittest.TestCase):
    def test_command_lengths_follow_decomp_tables(self):
        self.assertEqual(command_words(11), 5)   # hitbox
        self.assertEqual(command_words(7), 2)    # goto
        self.assertEqual(command_words(38), 7)   # 10 + index 28 in ftAction_803C0870

    def test_hitbox_decode_and_tuning(self):
        data = FighterData(_fighter_archive())
        _, _, name, script = next(data.action_tables())
        self.assertTrue(name.endswith("SpecialLw_figatree"))
        box = data.hitboxes(script)[0]
        self.assertEqual((box["id"], box["bone"], box["damage"], box["angle"], box["knockback_growth"], box["base_knockback"]), (1, 5, 15, 361, 70, 50))
        changes = apply_moveset(data, {"moves": [{"name": "Dragon's Rage", "slot": "special_down", "actions": [
            {"action": "SpecialLw", "tuning": {"damage": 18, "angle": 30, "knockback_growth": 95, "base_knockback": 70}}]}]})
        self.assertEqual(len(changes), 1)
        after = FighterData(bytes(data.raw)).hitboxes(script)[0]
        self.assertEqual((after["id"], after["bone"], after["damage"], after["angle"], after["knockback_growth"], after["base_knockback"]), (1, 5, 18, 30, 95, 70))

    def test_out_of_range_tuning_is_rejected(self):
        data = FighterData(_fighter_archive())
        with self.assertRaises(FighterMoveError):
            apply_moveset(data, {"moves": [{"name": "x", "slot": "s", "action": "SpecialLw", "tuning": {"angle": 600}}]})


if __name__ == "__main__":
    unittest.main()
