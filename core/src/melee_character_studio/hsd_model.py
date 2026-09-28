from __future__ import annotations
import struct, math
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from .hsd_archive import validate_hsd_archive, extract_hsd_publics
from .hsd_texture import decode_hsd_texture
from .hsd_animation import scan_figatree

@dataclass(frozen=True)
class HsdJoint:
    index:int; name:str; flags:int; rotation:tuple[float,float,float]; scale:tuple[float,float,float]; position:tuple[float,float,float]; parent:int|None; children:tuple[int,...]; envelope_matrix:tuple[float,...]=()
@dataclass(frozen=True)
class HsdModelReport:
    path:Path; root_symbol:str; root_offset:int; joints:tuple[HsdJoint,...]; diagnostics:tuple[str,...]=(); joint_offsets:tuple[int,...]=()

class HsdReader:
    def __init__(self,path):
        self.path=Path(path); self.raw=self.path.read_bytes(); self.info=validate_hsd_archive(self.raw); self.base=0x20; self.data=memoryview(self.raw)[self.base:self.base+self.info.data_size]; self.publics=extract_hsd_publics(self.raw)
    def u32(self,off): return struct.unpack_from('>I',self.data,off)[0]
    def f32(self,off): return struct.unpack_from('>f',self.data,off)[0]
    def ptr(self,off):
        value=self.u32(off)
        return None if value==0 else (value if value<self.info.data_size else None)
    def vec3(self,off): return tuple(self.f32(off+i*4) for i in range(3))
    def cstring(self,off):
        if not off or off>=self.info.data_size:return ''
        end=bytes(self.data[off:]).find(b'\0'); end=len(self.data)-off if end<0 else end
        return bytes(self.data[off:off+end]).decode('ascii','replace')
    def root_for_symbol(self,symbol):
        root=self.publics.get(symbol)
        if root is None: raise ValueError(f'public symbol not found: {symbol}')
        if symbol.startswith('ftData'):
            if root+0x60>self.info.data_size: raise ValueError('ftData root is truncated')
            joint=self.ptr(root+0x5c)
            if joint is None: raise ValueError('ftData has no model root joint')
        else:
            joint=root
        return root,joint
    def joints(self,symbol=None):
        if symbol is None: symbol=next((x for x in self.publics if x.startswith('ftData')),None)
        if symbol is None: raise ValueError('no ftData public symbol found')
        root,first=self.root_for_symbol(symbol); output=[]; offsets=[]; seen=set()
        def walk(off,parent):
            if off in seen:return
            seen.add(off); index=len(output); seen.add(off)
            # reserve so child references can be patched after recursion
            output.append([index,'',0,(0,0,0),(1,1,1),(0,0,0),parent,[],()]); offsets.append(off)
            flags=self.u32(off+4); child=self.ptr(off+8); nxt=self.ptr(off+12); name=self.cstring(self.ptr(off+0) or 0)
            output[index][1]=name or f'joint_{index}'; output[index][2]=flags; output[index][3]=self.vec3(off+0x14); output[index][4]=self.vec3(off+0x20); output[index][5]=self.vec3(off+0x2c); bind=self.ptr(off+0x38); output[index][8]=tuple(self.f32(bind+r*16+c*4) for r in range(3) for c in range(4)) if bind is not None and bind+48<=self.info.data_size else ()
            c=child
            while c is not None and c not in seen:
                child_index=len(output); output[index][7].append(child_index); walk(c,index); c=self.ptr(c+12)
        walk(first,None)
        joints=tuple(HsdJoint(x[0],x[1],x[2],x[3],x[4],x[5],x[6],tuple(x[7]),tuple(x[8])) for x in output)
        return HsdModelReport(self.path,symbol,root,joints,(),tuple(offsets))

def inspect_hsd_model(path,symbol=None): return HsdReader(path).joints(symbol)


GX_QUADS=0x80; GX_TRIANGLES=0x90; GX_TRIANGLESTRIP=0x98; GX_TRIANGLEFAN=0xA0
GX_LINES=0xA8; GX_LINESTRIP=0xB0; GX_POINTS=0xB8
GX_DIRECT=1; GX_INDEX8=2; GX_INDEX16=3; GX_VA_POS=9; GX_VA_NRM=10; GX_VA_TEX0=13; GX_VA_NULL=0xFF

