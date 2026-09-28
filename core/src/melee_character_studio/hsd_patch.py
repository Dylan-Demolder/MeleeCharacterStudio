from __future__ import annotations
import struct
from pathlib import Path
from .hsd_archive import validate_hsd_archive, validate_hsd_relocations


def _pack_component(kind,value,frac=0):
    if kind=="s8":
        scaled=round(float(value)*(1<<int(frac)))
        if not -128<=scaled<=127: raise ValueError("s8 vertex component is out of range")
        return struct.pack(">b",scaled)
    if kind=="u8":
        scaled=round(float(value)*(1<<int(frac)))
        if not 0<=scaled<=255: raise ValueError("u8 vertex component is out of range")
        return struct.pack(">B",scaled)
    if kind=="s16":
        scaled=round(float(value)*(1<<int(frac)))
        if not -32768<=scaled<=32767: raise ValueError("s16 vertex component is out of range")
        return struct.pack(">h",scaled)
    if kind=="u16":
        scaled=round(float(value)*(1<<int(frac)))
        if not 0<=scaled<=65535: raise ValueError("u16 vertex component is out of range")
        return struct.pack(">H",scaled)
    if kind=="f32": return struct.pack(">f",float(value))
    raise ValueError(f"unsupported HSD component type: {kind}")


def patch_hsd_streams(source,output,edits):
    """Patch fixed-size HSD vertex streams without changing archive layout.

    Each edit contains a data-relative ``stream_offset``, vertex ``index``,
    ``stride``, component ``type`` (``s16``, ``u16``, or ``f32``), optional
    fractional-bit count, and a three-value ``value``. This is deliberately
    source-space: callers must resolve HSD PObj provenance before patching.
    """
    source=Path(source).expanduser().resolve(); output=Path(output).expanduser().resolve(); raw=bytearray(source.read_bytes()); info=validate_hsd_archive(raw); data_base=0x20; changed=set()
    if not isinstance(edits,list): raise ValueError("HSD edits must be a list")
    for edit in edits:
        if not isinstance(edit,dict): raise ValueError("each HSD edit must be an object")
        stream=int(edit["stream_offset"]); index=int(edit["index"]); stride=int(edit.get("stride",6)); kind=str(edit.get("type","s16")); frac=int(edit.get("frac",0)); values=edit.get("value"); components=int(edit.get("components",len(values) if isinstance(values,(list,tuple)) else 0))
        if stream<0 or index<0 or stride<=0 or components not in (2,3) or not isinstance(values,(list,tuple)) or len(values)!=components: raise ValueError("invalid HSD stream edit")
        width={"s8":1,"u8":1,"s16":2,"u16":2,"f32":4}.get(kind)
        if width is None: raise ValueError(f"unsupported HSD component type: {kind}")
        position=data_base+stream+index*stride
        if position< data_base or position+width*components>data_base+info.data_size: raise ValueError("HSD stream edit is outside the archive data section")
        for component,value in enumerate(values): raw[position+component*width:position+(component+1)*width]=_pack_component(kind,value,frac)
        changed.add((stream,index))
    validate_hsd_relocations(bytes(raw))
    output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(raw); return output,len(changed)


def _scene_stream_edits(scene, changes, *, source_prefix, current_field, base_field, value_field, index_field):
    edits_by_key={}; parts=scene.get("geometry",[])
    for key,value in (changes or {}).items():
        part_index,vertex_index=map(int,key)
        if not 0<=part_index<len(parts): raise ValueError("edited HSD mesh part is out of range")
        part=parts[part_index]; indices=part.get(f"source_{source_prefix}_indices",[]); source_values=part.get(f"source_{source_prefix}_values",[]); base_values=part.get(base_field,part.get(current_field,[]))
        if not 0<=vertex_index<len(indices) or indices[vertex_index] is None: raise ValueError(f"edited vertex has no source HSD {source_prefix} index")
        if not 0<=vertex_index<len(source_values) or not 0<=vertex_index<len(base_values): raise ValueError(f"edited vertex lacks HSD {source_prefix} provenance")
        stream=part.get(f"source_{source_prefix}_stream"); stride=part.get(f"source_{source_prefix}_stride"); kind=part.get(f"source_{source_prefix}_type"); frac=part.get(f"source_{source_prefix}_frac")
        if stream is None or stride is None or kind not in ("s8","u8","s16","u16","f32"): raise ValueError(f"edited vertex uses an unsupported HSD {source_prefix} stream")
        desired=tuple(float(x) for x in value); original=base_values[vertex_index]; raw=source_values[vertex_index]; components=len(desired); patched=tuple(float(raw[axis])+(desired[axis]-float(original[axis])) for axis in range(components)); edit={"stream_offset":int(stream),"index":int(indices[vertex_index]),"stride":int(stride),"type":kind,"frac":int(frac or 0),"components":components,"value":list(patched),"archive":part.get(f"source_{source_prefix}_archive","model")}; marker=(edit["archive"],int(stream),int(indices[vertex_index]),kind)
        previous=edits_by_key.get(marker)
        if previous is not None and previous["value"]!=edit["value"]: raise ValueError("duplicated HSD source vertex has conflicting edits")
        edits_by_key[marker]=edit
    return list(edits_by_key.values())


def position_edits_from_hsd_scene(scene,vertex_edits,uv_edits=None,normal_edits=None):
    """Translate Studio HSD scene edits into fixed-size source stream edits.

    The conversion uses the displayed rest-pose delta as a source-space delta.
    It rejects topology changes and unsupported/direct streams. Envelope-space
    edits should be reviewed before being used in a game build.
    """
    if scene.get("hsd") is not True: raise ValueError("scene is not an HSD scene")
    if scene.get("topology_ops"): raise ValueError("topology edits cannot be written as fixed-size HSD patches")
    edits=[]
    edits.extend(_scene_stream_edits(scene,vertex_edits,source_prefix="position",current_field="vertices",base_field="base_vertices",value_field="value",index_field="index"))
    edits.extend(_scene_stream_edits(scene,uv_edits,source_prefix="uv",current_field="uvs",base_field="base_uvs",value_field="value",index_field="index"))
    edits.extend(_scene_stream_edits(scene,normal_edits,source_prefix="normal",current_field="normals",base_field="base_normals",value_field="value",index_field="index"))
    return edits
