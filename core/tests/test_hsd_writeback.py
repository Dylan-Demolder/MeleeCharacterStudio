import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from melee_character_studio.hsd_archive import build_hsd_archive, extract_hsd_publics, validate_hsd_relocations
from melee_character_studio.hsd_codegen import GX_DIRECT, GX_TRIANGLES, GxCommand, GxDescriptor, decode_gx_display_list, encode_gx_display_list, serialize_hsd_graph
from melee_character_studio.package_runtime import PackageRuntimeError, compose_fighter_hsd, compose_fighter_slot
from melee_character_studio.hsd_writeback import HsdWritebackError, compose_tev, generate_uv, write_hsd_scene


class HsdWritebackTests(unittest.TestCase):
    def test_uv_projections_are_bounded_and_deterministic(self):
        vertices = [(-1.0, -1.0, 0.0), (1.0, 1.0, 2.0)]
        self.assertEqual(generate_uv(vertices, "planar"), [(0.0, 0.0), (1.0, 1.0)])
        for projection in ("cylindrical", "spherical"):
            for uv in generate_uv(vertices, projection):
                self.assertTrue(all(0.0 <= component <= 1.0 for component in uv))
        self.assertEqual(generate_uv(vertices, "spherical"), generate_uv(vertices, "spherical"))

    def test_tev_composes_ordered_clamped_stages(self):
        result = compose_tev(
            [{"operation": "multiply", "a": "texture", "b": "vertex"},
             {"operation": "add", "a": "previous", "b": "constant"}],
            {"texture": (0.8, 0.5, 0.2, 1.0), "vertex": (0.5, 0.5, 0.5, 1.0),
             "constant": (0.8, 0.8, 0.8, 1.0)},
        )
        self.assertEqual(result, (1.0, 1.0, 0.9, 1.0))

    def test_scene_writeback_preserves_archive_layout(self):
        body = bytearray(32)
        struct.pack_into(">I", body, 0, 4)
        struct.pack_into(">fff", body, 8, 1.0, 2.0, 3.0)
        source_data = build_hsd_archive(body, relocations=(0,))
        scene = {"hsd": True, "geometry": [{
            "vertices": [(1.0, 2.0, 3.0)], "base_vertices": [(1.0, 2.0, 3.0)],
            "source_position_indices": [0], "source_position_values": [(1.0, 2.0, 3.0)],
            "source_position_stream": 8, "source_position_stride": 12,
            "source_position_type": "f32", "source_position_frac": 0,
        }]}
        with tempfile.TemporaryDirectory() as td:
            source, output = Path(td) / "source.dat", Path(td) / "edited.dat"
            source.write_bytes(source_data)
            write_hsd_scene(source, output, scene, vertex_edits={(0, 0): (2.0, 2.0, 3.0)})
            edited = output.read_bytes()
            self.assertEqual(len(edited), len(source_data))
            self.assertEqual(struct.unpack_from(">fff", edited, 0x20 + 8), (2.0, 2.0, 3.0))

    def test_scene_rejects_topology_changes(self):
        with self.assertRaises(HsdWritebackError):
            write_hsd_scene("source.dat", "output.dat", {"hsd": True, "topology_ops": ["delete"]})

    def test_gx_display_list_round_trip(self):
        descriptors = [
            GxDescriptor(9, GX_DIRECT, 0, 4),
            GxDescriptor(13, GX_DIRECT, 0, 4),
        ]
        commands = [GxCommand(GX_TRIANGLES, (
            {9: (0.0, 1.0, 2.0), 13: (0.0, 0.5)},
            {9: (1.0, 1.0, 2.0), 13: (1.0, 0.5)},
            {9: (0.0, 2.0, 2.0), 13: (0.0, 1.0)},
        ))]
        encoded = encode_gx_display_list(commands, descriptors)
        self.assertEqual(decode_gx_display_list(encoded, descriptors), tuple(commands))

    def test_graph_serializer_rewrites_relocations_and_symbols(self):
        body = bytearray(24)
        struct.pack_into(">I", body, 0, 12)
        struct.pack_into(">I", body, 4, 0)
        source = build_hsd_archive(body, relocations=(0,), publics=((0, "ftDataTest"),))
        result = serialize_hsd_graph(source, public_symbols={"ftTestNormalAJ": 12})
        self.assertEqual(extract_hsd_publics(result), {"ftDataTest": 0, "ftTestNormalAJ": 12})
        self.assertEqual(validate_hsd_relocations(result), (12,))

    def test_package_composition_adds_loadable_fighter_root(self):
        body = bytearray(0x80)
        struct.pack_into(">I", body, 0x5C, 0x64)
        struct.pack_into(">I", body, 0x64, 0)
        source = build_hsd_archive(body, publics=((0, "ftDataBase"),))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "nova-hsd-2e.melee-character"
            package.mkdir()
            character = {"id": "nova-hsd-2e", "version": "1", "target_game_version": "GALE01-1.02", "compatibility": "offline-gameplay"}
            (package / "character.json").write_text(json.dumps(character), encoding="utf-8")
            (package / "moveset.json").write_text("{}", encoding="utf-8")
            checksums = {"character.json": __import__("hashlib").sha256((package / "character.json").read_bytes()).hexdigest(), "moveset.json": __import__("hashlib").sha256((package / "moveset.json").read_bytes()).hexdigest()}
            (package / "checksums.json").write_text(json.dumps(checksums), encoding="utf-8")
            source_path, output = root / "base.dat", root / "patched.dat"
            source_path.write_bytes(source)
            compose_fighter_hsd(package, source_path, output)
            publics = extract_hsd_publics(output.read_bytes())
            self.assertIn("ftDataNovahsd2e", publics)
            self.assertEqual(validate_hsd_relocations(output.read_bytes())[-1], 0x64)


    def _slot_package(self, root, attributes):
        import hashlib
        package = root / "slot.melee-character"
        package.mkdir()
        character = {"id": "ember", "version": "1", "target_game_version": "GALE01-1.02", "compatibility": "offline-gameplay", "attributes": attributes}
        (package / "character.json").write_text(json.dumps(character), encoding="utf-8")
        (package / "moveset.json").write_text(json.dumps({"base_fighter": "falco", "moves": []}), encoding="utf-8")
        checksums = {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in ("character.json", "moveset.json")}
        (package / "checksums.json").write_text(json.dumps(checksums), encoding="utf-8")
        return package

    def _slot_base(self):
        body = bytearray(0x60 + 0x184)
        struct.pack_into(">I", body, 0, 0x60)          # ftData.x0 -> ftCo_DatAttrs
        struct.pack_into(">f", body, 0x60 + 0x5C, 0.17)
        struct.pack_into(">f", body, 0x60 + 0x8C, 1.1)
        return build_hsd_archive(body, relocations=(0,), publics=((0, "ftDataFalco"),))

    def test_slot_composition_keeps_root_and_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = self._slot_package(root, {"gravity": 0.09, "size": 1.4})
            base, output = root / "PlFc.dat", root / "out" / "PlFc.dat"
            base.write_bytes(self._slot_base())
            compose_fighter_slot(package, base, output)
            before, after = base.read_bytes(), output.read_bytes()
            self.assertEqual(len(before), len(after))
            self.assertEqual(extract_hsd_publics(after), {"ftDataFalco": 0})
            self.assertEqual(validate_hsd_relocations(after), validate_hsd_relocations(before))
            self.assertAlmostEqual(struct.unpack_from(">f", after, 0x20 + 0x60 + 0x5C)[0], 0.09, places=6)
            self.assertAlmostEqual(struct.unpack_from(">f", after, 0x20 + 0x60 + 0x8C)[0], 1.4, places=6)
            diff = [i for i in range(len(before)) if before[i] != after[i]]
            self.assertTrue(all(0x20 + 0x60 + 0x5C <= i < 0x20 + 0x60 + 0x60 or 0x20 + 0x60 + 0x8C <= i < 0x20 + 0x60 + 0x90 for i in diff))
            report = json.loads((root / "out" / "PlFc.dat.slot.json").read_text(encoding="utf-8"))
            self.assertEqual(report["slot_root"], "ftDataFalco")
            self.assertFalse(report["game_integration"])

    def test_slot_composition_writes_integer_and_extended_attributes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "PlFc.dat"
            base.write_bytes(self._slot_base())
            output = root / "out" / "PlFc.dat"
            compose_fighter_slot(self._slot_package(root, {"max_jumps": 6, "shield_size": 14.5, "fast_fall_speed": 3.1}), base, output)
            after = output.read_bytes()
            self.assertEqual(struct.unpack_from(">i", after, 0x20 + 0x60 + 0x58)[0], 6)
            self.assertAlmostEqual(struct.unpack_from(">f", after, 0x20 + 0x60 + 0x90)[0], 14.5, places=5)
            self.assertAlmostEqual(struct.unpack_from(">f", after, 0x20 + 0x60 + 0x74)[0], 3.1, places=5)
            report = json.loads((root / "out" / "PlFc.dat.slot.json").read_text(encoding="utf-8"))
            self.assertEqual(report["attributes"]["max_jumps"]["value"], 6)

    def test_slot_composition_rejects_unmapped_and_out_of_range(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "PlFc.dat"
            base.write_bytes(self._slot_base())
            with self.assertRaisesRegex(PackageRuntimeError, "no verified"):
                compose_fighter_slot(self._slot_package(root, {"kirby_star_damage": 1.0}), base, root / "a.dat")
            (root / "slot.melee-character").rename(root / "older")
            with self.assertRaisesRegex(PackageRuntimeError, "whole number"):
                compose_fighter_slot(self._slot_package(root, {"max_jumps": 2.5}), base, root / "c.dat")
            (root / "slot.melee-character").rename(root / "old")
            with self.assertRaisesRegex(PackageRuntimeError, "between"):
                compose_fighter_slot(self._slot_package(root, {"gravity": 99}), base, root / "b.dat")


if __name__ == "__main__":
    unittest.main()
