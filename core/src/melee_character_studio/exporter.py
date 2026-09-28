from __future__ import annotations
import hashlib, json, os, zipfile
from pathlib import Path
from .gltf import validate_model
from .moveset import validate_moveset, validate_animation_bindings
from .preview import _json, animation_durations
from .attributes import validate_attributes, validate_attribute_scales
FORBIDDEN={".exe",".dll",".so",".dylib",".elf",".dol",".iso"}
CHARACTER_FIELDS=("id","display_name","version","author","license")
COMPATIBILITIES={"visual-only","offline-gameplay","training-compatible","unknown"}
def safe(rel):
 p=Path(rel); return not p.is_absolute() and ".." not in p.parts and p.suffix.lower() not in FORBIDDEN
def validate_character(character):
 errors=[]
 for field in CHARACTER_FIELDS:
  value=character.get(field) if isinstance(character,dict) else None
  if not isinstance(value,str) or not value.strip(): errors.append(f"character.json {field} is missing or invalid")
 target=character.get("target_game_version") if isinstance(character,dict) else None
 if target!="GALE01-1.02": errors.append("character.json target_game_version must be GALE01-1.02")
 compatibility=character.get("compatibility") if isinstance(character,dict) else None
 if compatibility not in COMPATIBILITIES: errors.append("character.json compatibility must be one of: "+", ".join(sorted(COMPATIBILITIES)))
 return errors
def export_project(project,output):
 project=Path(project).resolve(); output=Path(output).resolve(); errors=[]
 for required in ("character.json","moveset.json"):
  if not (project/required).is_file(): errors.append(f"missing {required}")
 character_path=project/"character.json"
 character=None
 if character_path.is_file():
  try: character=json.loads(character_path.read_text())
  except (OSError,json.JSONDecodeError): errors.append("invalid character.json")
 if character is not None: errors.extend(validate_character(character))
 model=next(iter((project/"assets/model").glob("*.gltf")),None) if (project/"assets/model").is_dir() else None
 if model:
  report=validate_model(model)
  errors.extend(f"{x.path}: {x.message}" for x in report.diagnostics if x.severity=="error")
 if errors: raise ValueError("cannot export: "+"; ".join(errors))
 entries={}
 for f in sorted(project.rglob("*")):
  if not f.is_file() or f.name in {"checksums.json","validation-report.json"}: continue
  rel=f.relative_to(project).as_posix()
  if not safe(rel): raise ValueError(f"forbidden package path: {rel}")
  entries[rel]=f.read_bytes()
 moves=json.loads(entries["moveset.json"])
 move_errors=validate_moveset(moves)
 if move_errors: raise ValueError("invalid moveset: "+"; ".join(f"{x.path}: {x.message}" for x in move_errors))
 if model:
  animation_doc,animation_errors=_json(model); clips=[]
  if not animation_errors and animation_doc is not None:
   durations=animation_durations(model)
   clips=[{"name":clip.get("name",f"clip_{i}"),"duration":durations[i] if i<len(durations) else 0.0} for i,clip in enumerate(animation_doc.get("animations",[]))]
  binding_errors=validate_animation_bindings(moves,clips)
  if binding_errors: raise ValueError("invalid animation binding: "+"; ".join(f"{x.path}: {x.message}" for x in binding_errors))
 scales=character.get("attribute_scales",{})
 attr_errors=validate_attribute_scales(scales)
 if "attributes" in character: attr_errors+=validate_attributes(character.get("attributes",{}),{},scaled=scales if isinstance(scales,dict) else ())
 if attr_errors: raise ValueError("invalid attributes: "+"; ".join(f"{x.path}: {x.message}" for x in attr_errors))
 checks={k:hashlib.sha256(v).hexdigest() for k,v in sorted(entries.items())}
 validation={"valid":True,"file_count":len(entries),"deterministic_id":hashlib.sha256(b"".join(k.encode()+entries[k] for k in sorted(entries))).hexdigest()}
 entries["checksums.json"]=json.dumps(checks,sort_keys=True,separators=(",",":" )).encode()+b"\n"; entries["validation-report.json"]=json.dumps(validation,sort_keys=True,separators=(",",":" )).encode()+b"\n"
 output.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as z:
  for name,data in sorted(entries.items()):
   info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0)); info.compress_type=zipfile.ZIP_DEFLATED; z.writestr(info,data)
 return output
