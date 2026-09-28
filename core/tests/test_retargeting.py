import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from melee_character_studio.retargeting import auto_map, retarget_pose
class RetargetTests(unittest.TestCase):
 def test_missing_joint_is_diagnostic(self):
  r=auto_map(["root","pelvis","spine","head"]); self.assertFalse(r.valid); self.assertIn("hand_l",r.unmapped)
 def test_aliases_map(self):
  names=["Root","Pelvis","Spine","Head","ShoulderLeft","UpperArmLeft","ForearmLeft","HandLeft","ShoulderRight","UpperArmRight","ForearmRight","HandRight","ThighLeft","ShinLeft","FootLeft","ThighRight","ShinRight","FootRight"]
  self.assertTrue(auto_map(names).valid)
 def test_transform_retargeting_applies_rest_delta_and_length_ratio(self):
  pose=retarget_pose({"arm":{"translation":[2,0,0],"rotation":[0,0,0,1]}},{"upper_arm_l":"arm"},{"arm":{"translation":[1,0,0]}},{"upper_arm_l":{"translation":[2,0,0]}}); self.assertEqual(pose["upper_arm_l"]["translation"],[4.0,0.0,0.0]); self.assertEqual(pose["upper_arm_l"]["rotation"],[0.0,0.0,0.0,1.0])
 def test_transform_retargeting_rejects_missing_source(self):
  with self.assertRaises(ValueError): retarget_pose({}, {"head":"missing"})

if __name__=="__main__":unittest.main()