def _scalar(data, off, comp, frac):
    if comp==0: return data[off]/float(1<<frac)
    if comp==1: return struct.unpack_from('>b',data,off)[0]/float(1<<frac)
    if comp==2: return struct.unpack_from('>H',data,off)[0]/float(1<<frac)
    if comp==3: return struct.unpack_from('>h',data,off)[0]/float(1<<frac)
    if comp==4: return struct.unpack_from('>f',data,off)[0]
    raise ValueError(f'unsupported GX component type {comp}')

def _component_width(comp): return {0:1,1:1,2:2,3:2,4:4}.get(comp,1)

def _read_desc(reader,off):
    entries=[]
    while off is not None and off+24<=reader.info.data_size:
        attr=reader.u32(off)
        if attr==GX_VA_NULL: break
        entries.append({'attr':attr,'type':reader.u32(off+4),'count':reader.u32(off+8),'comp':reader.u32(off+12),'frac':reader.data[off+16],'stride':struct.unpack_from('>H',reader.data,off+18)[0],'vertex':reader.ptr(off+20)})
        off+=24
    return entries

def _read_attr(reader,d,pos):
    typ=d['type']; attr=d['attr']
    if typ==GX_DIRECT:
        if attr<=8:return reader.data[pos],pos+1
        n=(4 if d['count']==1 else 3) if attr in (11,12) else (2 if attr>=GX_VA_TEX0 else 3); width=_component_width(d['comp']); return tuple(_scalar(reader.data,pos+i*width,d['comp'],d['frac']) for i in range(n)),pos+n*width
    if typ in (GX_INDEX8,GX_INDEX16):
        width=1 if typ==GX_INDEX8 else 2; index=reader.data[pos] if width==1 else struct.unpack_from('>H',reader.data,pos)[0]; pos+=width
        if d['vertex'] is None:return 0,pos
        vertex_reader=d.get('_vertex_reader',reader); base=d['vertex']+index*d['stride']; n=(4 if d['count']==1 else 3) if attr in (11,12) else (2 if attr>=GX_VA_TEX0 else 3); sw=_component_width(d['comp']); return tuple(_scalar(vertex_reader.data,base+i*sw,d['comp'],d['frac']) for i in range(n)),pos
    return 0,pos

def _triangulate(kind,points):
    if kind==GX_TRIANGLES:return [(points[i],points[i+1],points[i+2]) for i in range(0,len(points)-2,3)]
    if kind==GX_QUADS:return [(points[i],points[i+1],points[i+2]) for i in range(0,len(points)-3,4)]+[(points[i],points[i+2],points[i+3]) for i in range(0,len(points)-3,4)]
    if kind==GX_TRIANGLESTRIP:return [(points[i],points[i+1],points[i+2]) if i%2==0 else (points[i+1],points[i],points[i+2]) for i in range(len(points)-2)]
    if kind==GX_TRIANGLEFAN:return [(points[0],points[i],points[i+1]) for i in range(1,len(points)-1)]
    return []

def _decode_pobj(reader,pobj,external_position_reader=None,external_position_vertex=None):
    desc=_read_desc(reader,reader.ptr(pobj+8))
    if external_position_reader is not None:
        desc=[dict(d,**({'vertex':external_position_vertex,'_vertex_reader':external_position_reader} if d['attr']==GX_VA_POS and d['vertex'] is None and external_position_vertex is not None else {})) for d in desc]
    display=reader.ptr(pobj+0x10); blocks=struct.unpack_from('>H',reader.data,pobj+0x0e)[0]
    if display is None or not desc:return []
    end=min(reader.info.data_size,display+blocks*32); cur=display; triangles=[]
    while cur+3<=end:
        kind=reader.data[cur]
        if kind==0:break
        count=struct.unpack_from('>H',reader.data,cur+1)[0]; cur+=3
        if kind not in (GX_QUADS,GX_TRIANGLES,GX_TRIANGLESTRIP,GX_TRIANGLEFAN,GX_LINES,GX_LINESTRIP,GX_POINTS) or count>10000:break
        points=[]
        for _ in range(count):
            position=None; position_index=None; matrix=0; uv=None; uv_index=None; normal=None; normal_index=None
            for d in desc:
                attr_start=cur; value,cur=_read_attr(reader,d,cur)
                if d['attr']==GX_VA_POS and isinstance(value,tuple):
                    position= value
                    if d['type'] in (GX_INDEX8,GX_INDEX16): position_index=reader.data[attr_start] if d['type']==GX_INDEX8 else struct.unpack_from('>H',reader.data,attr_start)[0]
                elif d['attr']==0 and isinstance(value,int):matrix=value
                elif d['attr']==GX_VA_NRM and isinstance(value,tuple):
                    normal=tuple(float(v) for v in value[:3]); normal_index=reader.data[attr_start] if d['type']==GX_INDEX8 else (struct.unpack_from('>H',reader.data,attr_start)[0] if d['type']==GX_INDEX16 else None)
                elif d['attr']>=GX_VA_TEX0 and isinstance(value,tuple) and uv is None:
                    uv=tuple(float(v) for v in value[:2]); uv_index=reader.data[attr_start] if d['type']==GX_INDEX8 else (struct.unpack_from('>H',reader.data,attr_start)[0] if d['type']==GX_INDEX16 else None)
            if position is not None:points.append((tuple(float(v) for v in position),matrix,uv,normal,position_index,uv_index,normal_index))
            if cur>end:break
        triangles.extend(_triangulate(kind,points))
    return triangles

