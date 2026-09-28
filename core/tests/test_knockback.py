import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from melee_character_studio.knockback import apply_di, asdi, flight, hitstun, knockback, ko_percent, launch_angle, tumble_percent

LUIGI = {"weight": 100.0, "gravity": 0.069, "fall_speed": 1.6, "ground_friction": 0.025}
FOX = {"weight": 75.0, "gravity": 0.23, "fall_speed": 2.8, "ground_friction": 0.08}
DOC_FSMASH = {"damage": 19, "angle": 361, "knockback_growth": 97, "base_knockback": 30}
YOSHIS_STORY = {"left": -175.7, "right": 173.6, "top": 168.0, "bottom": -91.0, "edge": 56.0}


class KnockbackTests(unittest.TestCase):
    def test_recorded_launch(self):
        # Dr. Mario's forward smash on Luigi at 80% on Yoshi's Story, logged frame by frame in game:
        # launch velocity (4.0705, 3.9309), then these positions after hitlag; KO'd right on frame 56.
        kb = knockback(19, 80, 100.0, 97, 30)
        self.assertAlmostEqual(kb * 0.03, 5.6587, places=3)
        r = flight(kb, 361, LUIGI, YOSHIS_STORY, x=5.665, y=0.0001)
        for f, (x, y) in {1: (9.6988, 3.8266), 24: (92.3521, 63.0689), 55: (173.0479, 91.3960)}.items():
            self.assertAlmostEqual(r["path"][f][0], x, places=2)
            self.assertAlmostEqual(r["path"][f][1], y, places=2)
        self.assertEqual((r["ko"], r["frame"]), ("right", 56))

    def test_recorded_di(self):
        # Same hit, logged in game with the stick held as hitlag ends. Left: the launch turns from
        # 44 to about 53 degrees; up-left (-0.7, 0.7) also moves Luigi 3 units (ASDI) and saves him.
        kx, ky = apply_di(4.0705, 3.9309, -1, 0)
        m = (kx * kx + ky * ky) ** 0.5
        self.assertAlmostEqual(kx - 0.051 * kx / m, 3.3993, places=3)
        self.assertAlmostEqual(ky - 0.051 * ky / m, 4.4600, places=3)
        for got, want in zip(asdi(1.465, 4.2001, -0.7, 0.7), (-0.635, 6.3001)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertEqual(asdi(1.0, 1.0, 0.5, 0.0), (1.0, 1.0))   # under 0.7: no ASDI
        kb = knockback(19, 80, 100.0, 97, 30)
        self.assertEqual(flight(kb, 361, LUIGI, YOSHIS_STORY, x=2.985, y=0.0001, stick=(-1, 0))["ko"], "right")
        self.assertIsNone(flight(kb, 361, LUIGI, YOSHIS_STORY, x=1.465, y=4.2001, stick=(-0.7, 0.7))["ko"])

    def test_sakurai_angle(self):
        self.assertEqual(launch_angle(361, 20), 0.0)
        self.assertEqual(launch_angle(361, 188), 44.0)
        self.assertEqual(launch_angle(361, 188, grounded=False), 45.0)
        self.assertEqual(launch_angle(80, 5), 80.0)

    def test_percents(self):
        self.assertEqual(hitstun(100), 40)
        tipper = {"damage": 20, "angle": 361, "knockback_growth": 70, "base_knockback": 80}
        self.assertEqual(tumble_percent(tipper, FOX), 0)   # base knockback alone tumbles
        ko = ko_percent(tipper, FOX)
        self.assertTrue(55 <= ko <= 75, ko)
        self.assertLess(ko, ko_percent(tipper, dict(FOX, weight=120.0)))   # heavier lives longer
        weak = {"damage": 1, "angle": 361, "knockback_growth": 10, "base_knockback": 0}
        self.assertIsNone(ko_percent(weak, FOX))
        self.assertIsNone(ko_percent({"damage": 5, "angle": 90, "knockback_growth": 100, "base_knockback": 0,
                                      "weight_set_knockback": 20}, FOX))

    def test_ground_launch_slides(self):
        # A flat launch keeps a grounded target on the stage, sliding with its traction.
        r = flight(50, 0, FOX)
        self.assertIsNone(r["ko"])
        self.assertEqual(r["path"][5][1], 0.0)


class JavaScriptMirrorTests(unittest.TestCase):
    """web/knockback.js must give the studio's live numbers the same answers as this module."""

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_same_answers(self):
        js = (Path(__file__).parents[1] / "src/melee_character_studio/web/knockback.js").resolve().as_uri()
        hits = [DOC_FSMASH, {"damage": 20, "angle": 361, "knockback_growth": 70, "base_knockback": 80},
                {"damage": 13, "angle": 25, "knockback_growth": 100, "base_knockback": 0},
                {"damage": 14, "angle": 140, "knockback_growth": 80, "base_knockback": 40},
                {"damage": 18, "angle": 80, "knockback_growth": 112, "base_knockback": 30}]
        targets = [LUIGI, FOX, {"weight": 60.0, "gravity": 0.064, "fall_speed": 1.3, "ground_friction": 0.09}]
        script = (f"import * as k from {json.dumps(js)};"
                  f"const hits = {json.dumps(hits)}, targets = {json.dumps(targets)};"
                  "console.log(JSON.stringify(hits.map(h => targets.map(t => [k.koPercent(h, t), k.tumblePercent(h, t)]))));")
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [[[ko_percent(h, t), tumble_percent(h, t)] for t in targets] for h in hits])


if __name__ == "__main__":
    unittest.main()
