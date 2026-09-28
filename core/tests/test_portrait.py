import base64, hashlib, json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from melee_character_studio.hsd_texture import rgba_png
from melee_character_studio.portrait import HEIGHT, ICON_HEIGHT, ICON_WIDTH, STOCK_HEIGHT, STOCK_WIDTH, WIDTH, build_icon, build_portrait, build_stock, encode_rgb5a3, portrait_image


def _png(w, h, color):
    return rgba_png({"width": w, "height": h, "rgba": bytes(color) * (w * h)})


class PortraitTests(unittest.TestCase):
    def test_rgb5a3_tiles_and_pixel_forms(self):
        rgba = bytearray(8 * 4 * 4)
        rgba[0:4] = bytes((255, 0, 0, 255))          # (0,0) opaque red
        rgba[4 * 4:4 * 4 + 4] = bytes((0, 0, 255, 0x80))  # (4,0): first pixel of the second tile, translucent blue
        data = encode_rgb5a3(bytes(rgba), 8, 4)
        self.assertEqual(len(data), 8 * 4 * 2)
        self.assertEqual(data[0:2], (0x8000 | 31 << 10).to_bytes(2, "big"))
        self.assertEqual(data[32:34], (4 << 12 | 15).to_bytes(2, "big"))   # tile 2 starts after 16 pixels

    def test_any_photo_becomes_a_door_image(self):
        image = portrait_image(_png(300, 200, (10, 200, 30, 255)))
        self.assertEqual((image["width"], image["height"]), (WIDTH, HEIGHT))
        self.assertEqual(image["rgba"][:4], bytes((10, 200, 30, 255)))

    def test_build_portrait_writes_file_and_report(self):
        with tempfile.TemporaryDirectory() as td:
            project, out = Path(td) / "p", Path(td) / "out"; project.mkdir(); out.mkdir()
            (project / "character.json").write_text(json.dumps({"id": "chungus", "display_name": "Chungus"}))
            self.assertIsNone(build_portrait(project, out, "chungus"))
            (project / "portrait.png").write_bytes(_png(WIDTH, HEIGHT, (255, 255, 255, 255)))
            (project / "character.json").write_text(json.dumps({"id": "chungus", "portrait": "portrait.png"}))
            path = build_portrait(project, out, "chungus")
            data = path.read_bytes()
            self.assertEqual((path.name, len(data), data[:2]), ("chungus.portrait", WIDTH * HEIGHT * 2, b"\xff\xff"))
            report = json.loads((out / "chungus.portrait.json").read_text())
            self.assertEqual((report["character"], report["output_sha256"]), ("chungus", hashlib.sha256(data).hexdigest()))
            self.assertIsNone(build_icon(project, out, "chungus"))
            (project / "icon.png").write_bytes(_png(ICON_WIDTH, ICON_HEIGHT, (0, 0, 0, 0)))
            (project / "character.json").write_text(json.dumps({"id": "chungus", "portrait": "portrait.png", "icon": "icon.png"}))
            icon = build_icon(project, out, "chungus")
            self.assertEqual((icon.name, len(icon.read_bytes())), ("chungus.icon", ICON_WIDTH * ICON_HEIGHT * 2))
            (project / "stock.png").write_bytes(_png(STOCK_WIDTH, STOCK_HEIGHT, (9, 9, 9, 255)))
            (project / "character.json").write_text(json.dumps({"id": "chungus", "stock": "stock.png"}))
            stock = build_stock(project, out, "chungus")
            self.assertEqual((stock.name, len(stock.read_bytes())), ("chungus.stock", STOCK_WIDTH * STOCK_HEIGHT * 2))
            (project / "character.json").write_text(json.dumps({"id": "chungus"}))   # dropped: stale files go too
            self.assertIsNone(build_icon(project, out, "chungus"))
            self.assertFalse(icon.exists())


class PortraitServerTests(unittest.TestCase):
    def test_upload_validates_and_remove_cleans_up(self):
        from melee_character_studio import studio_server as ss
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "character.json").write_text(json.dumps({"id": "chungus", "display_name": "Chungus"}))
            s = ss.Session.__new__(ss.Session); s.project = project; s.lock = __import__("threading").Lock()
            s.character = json.loads((project / "character.json").read_text())
            url = lambda raw: "data:image/png;base64," + base64.b64encode(raw).decode()
            doc = s.set_portrait({"image": url(_png(WIDTH, HEIGHT, (1, 2, 3, 255))), "source": url(_png(40, 50, (1, 2, 3, 255))), "crop": {"zoom": 1.5, "x": 0.2, "y": -1}})["character"]
            self.assertEqual((doc["portrait"], doc["portrait_source"], doc["portrait_crop"]["zoom"]), ("portrait.png", "portrait_source.png", 1.5))
            self.assertEqual(s.portrait_file().read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            doc = s.set_portrait({"image": url(_png(WIDTH, HEIGHT, (9, 9, 9, 255))), "crop": {"zoom": 2}})["character"]   # re-crop
            self.assertEqual((doc["portrait_source"], doc["portrait_crop"]), ("portrait_source.png", {"zoom": 2.0}))
            self.assertTrue(s.portrait_file(source=True).is_file())
            # rendered from the model: door and icon together, no photo behind them
            doc = s.set_portrait({"image": url(_png(WIDTH, HEIGHT, (5, 5, 5, 0))), "icon": url(_png(ICON_WIDTH, ICON_HEIGHT, (5, 5, 5, 255))), "generated": True})["character"]
            self.assertEqual((doc["portrait_mode"], doc["icon"], "portrait_source" in doc, "portrait_crop" in doc), ("model", "icon.png", False, False))
            self.assertEqual(s.portrait_file(icon=True).name, "icon.png")
            doc = s.set_portrait({"stock": url(_png(STOCK_WIDTH, STOCK_HEIGHT, (5, 5, 5, 255)))})["character"]
            self.assertEqual((doc["stock"], s.portrait_file(stock=True).name), ("stock.png", "stock.png"))
            doc = s.set_portrait({"icon": url(_png(128, 112, (5, 5, 5, 255)))})["character"]   # icon alone, any size
            self.assertEqual((doc["portrait_mode"], len(s.portrait_file(icon=True).read_bytes()) > 0), ("model", True))
            with self.assertRaises(ValueError):
                s.set_portrait({"image": "data:text/html;base64,PGI+"})
            s.set_portrait({"remove": True})
            self.assertNotIn("portrait", json.loads((project / "character.json").read_text()))
            self.assertFalse((project / "portrait.png").exists() or (project / "icon.png").exists() or (project / "stock.png").exists())


if __name__ == "__main__":
    unittest.main()