def decode_hsd_geometry(path,symbol=None,texture_source=None):
    model_reader=HsdReader(path); reader=model_reader; texture_reader=None
    if texture_source:
        texture_reader=HsdReader(texture_source); reader=texture_reader
        if symbol is None or symbol.startswith('ftData'): symbol=next((name for name in texture_reader.publics if name.endswith('_joint') and not name.endswith('_matanim_joint')),None)
    report=reader.joints(symbol); geometry=[]; skipped_pobjs=[]; seen_dobj=set(); joint_index_by_offset={off:i for i,off in enumerate(report.joint_offsets)}
    external_position_reader=None; external_position_vertex=None
    if texture_reader is not None:
        external_position_reader=model_reader
        base_report=model_reader.joints(None if symbol is None or symbol.startswith('ftData') else None)
        for base_off in base_report.joint_offsets:
            bd=model_reader.ptr(base_off+0x10)
            while bd is not None:
                bp=model_reader.ptr(bd+0xc)
                while bp is not None:
                    for descriptor in _read_desc(model_reader,model_reader.ptr(bp+8)):
                        if descriptor['attr']==GX_VA_POS and descriptor['vertex'] is not None: external_position_vertex=descriptor['vertex']; break
                    if external_position_vertex is not None:break
                    bp=model_reader.ptr(bp+4)
                if external_position_vertex is not None:break
                bd=model_reader.ptr(bd+4)
            if external_position_vertex is not None:break
    texture_cache={}
    local=[{'position':list(j.position),'rotation':list(j.rotation),'scale':list(j.scale)} for j in report.joints]; world_matrices,_=_hsd_world_matrices(report.joints,local)
    def part_right(owner):
        owner_joint=report.joints[owner]
        if owner_joint.flags & 2:return None
        skeleton=owner
        while skeleton is not None and not (report.joints[skeleton].flags & 3): skeleton=report.joints[skeleton].parent
        if skeleton is None:return None
        sj=report.joints[skeleton]
        if skeleton==owner:return _inverse_affine4(tuple(sj.envelope_matrix)+(0,0,0,1)) if sj.envelope_matrix else _inverse_affine4(world_matrices[skeleton])
        if sj.flags & 2:return _matmul4(_inverse_affine4(world_matrices[skeleton]),world_matrices[owner])
        envelope=tuple(sj.envelope_matrix)+(0,0,0,1) if sj.envelope_matrix else _inverse_affine4(world_matrices[skeleton]); return _matmul4(_inverse_affine4(_matmul4(world_matrices[skeleton],envelope)),world_matrices[owner])
    def skin_point(point,matrix,envelope_table):
        if envelope_table is None: return point
        entry=reader.ptr(envelope_table+int(matrix)*4)
        if entry is None:return point
        result=[0.0,0.0,0.0]; total=0.0; cursor=entry
        for _ in range(32):
            joint_ptr=reader.ptr(cursor); weight=reader.f32(cursor+4)
            if joint_ptr is None or weight==0: break
            ji=joint_index_by_offset.get(joint_ptr)
            if ji is not None:
                for axis in range(3): result[axis]+=weight*(point[axis]+world[ji][axis])
                total+=weight
            cursor+=8
        return tuple(result[axis]/total for axis in range(3)) if total else point
    for joint_index,joint_off in enumerate(report.joint_offsets):
        dobj=reader.ptr(joint_off+0x10)
        while dobj is not None and dobj not in seen_dobj:
            seen_dobj.add(dobj); pobj=reader.ptr(dobj+0x0c)
            while pobj is not None:
                try: tris=_decode_pobj(reader,pobj,external_position_reader,external_position_vertex)
                except (struct.error,IndexError,ValueError):
                    # Auxiliary costume PObjs can rely on game-side GX stream
                    # binding and are not independently decodable.
                    skipped_pobjs.append(pobj); tris=[]
                if tris:
                    flags=struct.unpack_from('>H',reader.data,pobj+0x0c)[0]; envelope=reader.ptr(pobj+0x14) if flags&0x3000==0x2000 else None
                    vertices=[]; indices=[]; skins=[]; uvs=[]; normals=[]; has_normals=False
                    for tri in tris:
                        start=len(vertices)
                        for point in tri:
                            vertices.append(point[0]); matrix=int(point[1]); uvs.append(point[2] if len(point)>2 and point[2] is not None else (0.0,0.0)); normal=point[3] if len(point)>3 else None; has_normals=has_normals or normal is not None; normals.append(normal if normal is not None else (0.0,0.0,1.0)); weights=[]
                            if envelope is not None:
                                entry=reader.ptr(envelope+matrix*4) if envelope+matrix*4+4<=reader.info.data_size else None
                                cursor=entry
                                for _ in range(32):
                                    jp=reader.ptr(cursor) if cursor is not None else None
                                    if jp is None: break
                                    weight=reader.f32(cursor+4); ji=joint_index_by_offset.get(jp)
                                    if ji is not None and weight: weights.append((ji,weight))
                                    cursor+=8
                            if not weights: weights=[(joint_index,1.0)]
                            skins.append(tuple(weights))
                        indices.extend((start,start+1,start+2))
                    material={'diffuse':(0.72,0.72,0.72,1.0),'ambient':(0.2,0.2,0.2,1.0),'specular':(1.0,1.0,1.0,1.0),'alpha':1.0}
                    mobj=reader.ptr(dobj+8)
                    if mobj is not None:
                        mat=reader.ptr(mobj+0xc)
                        if mat is not None and mat+16<=reader.info.data_size:
                            diffuse=tuple(reader.data[mat+i]/255.0 for i in range(4)); ambient=tuple(reader.data[mat+4+i]/255.0 for i in range(4)); specular=tuple(reader.data[mat+8+i]/255.0 for i in range(4)); material={'diffuse':diffuse,'ambient':ambient,'specular':specular,'alpha':reader.f32(mat+12)}
                        texdesc=reader.ptr(mobj+8)
                        if texdesc is not None:
                            image_slot=texdesc+0x4c
                            if image_slot not in texture_cache: texture_cache[image_slot]=decode_hsd_texture(reader,image_slot)
                            if texture_cache[image_slot] is not None:
                                material['texture']=dict(texture_cache[image_slot]); material['texture']['mapped']=any(uv!=(0.0,0.0) for uv in uvs); material['texture']['wrap_s']=reader.u32(texdesc+0x34) if texdesc+0x38<=reader.info.data_size else 1; material['texture']['wrap_t']=reader.u32(texdesc+0x38) if texdesc+0x3c<=reader.info.data_size else 1; material['texture']['repeat_s']=reader.data[texdesc+0x3c] if texdesc+0x3d<=reader.info.data_size else 1; material['texture']['repeat_t']=reader.data[texdesc+0x3d] if texdesc+0x3e<=reader.info.data_size else 1; material['texture']['note']='HSD image, TEX0 stream, and TObj wrapping decoded; TEV composition may still be incomplete'
                    part={'vertices':vertices,'indices':indices,'skin':skins,'uvs':uvs,'joint':joint_index,'right_matrix':part_right(joint_index),'source_offset':pobj,'material':material}
                    position_desc=next((descriptor for descriptor in _read_desc(reader,reader.ptr(pobj+8)) if descriptor['attr']==GX_VA_POS),None)
                    if position_desc is not None:
                        part['source_position_stream']=external_position_vertex if position_desc.get('vertex') is None and external_position_vertex is not None else position_desc.get('vertex'); part['source_position_archive']='model' if external_position_vertex is not None else ('texture' if texture_reader is not None else 'model'); part['source_position_stride']=position_desc.get('stride'); part['source_position_frac']=position_desc.get('frac'); part['source_position_type']={0:'u8',1:'s8',2:'u16',3:'s16',4:'f32'}.get(position_desc.get('comp'),'unknown'); part['source_position_indices']=[point[4] for tri in tris for point in tri]; part['source_position_values']=[point[0] for tri in tris for point in tri]
                    uv_desc=next((descriptor for descriptor in _read_desc(reader,reader.ptr(pobj+8)) if descriptor['attr']>=GX_VA_TEX0),None); normal_desc=next((descriptor for descriptor in _read_desc(reader,reader.ptr(pobj+8)) if descriptor['attr']==GX_VA_NRM),None)
                    if uv_desc is not None:
                        part['source_uv_stream']=external_position_vertex if uv_desc.get('vertex') is None and external_position_vertex is not None else uv_desc.get('vertex'); part['source_uv_archive']='texture' if texture_reader is not None else 'model'; part['source_uv_stride']=uv_desc.get('stride'); part['source_uv_frac']=uv_desc.get('frac'); part['source_uv_type']={0:'u8',1:'s8',2:'u16',3:'s16',4:'f32'}.get(uv_desc.get('comp'),'unknown'); part['source_uv_indices']=[point[5] for tri in tris for point in tri]; part['source_uv_values']=[point[2] if point[2] is not None else (0.0,0.0) for tri in tris for point in tri]
                    if normal_desc is not None:
                        part['source_normal_stream']=normal_desc.get('vertex'); part['source_normal_archive']='texture' if texture_reader is not None else 'model'; part['source_normal_stride']=normal_desc.get('stride'); part['source_normal_frac']=normal_desc.get('frac'); part['source_normal_type']={0:'u8',1:'s8',2:'u16',3:'s16',4:'f32'}.get(normal_desc.get('comp'),'unknown'); part['source_normal_indices']=[point[6] for tri in tris for point in tri]; part['source_normal_values']=[point[3] if point[3] is not None else (0.0,0.0,1.0) for tri in tris for point in tri]
                    if has_normals: part['normals']=normals
                    part['base_uvs']=list(uvs); part['base_normals']=list(normals)
                    geometry.append(part)
                pobj=reader.ptr(pobj+4)
            dobj=reader.ptr(dobj+4)
    return report,geometry

