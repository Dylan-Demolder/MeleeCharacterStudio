from __future__ import annotations
from dataclasses import dataclass
import math
@dataclass(frozen=True)
class MoveDiagnostic:
 path:str; code:str; message:str
def validate_moveset(moveset,available_moves=None):
 available=set(available_moves or ()); d=[]
 if not isinstance(moveset,dict): return [MoveDiagnostic("moveset","type","must be an object")]
 moves=moveset.get("moves")
 if not isinstance(moves,list): return [MoveDiagnostic("moveset.moves","type","must be a list")]
 bindings={} ; names=set()
 for i,m in enumerate(moves):
  path=f"moveset.moves[{i}]"
  if not isinstance(m,dict): d.append(MoveDiagnostic(path,"type","must be an object")); continue
  name=m.get("name"); slot=m.get("slot"); ref=m.get("reference")
  if not isinstance(name,str) or not name: d.append(MoveDiagnostic(path+".name","required","move name is required"))
  elif name in names:d.append(MoveDiagnostic(path+".name","duplicate","move names must be unique"))
  else:names.add(name)
  if not isinstance(slot,str) or not slot:d.append(MoveDiagnostic(path+".slot","required","input/state slot is required"))
  elif slot in bindings:d.append(MoveDiagnostic(path+".slot","duplicate_binding",f"slot already bound by {bindings[slot]}"))
  else:bindings[slot]=name
  if ref is not None and (not isinstance(ref,str) or (available and ref not in available)): d.append(MoveDiagnostic(path+".reference","missing_reference",f"approved move {ref!r} is unavailable"))
  for timing in ("startup_frames","active_frames","recovery_frames"):
   if timing in m:
    value=m.get(timing)
    if isinstance(value,bool) or not isinstance(value,int) or value<0: d.append(MoveDiagnostic(path+"."+timing,"frame_range",f"{timing} must be a non-negative integer"))
  hitboxes=m.get("hitboxes",[])
  if not isinstance(hitboxes,list): d.append(MoveDiagnostic(path+".hitboxes","type","hitboxes must be a list")); hitboxes=[]
  hitbox_ids=set()
  for hidx,h in enumerate(hitboxes):
   hpath=f"{path}.hitboxes[{hidx}]"
   if not isinstance(h,dict): d.append(MoveDiagnostic(hpath,"type","hitbox must be an object")); continue
   hid=h.get("id")
   if not isinstance(hid,str) or not hid: d.append(MoveDiagnostic(hpath+".id","required","hitbox id is required"))
   elif hid in hitbox_ids: d.append(MoveDiagnostic(hpath+".id","duplicate","hitbox ids must be unique within a move"))
   else: hitbox_ids.add(hid)
   if not isinstance(h.get("bone"),str) or not h.get("bone"): d.append(MoveDiagnostic(hpath+".bone","required","hitbox bone is required"))
   start,end=h.get("start_frame"),h.get("end_frame")
   for field,value in (("start_frame",start),("end_frame",end)):
    if isinstance(value,bool) or not isinstance(value,int) or value<0: d.append(MoveDiagnostic(hpath+"."+field,"frame_range",f"{field} must be a non-negative integer"))
   if isinstance(start,int) and isinstance(end,int) and start>=0 and end>=0 and end<start: d.append(MoveDiagnostic(hpath+".end_frame","frame_order","end_frame must be greater than or equal to start_frame"))
   for field in ("damage","angle","knockback_growth","base_knockback","radius"):
    if field in h:
     value=h[field]
     if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)) or (field=="radius" and value<=0) or (field!="radius" and value<0): d.append(MoveDiagnostic(hpath+"."+field,"value_range",f"{field} has an invalid value"))
   offset=h.get("offset",[0.0,0.0,0.0])
   if not isinstance(offset,list) or len(offset)!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(float(x)) for x in offset): d.append(MoveDiagnostic(hpath+".offset","vector","offset must contain three finite numbers"))
  hurtboxes=m.get("hurtboxes",[])
  if not isinstance(hurtboxes,list): d.append(MoveDiagnostic(path+".hurtboxes","type","hurtboxes must be a list")); hurtboxes=[]
  hurtbox_ids=set()
  for hidx,h in enumerate(hurtboxes):
   hpath=f"{path}.hurtboxes[{hidx}]"
   if not isinstance(h,dict): d.append(MoveDiagnostic(hpath,"type","hurtbox must be an object")); continue
   hid=h.get("id")
   if not isinstance(hid,str) or not hid: d.append(MoveDiagnostic(hpath+".id","required","hurtbox id is required"))
   elif hid in hurtbox_ids: d.append(MoveDiagnostic(hpath+".id","duplicate","hurtbox ids must be unique within a move"))
   else: hurtbox_ids.add(hid)
   if not isinstance(h.get("bone"),str) or not h.get("bone"): d.append(MoveDiagnostic(hpath+".bone","required","hurtbox bone is required"))
   start,end=h.get("start_frame"),h.get("end_frame")
   for field,value in (("start_frame",start),("end_frame",end)):
    if isinstance(value,bool) or not isinstance(value,int) or value<0: d.append(MoveDiagnostic(hpath+"."+field,"frame_range",f"{field} must be a non-negative integer"))
   if isinstance(start,int) and isinstance(end,int) and start>=0 and end>=0 and end<start: d.append(MoveDiagnostic(hpath+".end_frame","frame_order","end_frame must be greater than or equal to start_frame"))
   radius=h.get("radius")
   if isinstance(radius,bool) or not isinstance(radius,(int,float)) or not math.isfinite(float(radius)) or radius<=0: d.append(MoveDiagnostic(hpath+".radius","value_range","radius must be a positive finite number"))
   offset=h.get("offset",[0.0,0.0,0.0])
   if not isinstance(offset,list) or len(offset)!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(float(x)) for x in offset): d.append(MoveDiagnostic(hpath+".offset","vector","offset must contain three finite numbers"))
 # transitions must reference known move names and be acyclic unless explicitly interruptible.
 for i,m in enumerate(moves):
  if not isinstance(m,dict):continue
  for j,target in enumerate(m.get("transitions",[])):
   if target not in names:d.append(MoveDiagnostic(f"moveset.moves[{i}].transitions[{j}]","missing_reference",f"unknown target {target!r}"))
 return d


def validate_animation_bindings(moveset,clips):
    """Validate move clip names and collision windows against imported clips."""
    if not clips:return []
    d=[]; lookup={str(c.get("name",f"clip_{i}")):float(c.get("duration",0.0)) for i,c in enumerate(clips) if isinstance(c,dict)}
    for index,move in enumerate(moveset.get("moves",[]) if isinstance(moveset,dict) else []):
        if not isinstance(move,dict) or move.get("animation") is None: continue
        animation=str(move.get("animation")); path=f"moveset.moves[{index}].animation"
        if animation not in lookup: d.append(MoveDiagnostic(path,"missing_animation",f"animation clip {animation!r} is not present in the imported model")); continue
        max_frame=max([int(move.get("startup_frames",0) or 0)+int(move.get("active_frames",0) or 0)+int(move.get("recovery_frames",0) or 0)]+[int(x.get("end_frame",0)) for group in (move.get("hitboxes",[]),move.get("hurtboxes",[])) for x in group if isinstance(x,dict)])
        available=int(math.floor(lookup[animation]*60.0+1e-5))
        if max_frame>available: d.append(MoveDiagnostic(path,"animation_frame_range",f"move reaches frame {max_frame}, but clip {animation!r} has {available} frames"))
    return d
