from __future__ import annotations
import struct
from dataclasses import dataclass
from pathlib import Path
from .hsd_archive import validate_hsd_archive, extract_hsd_publics, extract_hsd_symbols, extract_hsd_tables, build_hsd_archive

@dataclass(frozen=True)
class FigaTrack:
    length:int; start_frame:int; obj_type:int; frac_value:int; frac_slope:int; ad_offset:int
@dataclass(frozen=True)
class FigaClip:
    name:str; frames:float; nodes:tuple[int,...]; tracks:tuple[FigaTrack,...]; archive_offset:int; archive_size:int

class _Reader:
    def __init__(self,raw,archive_offset=0):
        self.raw=raw; self.offset=archive_offset; fs=struct.unpack_from('>I',raw,archive_offset)[0]; self.seg=memoryview(raw)[archive_offset:archive_offset+fs]; self.info=validate_hsd_archive(self.seg); self.data=memoryview(self.seg)[0x20:0x20+self.info.data_size]
    def u32(self,o):return struct.unpack_from('>I',self.data,o)[0]
    def f32(self,o):return struct.unpack_from('>f',self.data,o)[0]
    def ptr(self,o):
        v=self.u32(o); return None if v==0 or v>=self.info.data_size else v

def scan_figatree(path):
    raw=Path(path).read_bytes(); result=[]; off=0
    while off+0x20<=len(raw):
        fs=struct.unpack_from('>I',raw,off)[0]
        if fs<0x20 or off+fs>len(raw): break
        try:
            r=_Reader(raw,off); publics=extract_hsd_publics(r.seg); symbol=next(iter(publics),None); root=publics.get(symbol) if symbol else None
            if root is not None and root+0x14<=r.info.data_size:
                frames=r.f32(root+8); nodes_ptr=r.ptr(root+0xc); tracks_ptr=r.ptr(root+0x10); nodes=[]
                if nodes_ptr is not None:
                    for i in range(0x1000):
                        value=struct.unpack_from('>b',r.data,nodes_ptr+i)[0]
                        if value==-1: break
                        nodes.append(value)
                tracks=[]; count=sum(nodes)
                if tracks_ptr is not None:
                    for i in range(count):
                        p=tracks_ptr+i*12
                        if p+12>r.info.data_size: break
                        tracks.append(FigaTrack(*struct.unpack_from('>HHBBB x I',r.data,p)))
                result.append(FigaClip(symbol or f'clip_{len(result)}',frames,tuple(nodes),tuple(tracks),off,fs))
        except Exception:
            pass
        off=(off+fs+31)&~31
    return tuple(result)

def _frac(raw,frac):
    if frac==0:return 0.0
    denom=1<<(frac&0x1f); kind=frac&0xe0
    if kind==0:return 0.0
    if kind==0x20: value=struct.unpack_from('<h',raw,0)[0]
    elif kind==0x40:value=struct.unpack_from('<H',raw,0)[0]
    elif kind==0x60:value=struct.unpack_from('<b',raw,0)[0]
    elif kind==0x80:value=raw[0]
    else:return 0.0
    return value/denom

def _read_float(data,pos,frac):
    if frac==0: return struct.unpack_from('<f',data,pos)[0],pos+4
    size=2 if (frac&0xe0) in (0x20,0x40) else 1
    return _frac(data[pos:pos+size],frac),pos+size

def _varint(data,pos):
    value=0; shift=0
    while pos<len(data):
        b=data[pos]; pos+=1; value|=(b&0x7f)<<shift
        if not b&0x80:return value,pos
        shift+=7
    return value,pos

def sample_track(reader,track,frame):
    if track.ad_offset is None or track.ad_offset+track.length>len(reader.data): return 0.0
    data=reader.data[track.ad_offset:track.ad_offset+track.length]; pos=0; cursor=float(track.start_frame); previous=0.0; last=0.0
    while pos<len(data):
        header=data[pos]; pos+=1; op=header&0xf; count=((header>>4)&7)+1
        if header&0x80:
            extra, pos=_varint(data,pos); count+=extra<<3
        for _ in range(count):
            p0=previous; d0=0.0
            if op in (1,2,3,4,6):
                value,pos=_read_float(data,pos,track.frac_value); p1=value
            else:p1=previous
            if op in (4,5): d0,pos=_read_float(data,pos,track.frac_slope)
            wait,pos=_varint(data,pos); end=cursor+wait
            if frame<=end:
                alpha=0.0 if wait==0 else max(0.0,min(1.0,(frame-cursor)/wait))
                if op==1:return p1 if frame>=end else p0
                if op==2:return p0+(p1-p0)*alpha
                if op in (3,4,5):return p0+(p1-p0)*alpha
                if op==6:return p1 if frame>=cursor else p0
            cursor=end; previous=p1; last=p1
    return last

def clip_transforms(path,clip,frame):
    reader=_Reader(Path(path).read_bytes(),clip.archive_offset); values=[]; index=0
    for node_count in clip.nodes:
        channels=[]
        for _ in range(node_count):
            if index>=len(clip.tracks):break
            track=clip.tracks[index]; channels.append((track.obj_type,sample_track(reader,track,frame))); index+=1
        values.append(tuple(channels))
    return tuple(values)


def _varint_encode(value):
    value=int(value); out=bytearray()
    while True:
        byte=value&0x7f; value >>= 7
        if value: out.append(byte|0x80)
        else: out.append(byte); return bytes(out)


