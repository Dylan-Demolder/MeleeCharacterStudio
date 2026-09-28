from __future__ import annotations
import json, os, shutil, tempfile
from pathlib import Path
from .attributes import validate_attributes
from .moveset import validate_moveset

class ProjectEditError(ValueError): pass

class ProjectEditor:
    """Small deterministic, source-preserving move/attribute editor.

    Edits are held in memory and `save` writes a new project directory through a
    sibling staging directory. The input project is never changed.
    """
    def __init__(self, project):
        self.project=Path(project).expanduser().resolve()
        self.character=self._load("character.json")
        self.moveset=self._load("moveset.json")
    def _load(self,name):
        path=self.project/name
        if not path.is_file(): raise ProjectEditError(f"missing {name}")
        try: return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc: raise ProjectEditError(f"invalid {name}: {exc}") from exc
    def add_move(self,name,slot,reference=None,transitions=None,hitboxes=None,hurtboxes=None):
        move={"name":name,"slot":slot}
        if reference is not None: move["reference"]=reference
        if transitions is not None: move["transitions"]=list(transitions)
        if hitboxes is not None: move["hitboxes"]=[dict(hitbox) for hitbox in hitboxes]
        if hurtboxes is not None: move["hurtboxes"]=[dict(hurtbox) for hurtbox in hurtboxes]
        self.moveset.setdefault("moves",[]).append(move)
        return move
    def update_move(self,old_name,name=None,slot=None,reference=None,transitions=None,timing=None,hitboxes=None,hurtboxes=None):
        moves=self.moveset.get("moves",[])
        target=next((m for m in moves if isinstance(m,dict) and m.get("name")==old_name),None)
        if target is None: raise ProjectEditError(f"move not found: {old_name}")
        if name is not None: target["name"]=name
        if slot is not None: target["slot"]=slot
        if reference is not None: target["reference"]=reference
        if transitions is not None: target["transitions"]=list(transitions)
        if timing is not None:
            for key in ("startup_frames","active_frames","recovery_frames"):
                if key in timing: target[key]=int(timing[key])
        if hitboxes is not None: target["hitboxes"]=[dict(hitbox) for hitbox in hitboxes]
        if hurtboxes is not None: target["hurtboxes"]=[dict(hurtbox) for hurtbox in hurtboxes]
        return target

    def remove_move(self,name):
        moves=self.moveset.get("moves",[])
        kept=[m for m in moves if isinstance(m,dict) and m.get("name")!=name]
        if len(kept)==len(moves): raise ProjectEditError(f"move not found: {name}")
        self.moveset["moves"]=kept
    def set_attribute(self,name,value):
        if isinstance(value,bool) or not isinstance(value,(int,float)): raise ProjectEditError("attribute value must be numeric")
        self.character.setdefault("attributes",{})[name]=value
    def validate(self):
        diagnostics=[]
        diagnostics.extend(validate_moveset(self.moveset))
        diagnostics.extend(validate_attributes(self.character.get("attributes",{}),{}))
        return diagnostics
    def save(self,output):
        output=Path(output).expanduser().resolve()
        if output==self.project: raise ProjectEditError("output must differ from source project")
        output.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix=output.name+"-",dir=output.parent))
        try:
            shutil.copytree(self.project,stage/"project",dirs_exist_ok=True)
            (stage/"project/character.json").write_text(json.dumps(self.character,indent=2,sort_keys=True)+"\n",encoding="utf-8")
            (stage/"project/moveset.json").write_text(json.dumps(self.moveset,indent=2,sort_keys=True)+"\n",encoding="utf-8")
            if output.exists(): raise ProjectEditError(f"output already exists: {output}")
            os.replace(stage/"project",output)
        finally: shutil.rmtree(stage,ignore_errors=True)
        return output