@lru_cache(maxsize=8)
def _cached_figatree_clips(path_string):
    return tuple(scan_figatree(Path(path_string)))

@lru_cache(maxsize=4)
def _cached_hsd_geometry(path_string,symbol,texture_string):
    return decode_hsd_geometry(Path(path_string),symbol,Path(texture_string) if texture_string else None)

def _scene_geometry(path,symbol=None,texture_source=None):
    report,geometry=_cached_hsd_geometry(str(Path(path).expanduser().resolve()),symbol,str(Path(texture_source).expanduser().resolve()) if texture_source else None)
    # Skinning replaces only each part's vertex list. Shallow-copy the part
    # records and vertex arrays so animation frames avoid a full archive-sized
    # deepcopy while retaining cache immutability.
    return report,[dict(part,vertices=list(part.get("vertices",[])),base_vertices=list(part.get("base_vertices",[])),uvs=list(part.get("uvs",[])),normals=list(part.get("normals",[])),skin=list(part.get("skin",[])),source_position_indices=list(part.get("source_position_indices",[])),source_position_values=list(part.get("source_position_values",[])),base_uvs=list(part.get("base_uvs",[])),base_normals=list(part.get("base_normals",[])),source_uv_indices=list(part.get("source_uv_indices",[])),source_uv_values=list(part.get("source_uv_values",[])),source_normal_indices=list(part.get("source_normal_indices",[])),source_normal_values=list(part.get("source_normal_values",[]))) for part in geometry]

