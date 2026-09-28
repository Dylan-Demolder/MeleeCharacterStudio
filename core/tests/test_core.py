import hashlib, json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
from melee_character_studio.gltf import validate_model
from melee_character_studio.exporter import export_project
from melee_character_studio.preview import render_svg, preview_scene
from melee_character_studio.asset_worker import AssetWorker
from melee_character_studio.hsd_model import inspect_hsd_model
from melee_character_studio.hsd_animation import scan_figatree, clip_transforms, export_hsd_animation
from melee_character_studio.hsd_texture import decode_hsd_texture, rgba_png, decode_png_rgba
from melee_character_studio.hsd_archive import (validate_hsd_archive, build_hsd_archive,
  serialize_hsd_archive, extract_hsd_symbols, extract_hsd_publics,
  validate_hsd_relocations, HsdArchiveError)
from melee_character_studio.hsd_patch import patch_hsd_streams, position_edits_from_hsd_scene
class StudioTests(unittest.TestCase):
 def test_gltf_validation_and_deterministic_export(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/"project"; (p/"assets/model").mkdir(parents=True)
   (p/"assets/model/model.gltf").write_text(json.dumps({"asset":{"version":"2.0"},"meshes":[],"images":[]}))
   (p/"character.json").write_text(json.dumps({"id":"clone","display_name":"Clone","version":"1.0.0","author":"test","license":"CC0","target_game_version":"GALE01-1.02","compatibility":"offline-gameplay"}))
   (p/"moveset.json").write_text(json.dumps({"base_fighter":"mario","moves":[]}))
   self.assertTrue(validate_model(p/"assets/model/model.gltf").valid)
   a=Path(td)/"a.melee-character"; b=Path(td)/"b.melee-character"; export_project(p,a); export_project(p,b); self.assertEqual(hashlib.sha256(a.read_bytes()).digest(),hashlib.sha256(b.read_bytes()).digest())

 def test_export_rejects_incomplete_character_metadata_before_writing(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/"project"; p.mkdir()
   (p/"character.json").write_text(json.dumps({"target_game_version":"GALE01-1.02","compatibility":"invalid"}))
   (p/"moveset.json").write_text(json.dumps({"base_fighter":"mario","moves":[]}))
   output=Path(td)/"rejected.melee-character"
   with self.assertRaisesRegex(ValueError, "character.json id is missing or invalid; character.json display_name is missing or invalid; character.json version is missing or invalid; character.json author is missing or invalid; character.json license is missing or invalid; character.json compatibility must be one of"):
    export_project(p,output)
   self.assertFalse(output.exists())

 def test_figatree_clip_scan_and_sampling(self):

  import struct
  body=bytearray(0x180); root=0x40; nodes=0x80; tracks=0x90; stream=0xA0
  struct.pack_into(">IIfII",body,root,1,0,60.0,nodes,tracks); body[nodes:nodes+2]=bytes((1,0xff))
  # one linear key: opcode 2, float 1.0 (packed little-endian), wait ten frames
  payload=bytes((0x02,))+struct.pack("<f",1.0)+bytes((10,)); body[stream:stream+len(payload)]=payload
  struct.pack_into(">HHBBBxI",body,tracks,len(payload),0,1,0,0,stream)
  archive=build_hsd_archive(body,publics=[(root,"PlyTest_ACTION_Wait_figatree")])
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/"PlTestAJ.dat"; path.write_bytes(archive); clips=scan_figatree(path); self.assertEqual(len(clips),1); self.assertEqual(clips[0].frames,60.0); self.assertAlmostEqual(clip_transforms(path,clips[0],5)[0][0][1],0.5,places=4)

 def test_figatree_translation_export_preserves_archive_chain(self):
  import struct
  body=bytearray(0x180); root=0x40; nodes=0x80; tracks=0x90; stream=0xA0
  struct.pack_into(">IIfII",body,root,1,0,20.0,nodes,tracks); body[nodes:nodes+2]=bytes((1,0xff)); payload=bytes((0x02,))+struct.pack("<f",1.0)+bytes((10,)); body[stream:stream+len(payload)]=payload; struct.pack_into(">HHBBBxI",body,tracks,len(payload),0,5,0,0,stream)
  archive=build_hsd_archive(body,publics=[(root,"PlyTest_ACTION_Wait_figatree")])
  with tempfile.TemporaryDirectory() as td:
   source=Path(td)/"PlTestAJ.dat"; output=Path(td)/"edited.dat"; source.write_bytes(archive); result=export_hsd_animation(source,output,0,[{"clip":0,"frame":5,"joint":0,"position":[.25,0,0],"rotation":[0,0,0],"scale":[0,0,0]}]); self.assertEqual(result[1],1); clips=scan_figatree(output); self.assertEqual(len(clips),1); self.assertAlmostEqual(clip_transforms(output,clips[0],5)[0][0][1],.75,places=3)

 def test_moveset_animation_binding_checks_clip_and_frame_range(self):
  from melee_character_studio.moveset import validate_animation_bindings
  moves={"moves":[{"name":"Jab","slot":"attack_neutral","animation":"Wait","startup_frames":2,"active_frames":3,"recovery_frames":8,"hitboxes":[{"id":"h","bone":"top","start_frame":1,"end_frame":12,"radius":1.0}]}]}
  self.assertFalse(validate_animation_bindings(moves,[{"name":"Wait","duration":1.0}]))
  self.assertEqual({d.code for d in validate_animation_bindings({"moves":[{"name":"Bad","slot":"idle","animation":"Missing"}]},[{"name":"Wait","duration":1.0}])},{"missing_animation"})
  self.assertEqual({d.code for d in validate_animation_bindings({"moves":[{"name":"Late","slot":"idle","animation":"Wait","hitboxes":[{"id":"h","bone":"top","start_frame":0,"end_frame":61,"radius":1.0}]}]},[{"name":"Wait","duration":1.0}])},{"animation_frame_range"})

 def test_moveset_timing_fields_are_non_negative_integers(self):
  from melee_character_studio.moveset import validate_moveset
  self.assertFalse(validate_moveset({"moves":[{"name":"Jab","slot":"attack_neutral","startup_frames":2,"active_frames":3,"recovery_frames":8}]}))
  diagnostics=validate_moveset({"moves":[{"name":"Bad","slot":"attack_neutral","startup_frames":-1,"active_frames":1.5}]})
  self.assertEqual({d.code for d in diagnostics},{"frame_range"})
  diagnostics=validate_moveset({"moves":[{"name":"Hit","slot":"attack_neutral","hitboxes":[{"id":"hb0","bone":"top","start_frame":4,"end_frame":2,"radius":0}]}]})
  self.assertEqual({d.code for d in diagnostics},{"frame_order","value_range"})
  self.assertFalse(validate_moveset({"moves":[{"name":"Hurt","slot":"idle","hurtboxes":[{"id":"body","bone":"joint_0","start_frame":0,"end_frame":10,"radius":2.0}]}]}))

 def test_hsd_patch_supports_uv_width_and_normal_s8(self):
  import struct
  archive=build_hsd_archive(bytearray(0x100))
  with tempfile.TemporaryDirectory() as td:
   source=Path(td)/"source.dat"; output=Path(td)/"patched.dat"; source.write_bytes(archive); edits=[{"stream_offset":0x40,"index":0,"stride":4,"type":"s16","frac":12,"components":2,"value":[1.25,.5]},{"stream_offset":0x70,"index":0,"stride":3,"type":"s8","frac":6,"components":3,"value":[0.0,-.5,1.0]}]; patch_hsd_streams(source,output,edits); raw=output.read_bytes(); self.assertEqual(struct.unpack_from(">hh",raw,0x60),(5120,2048)); self.assertEqual(struct.unpack_from(">bbb",raw,0x90),(0,-32,64))

 def test_hsd_scene_vertex_provenance_builds_patch_edit(self):
  scene={"hsd":True,"topology_ops":[],"geometry":[{"source_position_stream":32,"source_position_stride":6,"source_position_frac":11,"source_position_type":"s16","source_position_indices":[4],"source_position_values":[(1.0,2.0,3.0)],"base_vertices":[(1.0,2.0,3.0)],"vertices":[(1.0,2.0,3.0)]}]}; edits=position_edits_from_hsd_scene(scene,{(0,0):(1.25,2.0,3.0)}); self.assertEqual(edits[0]["index"],4); self.assertAlmostEqual(edits[0]["value"][0],1.25)

 def test_fixed_size_hsd_stream_patch_preserves_archive(self):
  import struct
  body=bytearray(0x100); struct.pack_into(">hhh",body,0x40,0,0,0); archive=build_hsd_archive(body)
  with tempfile.TemporaryDirectory() as td:
   source=Path(td)/"source.dat"; output=Path(td)/"patched.dat"; source.write_bytes(archive); result=patch_hsd_streams(source,output,[{"stream_offset":0x40,"index":0,"stride":6,"type":"s16","frac":0,"value":[12,-4,7]}]); self.assertEqual(result[1],1); raw=output.read_bytes(); self.assertEqual(struct.unpack_from(">hhh",raw,0x20+0x40),(12,-4,7)); self.assertEqual(validate_hsd_archive(raw).data_size,validate_hsd_archive(archive).data_size); self.assertEqual(validate_hsd_relocations(raw),validate_hsd_relocations(archive))

 def test_mesh_topology_operations_are_deterministic(self):
  from melee_character_studio.mesh_tools import apply_mesh_operations
  scene={"geometry":[{"vertices":[(0,0,0),(1,0,0),(0,1,0)],"indices":[0,1,2],"normals":[(0,0,1)]*3}]}; apply_mesh_operations(scene,[{"op":"reverse_winding","part":0},{"op":"duplicate","part":0}]); self.assertEqual(scene["geometry"][0]["indices"],[0,2,1]); self.assertEqual(len(scene["geometry"]),2); self.assertEqual(scene["geometry"][0]["normals"][0],(0,0,-1))

 def test_png_round_trip_for_imported_materials(self):
  raw=rgba_png({"width":2,"height":1,"rgba":bytes([255,0,0,255,0,255,0,128])}); decoded=decode_png_rgba(raw); self.assertEqual((decoded["width"],decoded["height"]),(2,1)); self.assertEqual(decoded["rgba"],bytes([255,0,0,255,0,255,0,128]))

 def test_hsd_i4_texture_decode(self):
  import struct
  class Info: data_size=128
  class Fake:
   info=Info()
   def __init__(self): self.data=memoryview(bytearray(128)); struct.pack_into(">I",self.data,0,16); struct.pack_into(">I",self.data,16,64); struct.pack_into(">HHI",self.data,20,8,8,0); self.data[64:96]=bytes([0xF0])*32
   def ptr(self,off):
    value=struct.unpack_from(">I",self.data,off)[0]; return None if value==0 else value
   def u32(self,off): return struct.unpack_from(">I",self.data,off)[0]
  image=decode_hsd_texture(Fake(),0); self.assertEqual((image["width"],image["height"],image["format"]),(8,8,0)); self.assertEqual(tuple(image["rgba"][:4]),(255,255,255,255)); self.assertEqual(tuple(image["rgba"][4:8]),(0,0,0,255))

 def test_hsd_joint_root_and_child_decode(self):
  import struct
  body=bytearray(0x380); root=0x100; joint=0x200; child=0x280
  struct.pack_into(">I",body,root+0x5c,joint)
  # joint: flags, child, next, rotation, scale, translation
  struct.pack_into(">IIIII3f3f3f",body,joint,*(0,9,child,0,0, 0,0,0, 1,1,1, 0,0,0))
  struct.pack_into(">IIIII3f3f3f",body,child,*(0,9,0,0,0, 0,0,0, 1,1,1, 0,2,0))
  archive=build_hsd_archive(body,publics=[(root,"ftDataTest")])
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/"PlTest.dat"; path.write_bytes(archive); report=inspect_hsd_model(path); self.assertEqual(report.root_symbol,"ftDataTest"); self.assertEqual(len(report.joints),2); self.assertEqual(report.joints[0].children,(1,)); self.assertEqual(report.joints[1].position,(0.0,2.0,0.0))

 def test_asset_worker_executes_off_ui_thread(self):
  import threading
  worker=AssetWorker(2); main=threading.current_thread().name; result=[]; done=threading.Event()
  worker.submit(lambda: threading.current_thread().name, done=lambda r: (result.append(r.value),done.set()))
  self.assertTrue(done.wait(2)); self.assertEqual(result[0].startswith("character-assets"),True); self.assertNotEqual(result[0],main); worker.close()

 def test_mesh_geometry_and_animation_sampling(self):
  import base64, struct
  with tempfile.TemporaryDirectory() as td:
   t=Path(td); raw=struct.pack("<9f3H2f6f", *(0,0,0, 1,0,0, 0,1,0, 0,1,2, 0,1, 0,0,0, 1,0,0))
   # positions, indices, animation times, animation translations
   uri="data:application/octet-stream;base64,"+base64.b64encode(raw).decode()
   doc={"asset":{"version":"2.0"},"buffers":[{"uri":uri,"byteLength":len(raw)}],"bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":36},{"buffer":0,"byteOffset":36,"byteLength":6},{"buffer":0,"byteOffset":42,"byteLength":8},{"buffer":0,"byteOffset":50,"byteLength":24}],"accessors":[{"bufferView":0,"componentType":5126,"count":3,"type":"VEC3"},{"bufferView":1,"componentType":5123,"count":3,"type":"SCALAR"},{"bufferView":2,"componentType":5126,"count":2,"type":"SCALAR"},{"bufferView":3,"componentType":5126,"count":2,"type":"VEC3"}],"meshes":[{"primitives":[{"attributes":{"POSITION":0},"indices":1}]}],"nodes":[{"name":"root","mesh":0}],"animations":[{"name":"move","samplers":[{"input":2,"output":3}],"channels":[{"sampler":0,"target":{"node":0,"path":"translation"}}]}]}
   model=t/"model.gltf"; model.write_text(json.dumps(doc)); a=preview_scene(model,0,0.0); b=preview_scene(model,0,0.5); edited=preview_scene(model,0,0.0,[{"clip":0,"frame":0,"joint":0,"position":[2,0,0]}]); scaled=preview_scene(model,0,0.0,[{"clip":0,"frame":0,"joint":0,"position":[0,0,0],"rotation":[0,0,1.57079632679],"scale":[1,1,1]}]); self.assertEqual(len(a["geometry"]),1); self.assertNotEqual(a["joints"][0]["position"],b["joints"][0]["position"]); self.assertAlmostEqual(edited["joints"][0]["position"][0],2.0); self.assertAlmostEqual(scaled["geometry"][0]["vertices"][1][0],0.0,places=4); self.assertAlmostEqual(scaled["geometry"][0]["vertices"][1][1],2.0,places=4); matrix_doc=json.loads(json.dumps(doc)); matrix_doc["nodes"][0]={"name":"root","mesh":0,"matrix":[2,0,0,0,0,2,0,0,0,0,2,0,5,0,0,1]}; matrix_doc["animations"]=[{"name":"move","samplers":[],"channels":[]}]; matrix_model=t/"matrix.gltf"; matrix_model.write_text(json.dumps(matrix_doc)); matrix_scene=preview_scene(matrix_model,0,0.0,[{"clip":0,"frame":0,"joint":0,"position":[1,0,0]}]); self.assertAlmostEqual(matrix_scene["geometry"][0]["vertices"][1][0],8.0,places=4)

 def test_skeleton_preview_is_deterministic_svg(self):
  with tempfile.TemporaryDirectory() as td:
   t=Path(td); model=t/"model.gltf"; model.write_text(json.dumps({"asset":{"version":"2.0"},"nodes":[{"name":"root","translation":[0,0,0],"children":[1]},{"name":"head","translation":[0,2,0]}],"meshes":[],"images":[]})); self.assertEqual(len(preview_scene(model)["joints"]),2); a=render_svg(model,t/"a.svg").read_text(); b=render_svg(model,t/"b.svg").read_text(); self.assertEqual(a,b); self.assertIn("skeleton preview",a)

