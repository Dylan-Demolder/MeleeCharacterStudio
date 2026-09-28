from __future__ import annotations
import base64, json, struct
from dataclasses import dataclass, field
from pathlib import Path
@dataclass(frozen=True)
class Budget:
 max_meshes:int=64; max_primitives:int=256; max_materials:int=64; max_textures:int=128; max_texture_dimension:int=2048; max_joints:int=128; max_file_bytes:int=256*1024*1024
@dataclass(frozen=True)
class Diagnostic:
 severity:str; path:str; code:str; message:str
@dataclass
class ModelReport:
 valid:bool; diagnostics:list[Diagnostic]=field(default_factory=list); meshes:int=0; primitives:int=0; materials:int=0; textures:int=0; joints:int=0
def _json(path):
 p=Path(path); raw=p.read_bytes()
 if p.suffix.lower()==".glb":
  if len(raw)<20 or raw[:4]!=b"glTF": return None,[Diagnostic("error","file","header","invalid GLB header")]
  version,size=struct.unpack_from("<II",raw,4)
  if version!=2 or size!=len(raw): return None,[Diagnostic("error","file","version","GLB must be version 2 and have a valid size")]
  typ,chunk_size=struct.unpack_from("<II",raw,12)
  if typ!=0x4e4f534a: return None,[Diagnostic("error","file","json_chunk","GLB JSON chunk missing")]
  try:return json.loads(raw[20:20+chunk_size].decode("utf-8")),[]
  except Exception:return None,[Diagnostic("error","file","json","invalid GLB JSON")]
 try:return json.loads(raw),[]
 except json.JSONDecodeError as e:return None,[Diagnostic("error","file","json",str(e))]
def validate_model(path,budget=Budget()):
 p=Path(path); d=[]
 if not p.is_file(): return ModelReport(False,[Diagnostic("error","file","missing","model file does not exist")])
 if p.stat().st_size>budget.max_file_bytes: d.append(Diagnostic("error","file","budget",f"file exceeds {budget.max_file_bytes} bytes"))
 if p.suffix.lower() not in {".gltf",".glb"}: d.append(Diagnostic("error","file","format","only glTF 2.0 and GLB are supported")); return ModelReport(False,d)
 doc,parse= _json(p); d.extend(parse)
 if doc is None:return ModelReport(False,d)
 if doc.get("asset",{}).get("version")!="2.0": d.append(Diagnostic("error","asset.version","version","glTF asset version 2.0 is required"))
 for uri in [x.get("uri","") for x in doc.get("images",[])]:
  if uri.startswith("data:"): continue
  q=Path(uri)
  if q.is_absolute() or ".." in q.parts: d.append(Diagnostic("error","images.uri","unsafe_path",f"unsafe external image URI: {uri}"))
  elif not (p.parent/q).is_file(): d.append(Diagnostic("error","images.uri","missing","external image is missing: "+uri))
 meshes=len(doc.get("meshes",[])); prim=sum(len(m.get("primitives",[])) for m in doc.get("meshes",[])); materials=len(doc.get("materials",[])); textures=len(doc.get("textures",[])); joints=sum(len(s.get("joints",[])) for s in doc.get("skins",[]))
 if meshes>budget.max_meshes:d.append(Diagnostic("error","meshes","budget",f"{meshes} meshes exceeds {budget.max_meshes}"))
 if prim>budget.max_primitives:d.append(Diagnostic("error","meshes.primitives","budget",f"{prim} primitives exceeds {budget.max_primitives}"))
 if materials>budget.max_materials:d.append(Diagnostic("error","materials","budget",f"{materials} materials exceeds {budget.max_materials}"))
 if textures>budget.max_textures:d.append(Diagnostic("error","textures","budget",f"{textures} textures exceeds {budget.max_textures}"))
 if joints>budget.max_joints:d.append(Diagnostic("error","skins.joints","budget",f"{joints} joints exceeds {budget.max_joints}"))
 for i,s in enumerate(doc.get("skins",[])):
  if not s.get("joints"): d.append(Diagnostic("error",f"skins[{i}].joints","required","skin has no joints"))
 return ModelReport(not any(x.severity=="error" for x in d),d,meshes,prim,materials,textures,joints)
