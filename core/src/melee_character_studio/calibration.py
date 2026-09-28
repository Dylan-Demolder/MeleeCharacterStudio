from __future__ import annotations
import json
from pathlib import Path
from .moveset import MoveDiagnostic

def validate_calibration(document):
    d=[]
    if not isinstance(document,dict): return [MoveDiagnostic("calibration","type","must be an object")]
    for key in ("game_version","calibration_version","unit_system","source","attributes"):
        if key not in document: d.append(MoveDiagnostic("calibration."+key,"required","field is required"))
    if document.get("game_version")!="GALE01-1.02": d.append(MoveDiagnostic("calibration.game_version","game_version","must target GALE01-1.02"))
    if not isinstance(document.get("attributes"),dict): return d+[MoveDiagnostic("calibration.attributes","type","must map attributes") ]
    for name,value in document["attributes"].items():
        path=f"calibration.attributes.{name}"
        if not isinstance(value,dict): d.append(MoveDiagnostic(path,"type","attribute calibration must be an object")); continue
        for key in ("unit","minimum","maximum","default"):
            if key not in value: d.append(MoveDiagnostic(path+"."+key,"required","field is required"))
        if not isinstance(value.get("unit"),str) or not value.get("unit"): d.append(MoveDiagnostic(path+".unit","unit","unit is required"))
        numbers=[value.get(k) for k in ("minimum","maximum","default")]
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) for x in numbers): d.append(MoveDiagnostic(path,"number","minimum, maximum, and default must be numeric")); continue
        if value["minimum"]>value["maximum"]: d.append(MoveDiagnostic(path,"range","minimum exceeds maximum"))
        if not value["minimum"]<=value["default"]<=value["maximum"]: d.append(MoveDiagnostic(path+".default","range","default is outside calibrated range"))
    return d

def load_calibration(path):
    source=Path(path).expanduser().resolve(); document=json.loads(source.read_text(encoding="utf-8")); d=validate_calibration(document)
    if d: raise ValueError("invalid calibration: "+"; ".join(x.message for x in d))
    return document
