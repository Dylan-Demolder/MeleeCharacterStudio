import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from melee_character_studio.cast import project_moves, review, shield_of


def _move(action, startup, damage, lag, first=None):
    return {"action": action, "startup": startup, "active": [[startup, startup + 2]], "ready": startup + lag + 1,
            "lag_after_hit": lag, "total": startup + lag + 1, "damage": damage, "first_damage": first or damage}


CAST = {"fighters": {
    "fox": {"attributes": {"weight": 75.0, "landing_lag_nair": 15.0},
            "moves": {"jab": _move("Attack11", 2, 4, 13), "attack_air_neutral": _move("AttackAirN", 4, 12, 37)}},
    "bowser": {"attributes": {"weight": 117.0, "landing_lag_nair": 30.0},
               "moves": {"jab": _move("Attack11", 7, 5, 13), "attack_air_neutral": _move("AttackAirN", 8, 13, 39)}},
    "marth": {"attributes": {"weight": 87.0, "landing_lag_nair": 15.0},
              "moves": {"jab": _move("Attack11", 4, 6, 21), "smash_down": _move("AttackLw4", 5, 16, 56)}},
}}


class CastReviewTests(unittest.TestCase):
    def test_untouched_base_fighter_raises_nothing(self):
        fox = CAST["fighters"]["fox"]["attributes"]
        r = review(CAST, "fox", dict(fox), {"moves": []})
        self.assertEqual(r["findings"], [])   # Fox's own fastest jab is not the project's doing
        jab = next(m for m in r["moves"] if m["slot"] == "jab")
        self.assertEqual((jab["startup_rank"], jab["of"]), (1, 4))

    def test_stats_beyond_the_cast_are_named(self):
        attrs = dict(CAST["fighters"]["fox"]["attributes"], weight=150.0)
        r = review(CAST, "fox", attrs, {"moves": []})
        [f] = r["findings"]
        self.assertIn("heavier than any fighter", f["message"]); self.assertIn("Bowser has 117", f["message"])
        self.assertEqual(r["stats"]["weight"]["rank"], 1)

    def test_tuned_and_borrowed_moves(self):
        attrs = dict(CAST["fighters"]["fox"]["attributes"])
        moveset = {"moves": [
            {"name": "Mega Jab", "slot": "jab", "actions": [{"action": "Attack11", "tuning": {"damage_scale": 3}}]},
            {"name": "Blade Sweep", "slot": "smash_down", "borrow": {"fighter": "marth", "action": "AttackLw4"}}]}
        mine = project_moves(CAST, "fox", moveset, attrs)
        self.assertEqual(mine["jab"]["damage"], 12)
        self.assertEqual(mine["smash_down"]["source"], "marth"); self.assertEqual(mine["smash_down"]["startup"], 5)
        msgs = [f["message"] for f in review(CAST, "fox", attrs, moveset)["findings"]]
        self.assertTrue(any("Mega Jab does 12%" in m and "Marth's does 6%" in m for m in msgs), msgs)

    def test_aerial_shield_uses_l_cancelled_landing_lag(self):
        nair = CAST["fighters"]["fox"]["moves"]["attack_air_neutral"]
        self.assertEqual(shield_of(nair, {"landing_lag_nair": 15.0}, "attack_air_neutral"), 7 - 7)
        self.assertEqual(shield_of(CAST["fighters"]["fox"]["moves"]["jab"], None, "jab"), 3 - 13)


    def test_kill_power_and_survival(self):
        cast = copy.deepcopy(CAST)
        for f, bkb in (("fox", 30), ("bowser", 40), ("marth", 80)):
            for m in cast["fighters"][f]["moves"].values():
                m["ko_hit"] = {"damage": 16, "angle": 361, "knockback_growth": 70, "base_knockback": bkb, "weight_set_knockback": 0}
        for f, d in cast["fighters"].items():
            d["attributes"].update(gravity=0.1, fall_speed=2.0, ground_friction=0.06)
        cast["fighters"]["marth"]["moves"]["smash_forward"] = dict(cast["fighters"]["marth"]["moves"]["jab"], action="AttackS4S")
        attrs = dict(cast["fighters"]["fox"]["attributes"])
        moveset = {"moves": [{"name": "Mega Jab", "slot": "jab", "actions": [{"action": "Attack11", "tuning": {"base_knockback": 120}}]}]}
        r = review(cast, "fox", attrs, moveset)
        jab = next(m for m in r["moves"] if m["slot"] == "jab")
        self.assertEqual(jab["ko_rank"], 1)
        self.assertTrue(any("Mega Jab KOs Fox from" in f["message"] and "Marth's" in f["message"] for f in r["findings"]), r["findings"])
        heavy = review(cast, "fox", dict(attrs, weight=200.0), {"moves": []})
        [s] = heavy["survival"]   # only Marth's forward smash is in this small cast
        self.assertGreater(s["yours"], s["base"])
        self.assertTrue(any(f["message"].startswith("Survives Marth's forward smash") for f in heavy["findings"]), heavy["findings"])


if __name__ == "__main__":
    unittest.main()
