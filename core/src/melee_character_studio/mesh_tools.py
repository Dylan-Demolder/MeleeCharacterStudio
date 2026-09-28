from __future__ import annotations


def _clone_part(part):
    clone=dict(part)
    for key in ("vertices","indices","uvs","normals","skin"):
        if key in part: clone[key]=list(part[key])
    if isinstance(part.get("material"),dict): clone["material"]=dict(part["material"])
    return clone


def apply_mesh_operation(scene,operation):
    """Apply one authoring topology operation to a scene in place."""
    if not isinstance(operation,dict): raise ValueError("mesh operation must be an object")
    op=str(operation.get("op","")); parts=scene.setdefault("geometry",[]); index=int(operation.get("part",-1))
    if op=="duplicate":
        if not 0<=index<len(parts): raise ValueError("duplicate mesh part is out of range")
        parts.insert(index+1,_clone_part(parts[index]))
    elif op=="delete":
        if not 0<=index<len(parts): raise ValueError("delete mesh part is out of range")
        parts.pop(index)
    elif op=="reverse_winding":
        if not 0<=index<len(parts): raise ValueError("reverse mesh part is out of range")
        part=parts[index]; indices=list(part.get("indices",[]))
        for cursor in range(0,len(indices)-2,3): indices[cursor+1],indices[cursor+2]=indices[cursor+2],indices[cursor+1]
        part["indices"]=indices
        if part.get("normals"): part["normals"]=[tuple(-float(value) for value in normal[:3]) for normal in part["normals"]]
    else: raise ValueError(f"unsupported mesh operation: {op}")
    return scene


def apply_mesh_operations(scene,operations):
    for operation in operations or (): apply_mesh_operation(scene,operation)
    return scene
