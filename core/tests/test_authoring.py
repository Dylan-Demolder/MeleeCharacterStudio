import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from melee_character_studio.moveset import validate_moveset
from melee_character_studio.attributes import validate_attributes
from melee_character_studio.authoring import ProjectEditor, ProjectEditError
from melee_character_studio.gui import StudioController
from melee_character_studio.move_library import load_library, validate_library
from melee_character_studio.calibration import validate_calibration
import json, tempfile
class AuthoringTests(unittest.TestCase):
 def test_moveset_duplicate_and_missing_refs(self):
  d=validate_moveset({"moves":[{"name":"jab","slot":"a","reference":"known"},{"name":"kick","slot":"a","reference":"missing","transitions":["nope"]}]}, {"known"}); self.assertTrue(any(x.code=="duplicate_binding" for x in d)); self.assertTrue(any(x.code=="missing_reference" for x in d))
 def test_attributes_ranges_and_inheritance(self):
  d=validate_attributes({"dash_speed":9,"run_speed":4,"weight":0},{"walk_speed":1,"gravity":1,"fall_speed":1,"size":1,"air_speed":1,"jump_velocity":1}); self.assertTrue(any(x.code=="range" for x in d)); self.assertTrue(any(x.code=="unreachable" for x in d))
 def test_editor_saves_new_project_without_mutating_source(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td); (t/"source").mkdir(); (t/"source/character.json").write_text(json.dumps({"attributes":{}})); (t/"source/moveset.json").write_text(json.dumps({"moves":[]}));
   editor=ProjectEditor(t/"source"); editor.add_move("jab","a"); editor.set_attribute("weight",90); out=editor.save(t/"edited");
   self.assertEqual(json.loads((t/"source/moveset.json").read_text())["moves"],[])
   self.assertEqual(json.loads((out/"moveset.json").read_text())["moves"][0]["name"],"jab"); self.assertEqual(json.loads((out/"character.json").read_text())["attributes"]["weight"],90)
 def test_editor_updates_move_frame_data_and_hitboxes(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td); (t/"source").mkdir(); (t/"source/character.json").write_text(json.dumps({"attributes":{}})); (t/"source/moveset.json").write_text(json.dumps({"moves":[]})); editor=ProjectEditor(t/"source"); editor.add_move("jab","a"); updated=editor.update_move("jab",timing={"startup_frames":3,"active_frames":2,"recovery_frames":5},hitboxes=[{"id":"hb0","bone":"joint_0","start_frame":3,"end_frame":4,"radius":2.0}]); self.assertEqual(updated["startup_frames"],3); self.assertEqual(updated["hitboxes"][0]["bone"],"joint_0"); self.assertFalse(validate_moveset(editor.moveset))

 def test_editor_rejects_in_place_save(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td); (t/"character.json").write_text(json.dumps({"attributes":{}})); (t/"moveset.json").write_text(json.dumps({"moves":[]}));
   with self.assertRaises(ProjectEditError): ProjectEditor(t).save(t)

 def test_studio_controller_is_headless_and_validates_before_save(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td); (t/"source").mkdir(); (t/"source/character.json").write_text(json.dumps({"attributes":{"walk_speed":1,"dash_speed":2,"run_speed":3,"gravity":1,"fall_speed":1,"weight":90,"size":1,"air_speed":1,"jump_velocity":1}})); (t/"source/moveset.json").write_text(json.dumps({"moves":[]})); c=StudioController(t/"source"); c.add_move("jab","a"); self.assertEqual(c.moves()[0]["name"],"jab"); self.assertTrue(c.save(t/"edited").is_dir())

 def test_move_library_requires_provenance_and_valid_frames(self):
  document={"game_version":"GALE01-1.02","library_version":"1.0.0","source":"project-owned","moves":{"jab":{"display_name":"Jab","slot":"a","startup_frames":2,"active_frames":3,"recovery_frames":8,"license":"CC0"}}}; self.assertFalse(validate_library(document));
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/"library.json"; path.write_text(json.dumps(document)); self.assertEqual(load_library(path).moves["jab"]["startup_frames"],2)
  document["moves"]["bad"]={"display_name":"Bad","slot":"b","startup_frames":-1,"active_frames":0,"recovery_frames":0}; self.assertTrue(any(x.code=="frames" for x in validate_library(document)))

 def test_attribute_calibration_requires_units_and_bounded_defaults(self):
  document={"game_version":"GALE01-1.02","calibration_version":"1.0.0","unit_system":"project-units","source":"project-owned","attributes":{"weight":{"unit":"game-weight","minimum":1,"maximum":500,"default":90}}}; self.assertFalse(validate_calibration(document)); document["attributes"]["weight"]["default"]=600; self.assertTrue(any(x.code=="range" for x in validate_calibration(document)))

if __name__=="__main__":unittest.main()
