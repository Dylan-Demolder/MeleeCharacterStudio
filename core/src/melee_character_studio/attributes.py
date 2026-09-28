from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class AttributeDiagnostic:
 path:str; code:str; message:str
RANGES={"walk_speed":(0,10),"dash_speed":(0,20),"run_speed":(0,20),"gravity":(0,10),"fall_speed":(0,20),"weight":(1,500),"size":(0.01,10),"air_speed":(0,20),"jump_velocity":(0,30),
 "ground_friction":(0,1),"jump_squat_frames":(1,15),"short_hop_velocity":(0,30),"air_jump_multiplier":(0,5),"max_jumps":(1,10),
 "air_acceleration":(0,1),"air_friction":(0,1),"fast_fall_speed":(0,20),"shield_size":(1,30),"landing_lag":(0,30),
 "landing_lag_nair":(0,60),"landing_lag_fair":(0,60),"landing_lag_bair":(0,60),"landing_lag_uair":(0,60),"landing_lag_dair":(0,60)}
INTEGER={"max_jumps"}
def validate_attribute_scales(scales):
 """``attribute_scales`` multiply the base fighter's value at compose time."""
 if not isinstance(scales,dict):return [AttributeDiagnostic("attribute_scales","type","must be an object")]
 d=[]
 for key,value in scales.items():
  if key not in RANGES:d.append(AttributeDiagnostic("attribute_scales."+key,"unknown","no verified attribute of that name"));continue
  if not isinstance(value,(int,float)) or isinstance(value,bool) or not 0<value<=10:d.append(AttributeDiagnostic("attribute_scales."+key,"range","scale must be a number in (0, 10]"))
 return d
def validate_attributes(attributes,base=None,scaled=()):
 if not isinstance(attributes,dict):return [AttributeDiagnostic("attributes","type","must be an object")]
 d=[]; base=base or {}; scaled=set(scaled)
 both=sorted(scaled&set(attributes))
 if both:d.append(AttributeDiagnostic("attribute_scales","conflict",f"set both absolutely and by scale: {both}"))
 for key,value in attributes.items():
  if not isinstance(value,(int,float)) or isinstance(value,bool):d.append(AttributeDiagnostic("attributes."+key,"type","must be numeric"));continue
  if key in INTEGER and value!=int(value):d.append(AttributeDiagnostic("attributes."+key,"type","must be a whole number"));continue
  if key in RANGES and not RANGES[key][0]<=value<=RANGES[key][1]:d.append(AttributeDiagnostic("attributes."+key,"range",f"must be between {RANGES[key][0]} and {RANGES[key][1]}"))
 for key in ("walk_speed","dash_speed","run_speed","gravity","fall_speed","weight","size","air_speed","jump_velocity"):
  if key not in attributes and key not in base and key not in scaled:d.append(AttributeDiagnostic("attributes."+key,"required","attribute must be inherited or overridden"))
 if not {"run_speed","dash_speed"}&scaled and attributes.get("run_speed",base.get("run_speed",0)) < attributes.get("dash_speed",base.get("dash_speed",0)):d.append(AttributeDiagnostic("attributes.run_speed","unreachable","run speed is below dash speed"))
 return d
