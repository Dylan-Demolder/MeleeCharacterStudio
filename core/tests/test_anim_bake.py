import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from melee_character_studio.anim_bake import OP_LIN, OP_SPL, _FObj, _hermite
from melee_character_studio.game_source import _fst


def track(*chunks):
    return b"".join(chunks)


def op(code, count):
    return bytes((code | ((count - 1) << 4),))


def f32(v):
    return struct.pack("<f", v)


def sample(data, frames, start=0):
    fobj = _FObj(data, start, 1, 0, 0)
    return [fobj.step(0.0 if f == 0 else 1.0) for f in range(frames)]


class FObjTests(unittest.TestCase):
    def test_linear_interpolates_between_keys(self):
        data = track(op(OP_LIN, 2), f32(0.0), bytes((10,)), f32(10.0), bytes((10,)))
        values = sample(data, 12)
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[5], 5.0)
        self.assertAlmostEqual(values[10], 10.0)
        # Past the final key the decomp's terminal state keeps evaluating the
        # last linear segment (fobj.c state 6); clips end their tracks on time.
        self.assertAlmostEqual(values[11], 11.0)

    def test_spline_matches_hermite_endpoints_and_slopes(self):
        data = track(op(OP_SPL, 2), f32(1.0), f32(0.0), bytes((8,)), f32(3.0), f32(0.0), bytes((8,)))
        values = sample(data, 9)
        self.assertAlmostEqual(values[0], 1.0, places=5)
        self.assertAlmostEqual(values[4], _hermite(1 / 8, 4.0, 1.0, 3.0, 0.0, 0.0), places=5)
        self.assertAlmostEqual(values[4], 2.0, places=5)  # symmetric ease with zero slopes
        self.assertAlmostEqual(values[8], 3.0, places=5)

    def test_negative_start_frame_delays_track(self):
        data = track(op(OP_LIN, 2), f32(0.0), bytes((4,)), f32(4.0), bytes((4,)))
        values = sample(data, 6, start=-2)
        self.assertIsNone(values[0])
        self.assertAlmostEqual(values[4], 2.0)


class DiscTests(unittest.TestCase):
    def test_fst_lists_files_and_rejects_other_discs(self):
        strings = b"\0PlCa.dat\0"
        fst = struct.pack(">III", 0x01000000, 0, 2) + struct.pack(">III", 1, 0x800, 16) + strings
        disc = bytearray(0x900)
        disc[0:6] = b"GALE01"
        struct.pack_into(">II", disc, 0x424, 0x440, len(fst))
        disc[0x440:0x440 + len(fst)] = fst
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "g.iso"; path.write_bytes(disc)
            self.assertEqual(_fst(path), {"PlCa.dat": (0x800, 16)})
            disc[0:6] = b"GALP01"; path.write_bytes(disc)
            with self.assertRaises(ValueError):
                _fst(path)


if __name__ == "__main__":
    unittest.main()