def _pack_track_value(value,frac):
    if frac==0:return struct.pack("<f",float(value))
    kind=frac&0xe0; scaled=round(float(value)*(1<<(frac&0x1f)))
    if kind==0x20:
        if not -32768<=scaled<=32767: raise ValueError("animation s16 value is out of range")
        return struct.pack("<h",scaled)
    if kind==0x40:
        if not 0<=scaled<=65535: raise ValueError("animation u16 value is out of range")
        return struct.pack("<H",scaled)
    if kind==0x60:
        if not -128<=scaled<=127: raise ValueError("animation s8 value is out of range")
        return struct.pack("<b",scaled)
    if kind==0x80:
        if not 0<=scaled<=255: raise ValueError("animation u8 value is out of range")
        return struct.pack("<B",scaled)
    raise ValueError(f"unsupported animation fractional format: {frac:#x}")


def _encode_baked_track(values,frac):
    if not values: return b""
    out=bytearray((0x01,)); out.extend(_pack_track_value(values[0],frac)); out.extend(_varint_encode(0))
    for value in values[1:]:
        out.append(0x02); out.extend(_pack_track_value(value,frac)); out.extend(_varint_encode(1))
    return bytes(out)


def export_hsd_animation(source,output,clip_index,edits):
    """Bake local translation pose edits into one FigaTree clip.

    The original archive's relocation/public/external tables are retained. A
    new fixed-rate stream is appended for each affected position track, and
    only its existing track descriptor is changed. Rotation, scale, topology,
    and new-joint edits are intentionally rejected.
    """
    source=Path(source).expanduser().resolve(); output=Path(output).expanduser().resolve(); raw=source.read_bytes(); clips=scan_figatree(source)
    if not clips: raise ValueError("no FigaTree clips found")
    clip_index=int(clip_index)
    if not 0<=clip_index<len(clips): raise ValueError("animation clip is out of range")
    clip=clips[clip_index]; segment=bytearray(raw[clip.archive_offset:clip.archive_offset+clip.archive_size]); reader=_Reader(bytes(segment),0); body=bytearray(reader.data); clip_edits={}
    for edit in edits or []:
        if not isinstance(edit,dict): raise ValueError("animation edit must be an object")
        if int(edit.get("clip",clip_index))!=clip_index: continue
        joint=int(edit.get("joint",-1)); frame=int(edit.get("frame",-1)); delta=edit.get("position",(0,0,0))
        if not 0<=joint<len(clip.nodes) or frame<0 or frame>int(clip.frames): raise ValueError("animation edit joint or frame is out of range")
        if not isinstance(delta,(list,tuple)) or len(delta)!=3: raise ValueError("animation position delta requires three values")
        entry=clip_edits.setdefault((joint,frame),{"position":[0.0,0.0,0.0],"rotation":[0.0,0.0,0.0],"scale":[0.0,0.0,0.0]})
        for key in ("position","rotation","scale"):
            values=edit.get(key,(0,0,0))
            if not isinstance(values,(list,tuple)) or len(values)!=3: raise ValueError(f"animation {key} delta requires three values")
            entry[key]=[float(x) for x in values]
    if not clip_edits: output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(raw); return output,0
    nodes=[]
    for joint,count in enumerate(clip.nodes): nodes.extend([joint]*count)
    frame_count=max(1,int(round(clip.frames)))+1; affected=0; track_index=0; new_tracks=[]
    for joint,count in enumerate(clip.nodes):
        for channel in range(count):
            track=clip.tracks[track_index] if track_index<len(clip.tracks) else None; descriptor=clip.archive_offset
            if track is None: track_index+=1; continue
            if track.obj_type in (1,2,3): edit_key="rotation"; axis=track.obj_type-1
            elif track.obj_type in (5,6,7): edit_key="position"; axis=track.obj_type-5
            elif track.obj_type in (8,9,10): edit_key="scale"; axis=track.obj_type-8
            else: edit_key=None; axis=-1
            deltas=[clip_edits.get((joint,frame),{}).get(edit_key,(0.0,0.0,0.0)) if edit_key else (0.0,0.0,0.0) for frame in range(frame_count)]
            if axis>=0 and any(abs(delta[axis])>1e-12 for delta in deltas):
                values=[sample_track(reader,track,float(frame))+deltas[frame][axis] for frame in range(frame_count)]; encoded=_encode_baked_track(values,track.frac_value)
                if len(encoded)>0xffff: raise ValueError("baked FigaTree track exceeds HSD track length")
                while len(body)%4: body.append(0)
                stream_offset=len(body); body.extend(encoded); descriptor_offset=clip.tracks[track_index].ad_offset
                # Track descriptor location is the public root's track table plus index.
                root=next(iter(extract_hsd_publics(segment).values())); tracks_ptr=struct.unpack_from(">I",body,root+0x10)[0]; track_pos=tracks_ptr+track_index*12; struct.pack_into(">H",body,track_pos,len(encoded)); struct.pack_into(">I",body,track_pos+8,stream_offset); affected+=1
            track_index+=1
    if not affected: output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(raw); return output,0
    reloc,publics,externs=extract_hsd_tables(segment); rebuilt=build_hsd_archive(body,relocations=reloc,publics=publics,externs=externs,version=bytes(segment[0x14:0x18])); old_next=(clip.archive_offset+clip.archive_size+31)&~31; new_end=clip.archive_offset+len(rebuilt); new_next=(new_end+31)&~31; result=raw[:clip.archive_offset]+rebuilt+b"\0"*(new_next-new_end)+raw[old_next:]; output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(result); return output,affected
