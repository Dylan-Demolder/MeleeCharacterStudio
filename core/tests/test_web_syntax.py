import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).parents[1] / "src" / "melee_character_studio" / "web"


@unittest.skipUnless(shutil.which("node"), "needs Node.js to parse the scripts")
class WebSyntaxTest(unittest.TestCase):
    """A script that does not parse leaves its page on "Loading…" with no error shown."""

    def test_the_studio_scripts_parse(self):
        for script in sorted(WEB.glob("*.js")):
            with self.subTest(script.name):
                r = subprocess.run(["node", "--input-type=module", "--check"], input=script.read_bytes(), capture_output=True)
                self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
