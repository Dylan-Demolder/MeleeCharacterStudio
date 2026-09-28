import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from melee_character_studio import projects, studio_server as ss


class StudioAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.home = Path(self.tmp.name)
        self._saved = (projects.CONFIG, projects.HOME)
        projects.CONFIG = self.home / "config.json"; projects.HOME = self.home
        self.studio = ss.Studio({"projects_dir": str(self.home / "projects")})
        self.studio.uploads = self.home / "uploads"

    def tearDown(self):
        projects.CONFIG, projects.HOME = self._saved
        self.tmp.cleanup()

    def test_test_profile_boots_into_a_match_in_place_of_the_base(self):
        root = self.home / "pp"; out = self.home / "out"; out.mkdir()
        (out / "ember.portrait").write_bytes(b"p")
        fake = SimpleNamespace(config={"pascalpatch": {"root": str(root), "repo": "r", "port": "p"}}, iso=self.home / "game.iso",
                               character={"id": "ember", "display_name": "Ember", "install": "new"}, base_fighter="falco")
        match = {"p1": "falco", "p2": "marth", "p2_player": "human", "stage": "bf", "rules": "endless"}
        pid = ss.Session._write_profile(fake, out, "PlFc", test=match)
        doc = json.loads((root / "profiles" / f"{pid}.json").read_text())
        self.assertEqual(pid, "ember-studio-test")
        self.assertEqual(doc["quick_match"], match)
        self.assertEqual(doc["characters"][0]["install"], "replace")   # quick-match picks it by its base's slot
        self.assertEqual(doc["characters"][0]["portrait"], str(out / "ember.portrait").replace("\\", "/"))
        # the ordinary profile keeps the project's choice and boots to the menus
        normal = json.loads((root / "profiles" / f"{ss.Session._write_profile(fake, out, 'PlFc')}.json").read_text())
        self.assertEqual((normal["characters"][0]["install"], "quick_match" in normal), ("new", False))

    def test_test_in_game_checks_its_choices_before_building(self):
        fake = SimpleNamespace(config={"pascalpatch": {"root": "x", "repo": "r", "port": "p"}}, base_fighter="falco",
                               TEST_STAGES=ss.Session.TEST_STAGES, TEST_PLAYERS=ss.Session.TEST_PLAYERS)
        for payload in ({"opponent": "not-a-fighter"}, {"stage": "hyrule"}, {"p2_player": "cpu4"}):
            with self.assertRaises(ValueError):
                ss.Session.test_in_game(fake, payload)
        with self.assertRaisesRegex(ValueError, "configure"):
            ss.Session.test_in_game(SimpleNamespace(config={}), {})

    def test_examples_open_and_roster_as_your_own_copies(self):
        listed = self.studio.info()["examples"]
        self.assertEqual([e["id"] for e in listed], ["glacier", "nova", "bolt9", "umbra", "cinder", "chungus"])
        self.assertTrue(all(e["description"] for e in listed))
        self.assertTrue(self.studio.picture(listed[0]["path"], "icon").is_file())   # the cards can show their icons
        copy = projects.example_copy("glacier", self.studio.cfg)
        self.assertEqual(copy, self.home / "projects" / "glacier")
        self.assertEqual(projects.example_copy("glacier", self.studio.cfg), copy)   # asked again: the same copy
        # only a name is taken from the id, so it cannot reach outside the examples
        self.assertEqual(projects.example_copy("../../examples/nova", self.studio.cfg), self.home / "projects" / "nova")
        with self.assertRaises(projects.ProjectError):
            projects.example_copy("not-an-example", self.studio.cfg)
        # a roster that already uses Bowser's slot keeps its own character there
        mine = projects.duplicate(copy, self.home / "projects", "My Golem")
        projects.save_user_roster({"name": "Mine", "characters": [str(mine)]}, self.studio.cfg)
        r = self.studio.add_examples()
        self.assertEqual([e["id"] for e in r["entries"]], ["my-golem", "nova", "bolt9", "umbra", "cinder", "chungus"])
        self.assertEqual(len({e["base_fighter"] for e in r["entries"]}), 6)
        self.assertEqual(self.studio.add_examples()["characters"], r["characters"])   # twice changes nothing

    def test_upload_checks_type_and_keeps_the_file(self):
        with self.assertRaises(projects.ProjectError):
            self.studio.upload("hero.fbx", io.BytesIO(b"x"), 1)
        r = self.studio.upload("../My Hero!.obj", io.BytesIO(b"v 0 0 0\n"), 8)
        files = list((self.studio.uploads / r["upload"]).iterdir())
        self.assertEqual([f.name for f in files], ["My Hero_.obj"]); self.assertEqual(files[0].read_bytes(), b"v 0 0 0\n")

    def test_settings_want_a_melee_disc(self):
        bad = self.home / "other.iso"; bad.write_bytes(b"GALP01" + b"\0" * 64)
        with self.assertRaises(projects.ProjectError):
            self.studio.settings({"iso": str(bad)})
        good = self.home / "melee.iso"; good.write_bytes(b"GALE01" + b"\0" * 64)
        info = self.studio.settings({"iso": str(good), "pascalpatch_root": "C:/pp"})
        self.assertTrue(info["ready"]); self.assertEqual(json.loads(projects.CONFIG.read_text())["pascalpatch"]["root"], "C:/pp")

    def test_pictures_only_for_known_projects(self):
        with self.assertRaises(projects.ProjectError):
            self.studio.picture(self.home, "icon")

    def test_only_its_own_page_may_call(self):
        ss.Handler.studio = self.studio
        server = ss.StudioServer(("127.0.0.1", 0), ss.Handler)
        ss.Handler.origin = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def call(path, headers=None, data=None):
                req = urllib.request.Request(ss.Handler.origin + path, data=data, headers=headers or {})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r: return r.status, r.read()
                except urllib.error.HTTPError as e: return e.code, e.read()
            self.assertEqual(call("/api/studio")[0], 200)
            self.assertEqual(call("/api/project/close", {"Origin": "http://evil.example"}, b"{}")[0], 403)
            code, body = call("/api/state")
            self.assertEqual(code, 400); self.assertIn("no project is open", body.decode())
            self.assertIn(b"start.js", call("/")[1])   # no project open: the start screen
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