def hsd_scene(path,symbol=None,texture_source=None):
    report,geometry=_scene_geometry(path,symbol,texture_source); world=[]
    for joint in report.joints:
        world.append(joint.position if joint.parent is None else tuple(world[joint.parent][j]+joint.position[j] for j in range(3)))
    local=[{'position':list(j.position),'rotation':list(j.rotation),'scale':list(j.scale)} for j in report.joints]; rest,_=_hsd_world_matrices(report.joints,local); geometry=_apply_skin(report,geometry,_envelope_skin_matrices(report,rest))
    world=[(m[3],m[7],m[11]) for m in rest]
    return {'model':str(report.path.resolve()),'meshes':len(geometry),'joints':[{'index':j.index,'name':j.name,'position':world[j.index],'local_position':j.position,'parent':j.parent} for j in report.joints],'geometry':geometry,'duration':0.0,'hsd':True,'root_symbol':report.root_symbol,'diagnostics':list(report.diagnostics)}



def _rotate_point(v,r):
    x,y,z=v; rx,ry,rz=r; cy,sy=math.cos(ry),math.sin(ry); x,z=x*cy+z*sy,-x*sy+z*cy; cx,sx=math.cos(rx),math.sin(rx); y,z=y*cx-z*sx,y*sx+z*cx; cz,sz=math.cos(rz),math.sin(rz); return (x*cz-y*sz,x*sz+y*cz,z)