if __name__=="__main__": unittest.main()


class HsdArchiveTests(unittest.TestCase):
 def test_big_endian_layout_and_tables(self):
  import struct
  body=b"\x00"*8; reloc=struct.pack(">I",4); public=struct.pack(">II",0,0); symbols=b"root\x00"
  size=0x20+len(body)+len(reloc)+len(public)+len(symbols)
  header=struct.pack(">5I",size,len(body),1,1,0)+b"HSD1"+b"\x00"*8
  info=validate_hsd_archive(header+body+reloc+public+symbols)
  self.assertEqual((info.data_size,info.relocations,info.publics,info.symbol_bytes),(8,1,1,5))
 def test_deterministic_container_writer_round_trips(self):
  archive=build_hsd_archive(b"\x00"*8,relocations=[0],publics=[(0,"root")],externs=[(4,"root")])
  info=validate_hsd_archive(archive); self.assertEqual(info.file_size,len(archive)); self.assertEqual(extract_hsd_symbols(archive),("root",)); self.assertEqual(extract_hsd_publics(archive),{"root":0}); self.assertEqual(validate_hsd_relocations(archive),(0,)); self.assertEqual(archive,build_hsd_archive(b"\x00"*8,relocations=[0],publics=[(0,"root")],externs=[(4,"root")]))

  def test_unmodified_reencode_is_byte_exact_and_preserves_tables(self):
   import struct
   body=bytearray(0x30)
   struct.pack_into(">II",body,0,0x10,0x20)
   archive=build_hsd_archive(body,relocations=[0,4,8],
    publics=[(0,"ftDataTest"),(0x10,"ftTestNormalAJ")],
    externs=[(4,"ftDataTest")],version=b"001B")
   rebuilt=serialize_hsd_archive(archive)
   self.assertEqual(rebuilt,archive)
   original=validate_hsd_archive(archive); result=validate_hsd_archive(rebuilt)
   self.assertEqual((result.file_size,result.data_size,result.relocations,
    result.publics,result.externs,result.string_table_offset),
    (original.file_size,original.data_size,original.relocations,
     original.publics,original.externs,original.string_table_offset))
   self.assertEqual(extract_hsd_symbols(rebuilt),
    ("ftDataTest","ftTestNormalAJ"))
   self.assertEqual(validate_hsd_relocations(rebuilt),(0x10,0x20,0))

  def test_rejects_bad_relocation_value_and_symbol_termination(self):
   import struct
   archive=build_hsd_archive(struct.pack(">I",999),relocations=[0])
   with self.assertRaises(HsdArchiveError): validate_hsd_relocations(archive)

  symbols=bytearray(build_hsd_archive(b"\x00"*4,publics=[(0,"root")]))
  symbols[-1]=ord("x")
  with self.assertRaises(HsdArchiveError): extract_hsd_symbols(symbols)

 def test_rejects_bad_size_and_offsets(self):
  import struct
  body=b"\x00"*4; size=0x20+len(body)+4
  bad=struct.pack(">5I",size,len(body),1,0,0)+b"HSD1"+b"\x00"*8+body+struct.pack(">I",4)
  with self.assertRaises(HsdArchiveError): validate_hsd_archive(bad)
  with self.assertRaises(HsdArchiveError): validate_hsd_archive(bad[:-1])
