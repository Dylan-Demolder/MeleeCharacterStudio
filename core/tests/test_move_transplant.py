import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from melee_character_studio.anim_bake import bake_clip
from melee_character_studio.hsd_animation import scan_figatree
from melee_character_studio.move_transplant import (ANIM_BUFFER, _euler_matrix, _euler_xyz, _reduce, encode_figatree,
                                                    is_normal_attack, transplant_script)


class EncodeTests(unittest.TestCase):
    def test_reduce_keeps_linear_runs_short(self):
        values = [i * 0.5 for i in range(20)]
        self.assertEqual(_reduce(values, 1e-6), [(0, 0.0), (19, 9.5)])

    def test_figatree_round_trips_through_the_game_interpreter(self):
        frames = 30
        channels = [{1: {1: math.sin(f / 5), 2: 0.25, 3: -f / 30, 7: f * 0.4}, 2: {1: 0.0, 2: 0.0, 3: 0.0}} for f in range(frames)]
        archive, tol = encode_figatree("PlyTest_Share_ACTION_AttackS4S_figatree", channels, 3, frames)
        self.assertLessEqual(len(archive), ANIM_BUFFER)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "aj.dat"; path.write_bytes(archive)
            clip = scan_figatree(path)[0]
        self.assertEqual(clip.name, "PlyTest_Share_ACTION_AttackS4S_figatree")
        self.assertEqual(clip.nodes, (0, 4, 3))
        baked = bake_clip(archive, clip)
        for f in (0, 7, 18, 29):
            self.assertAlmostEqual(baked[f][1][1], math.sin(f / 5), delta=0.01)
            self.assertAlmostEqual(baked[f][1][3], -f / 30, delta=0.01)
            self.assertAlmostEqual(baked[f][1][7], f * 0.4, delta=0.02)
            self.assertAlmostEqual(baked[f][1][2], 0.25, delta=0.001)   # constant (single key) track

    def test_euler_round_trip_matches_hsd_order(self):
        for angles in ((0.3, -0.7, 1.2), (-2.0, 0.4, -0.1)):
            back = _euler_xyz(_euler_matrix(*angles))
            self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(angles, back)))


class ScriptTests(unittest.TestCase):
    def _data(self, words):
        raw = bytearray(0x20 + 0x100)
        for i, w in enumerate(words):
            struct.pack_into(">I", raw, 0x20 + 0x40 + 4 * i, w)
        def walk(script):
            out, cur = [], script
            while True:
                op = raw[0x20 + cur] >> 2
                n = {11: 5, 17: 3}.get(op, 1)
                out.append((cur, op, n)); cur += 4 * n
                if op == 0: return out
        return SimpleNamespace(raw=raw, walk=walk)

    def _skeleton(self, hip):
        identity = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
        return SimpleNamespace(hip_height=hip, world=[identity] * 8)

    def test_hitbox_bone_and_size_are_remapped_and_sounds_dropped(self):
        hitbox = [(11 << 26) | (3 << 11) | 12, (100 << 16) | 0x0010, 0, 0, 0]
        sfx = [(17 << 26) | 5, 0, 0]
        data = self._data(hitbox + sfx + [0])
        out = transplant_script(data, 0x40, self._skeleton(1.0), self._skeleton(2.0), {3: 6})
        words = struct.unpack(f">{len(out) // 4}I", out)
        self.assertEqual((words[0] >> 11) & 0xFF, 6)            # bone remapped
        self.assertEqual(words[0] & 0x3FF, 12)                  # damage untouched
        self.assertEqual(words[1] >> 16, 200)                   # size scaled by hip ratio
        self.assertEqual([w >> 26 for w in words[5:8]], [1, 1, 1])  # sound -> no-op timers
        self.assertEqual(words[-1], 0)

    def test_specials_are_not_borrowable(self):
        self.assertTrue(is_normal_attack("AttackAirB"))
        self.assertFalse(is_normal_attack("SpecialNStart"))


if __name__ == "__main__":
    unittest.main()
