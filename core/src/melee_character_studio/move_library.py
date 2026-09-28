from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
from .moveset import MoveDiagnostic

@dataclass(frozen=True)
class MoveLibrary:
    game_version:str
    library_version:str
    moves:dict[str,dict]
    source:str

def validate_library(document):
    diagnostics=[]
    if not isinstance(document,dict): return [MoveDiagnostic("library","type","must be an object")]
    for key in ("game_version","library_version","moves","source"):
        if key not in document: diagnostics.append(MoveDiagnostic("library."+key,"required","field is required"))
    if document.get("game_version")!="GALE01-1.02": diagnostics.append(MoveDiagnostic("library.game_version","game_version","must target GALE01-1.02"))
    if not isinstance(document.get("library_version"),str) or not document.get("library_version"): diagnostics.append(MoveDiagnostic("library.library_version","version","library version is required"))
    if not isinstance(document.get("source"),str) or not document.get("source"): diagnostics.append(MoveDiagnostic("library.source","provenance","source/provenance is required"))
    moves=document.get("moves")
    if not isinstance(moves,dict): return diagnostics+[MoveDiagnostic("library.moves","type","must map IDs to move definitions")]
    for ident,move in moves.items():
        path=f"library.moves.{ident}"
        if not isinstance(ident,str) or not ident: diagnostics.append(MoveDiagnostic(path,"id","move ID must be non-empty")); continue
        if not isinstance(move,dict): diagnostics.append(MoveDiagnostic(path,"type","move must be an object")); continue
        for key in ("display_name","slot","startup_frames","active_frames","recovery_frames"):
            if key not in move: diagnostics.append(MoveDiagnostic(path+"."+key,"required","field is required"))
        if not isinstance(move.get("display_name"),str) or not move.get("display_name"): diagnostics.append(MoveDiagnostic(path+".display_name","type","display name is required"))
        if not isinstance(move.get("slot"),str) or not move.get("slot"): diagnostics.append(MoveDiagnostic(path+".slot","type","slot is required"))
        for key in ("startup_frames","active_frames","recovery_frames"):
            value=move.get(key)
            if isinstance(value,bool) or not isinstance(value,int) or value<0: diagnostics.append(MoveDiagnostic(path+"."+key,"frames","frame count must be a non-negative integer"))
        if move.get("license") is not None and not isinstance(move.get("license"),str): diagnostics.append(MoveDiagnostic(path+".license","provenance","license must be a string"))
    return diagnostics

def load_library(path):
    source=Path(path).expanduser().resolve()
    try: document=json.loads(source.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise ValueError(f"invalid move library: {exc}") from exc
    diagnostics=validate_library(document)
    if diagnostics: raise ValueError("invalid move library: "+"; ".join(d.message for d in diagnostics))
    return MoveLibrary(document["game_version"],document["library_version"],dict(document["moves"]),document["source"])