def _matmul4(a,b): return tuple(sum(a[r*4+k]*b[k*4+c] for k in range(4)) for r in range(4) for c in range(4))
def _matvec4(m,p): return (m[0]*p[0]+m[1]*p[1]+m[2]*p[2]+m[3],m[4]*p[0]+m[5]*p[1]+m[6]*p[2]+m[7],m[8]*p[0]+m[9]*p[1]+m[10]*p[2]+m[11])
def _hsd_local_matrix(scale,rotation,position,parent_scale=None):
    sx,sy,sz=scale; rx,ry,rz=rotation; sinx,cosx=math.sin(rx),math.cos(rx); siny,cosy=math.sin(ry),math.cos(ry); sinz,cosz=math.sin(rz),math.cos(rz)
    x2=x1=x=sx; y2=y1=y=sy; z2=z1=z=sz
    if parent_scale is not None:
        px,py,pz=parent_scale; x2*=py/px if abs(px)>1e-8 else 1; z2*=pz/px if abs(px)>1e-8 else 1; x1*=px/py if abs(py)>1e-8 else 1; z1*=pz/py if abs(py)>1e-8 else 1; x*=px/pz if abs(pz)>1e-8 else 1; y*=py/pz if abs(pz)>1e-8 else 1
    return (cosz*(x2*cosy), y2*(cosz*(sinx*siny)-cosx*sinz), z2*(cosz*(cosx*siny)+sinx*sinz), position[0], sinz*(x1*cosy), y1*(sinz*(sinx*siny)+cosx*cosz), z1*(sinz*(cosx*siny)-sinx*cosz), position[1], -x*siny, cosy*(y*sinx), cosy*(z*cosx), position[2], 0,0,0,1)
def _hsd_world_matrices(joints,local):
    matrices=[]; scales=[]
    for i,joint in enumerate(joints):
        parent=joint.parent; parent_scale=scales[parent] if parent is not None else None; local_matrix=_hsd_local_matrix(local[i]['scale'],local[i]['rotation'],local[i]['position'],parent_scale); matrices.append(_matmul4(matrices[parent],local_matrix) if parent is not None else local_matrix)
        scales.append(parent_scale if (joint.flags&8 and parent_scale is not None) else tuple(local[i]['scale'][k]*(parent_scale[k] if parent_scale else 1.0) for k in range(3)))
    return matrices,scales
def _world_transforms(joints,local):
    matrices,scales=_hsd_world_matrices(joints,local)
    return [((m[3],m[7],m[11]),tuple(local[i]['rotation']),tuple(scales[i])) for i,m in enumerate(matrices)]

def _inverse_affine4(m):
    a,b,c,d,e,f,g,h,i,j,k,l=m[:12]; det=a*(f*k-g*j)-b*(e*k-g*i)+c*(e*j-f*i)
    if abs(det)<1e-8:return (1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)
    q=1.0/det; r0=(f*k-g*j)*q; r1=(c*j-b*k)*q; r2=(b*g-c*f)*q; r4=(g*i-e*k)*q; r5=(a*k-c*i)*q; r6=(c*e-a*g)*q; r8=(e*j-f*i)*q; r9=(b*i-a*j)*q; r10=(a*f-b*e)*q
    return (r0,r1,r2,-(r0*d+r1*h+r2*l),r4,r5,r6,-(r4*d+r5*h+r6*l),r8,r9,r10,-(r8*d+r9*h+r10*l),0,0,0,1)

def _envelope_skin_matrices(report,world):
    return [_matmul4(world[i],tuple(j.envelope_matrix)+(0,0,0,1) if j.envelope_matrix else _inverse_affine4(world[i])) for i,j in enumerate(report.joints)]

def _apply_skin(report,geometry,matrices):
    for part in geometry:
        animated=[]
        for point,weights in zip(part['vertices'],part.get('skin',())):
            point=_matvec4(part['right_matrix'],point) if part.get('right_matrix') else point; value=[0.0,0.0,0.0]; total=0.0
            for ji,weight in weights:
                moved=_matvec4(matrices[ji],point)
                for axis in range(3):value[axis]+=moved[axis]*weight
                total+=weight
            animated.append(tuple(x/total for x in value) if total else point)
        if animated:
            part['base_vertices']=list(animated)
            part['vertices']=animated
    return geometry

def _inverse_rotate_point(v,r):
    x,y,z=v; rx,ry,rz=r; cz,sz=math.cos(-rz),math.sin(-rz); x,y=x*cz-y*sz,x*sz+y*cz; cx,sx=math.cos(-rx),math.sin(-rx); y,z=y*cx-z*sx,y*sx+z*cx; cy,sy=math.cos(-ry),math.sin(-ry); return (x*cy+z*sy,y,-x*sy+z*cy)

def _inverse_transform(point,transform):
    pos,rot,scale=transform; v=tuple(point[k]-pos[k] for k in range(3)); v=_inverse_rotate_point(v,rot); return tuple(v[k]/scale[k] if abs(scale[k])>1e-6 else v[k] for k in range(3))

def _forward_transform(point,transform):
    pos,rot,scale=transform; v=tuple(point[k]*scale[k] for k in range(3)); v=_rotate_point(v,rot); return tuple(v[k]+pos[k] for k in range(3))

def hsd_animation_scene(model_path, animation_path, clip_index=0, frame=0.0, texture_source=None, joint_edits=None):
    from .hsd_animation import scan_figatree, clip_transforms
    report,geometry=_scene_geometry(model_path,None,texture_source); clips=_cached_figatree_clips(str(Path(animation_path).expanduser().resolve()))
    if not clips: raise ValueError("no FigaTree animation clips found")
    clip=clips[int(clip_index)%len(clips)]; channels=clip_transforms(animation_path,clip,float(frame))
    local=[{'position':list(j.position),'rotation':list(j.rotation),'scale':list(j.scale)} for j in report.joints]
    for i,items in enumerate(channels):
        if i>=len(local): break
        for obj_type,value in items:
            if obj_type==1: local[i]['rotation'][0]=value
            elif obj_type==2: local[i]['rotation'][1]=value
            elif obj_type==3: local[i]['rotation'][2]=value
            elif obj_type==5: local[i]['position'][0]=value
            elif obj_type==6: local[i]['position'][1]=value
            elif obj_type==7: local[i]['position'][2]=value
            elif obj_type==8: local[i]['scale'][0]=value
            elif obj_type==9: local[i]['scale'][1]=value
            elif obj_type==10: local[i]['scale'][2]=value
    for edit in (joint_edits or []):
        if not isinstance(edit,dict) or int(edit.get("clip",clip_index))!=int(clip_index) or int(edit.get("frame",round(frame)))!=int(round(frame)): continue
        joint=int(edit.get("joint",-1))
        if not 0<=joint<len(local): continue
        for key in ("position","rotation","scale"):
            delta=edit.get(key)
            if isinstance(delta,(list,tuple)) and len(delta)==3:
                local[joint][key]=[float(local[joint][key][axis])+float(delta[axis]) for axis in range(3)]
    rest_local=[{'position':list(j.position),'rotation':list(j.rotation),'scale':list(j.scale)} for j in report.joints]
    rest_matrices,_=_hsd_world_matrices(report.joints,rest_local); anim_matrices,_=_hsd_world_matrices(report.joints,local); geometry=_apply_skin(report,geometry,_envelope_skin_matrices(report,anim_matrices))
    world_pos=[(m[3],m[7],m[11]) for m in anim_matrices]
    return {'model':str(Path(model_path).resolve()),'meshes':len(geometry),'joints':[{'index':j.index,'name':j.name,'position':world_pos[j.index],'local_position':tuple(local[j.index]['position']),'parent':j.parent} for j in report.joints],'geometry':geometry,'duration':clip.frames/60.0,'hsd':True,'root_symbol':report.root_symbol,'animation':clip.name,'animation_frame':float(frame),'animation_clip_index':int(clip_index)}
