from __future__ import annotations
from pathlib import Path
import base64, json, math, struct
from .gltf import _json, validate_model
from .hsd_texture import decode_png_rgba

_COMPONENTS={5120:("b",1),5121:("B",1),5122:("h",2),5123:("H",2),5125:("I",4),5126:("f",4)}
_TYPES={"SCALAR":1,"VEC2":2,"VEC3":3,"VEC4":4,"MAT2":4,"MAT3":9,"MAT4":16}

def _buffers(path,doc):
    out=[]
    glb_bin=None
    if path.suffix.lower()=='.glb':
        raw=path.read_bytes(); pos=12
        while pos+8<=len(raw):
            n,t=struct.unpack_from('<II',raw,pos); chunk=raw[pos+8:pos+8+n]; pos+=8+n
            if t==0x004e4942: glb_bin=chunk
    for i,b in enumerate(doc.get('buffers',[])):
        uri=b.get('uri')
        if uri is None: data=glb_bin or b''
        elif uri.startswith('data:'): data=base64.b64decode(uri.split(',',1)[1])
        else: data=(path.parent/uri).read_bytes()
        out.append(data)
    return out

def _accessor(path,doc,buffers,index):
    a=doc['accessors'][index]; view=doc['bufferViews'][a['bufferView']]; raw=buffers[view['buffer']]
    fmt,size=_COMPONENTS[a['componentType']]; count=a['count']; width=_TYPES[a['type']]; stride=view.get('byteStride',size*width)
    start=view.get('byteOffset',0)+a.get('byteOffset',0); result=[]
    for row in range(count):
        vals=struct.unpack_from('<'+fmt*width,raw,start+row*stride)
        if a.get('normalized'):
            if fmt.islower(): vals=tuple(max(-1.0,x/(2**(size*8-1)-1)) for x in vals)
            else: vals=tuple(x/(2**(size*8)-1) for x in vals)
        result.append(vals[0] if width==1 else tuple(vals))
    return result

def _qmul(a,b):
    return (a[3]*b[0]+a[0]*b[3]+a[1]*b[2]-a[2]*b[1],a[3]*b[1]-a[0]*b[2]+a[1]*b[3]+a[2]*b[0],a[3]*b[2]+a[0]*b[1]-a[1]*b[0]+a[2]*b[3],a[3]*b[3]-a[0]*b[0]-a[1]*b[1]-a[2]*b[2])
def _qvec(q,v):
    x,y,z=v; qv=(x,y,z,0); r=_qmul(_qmul(q,qv),(-q[0],-q[1],-q[2],q[3])); return r[:3]
def _transform(pos,rot,scale): return tuple(_qvec(rot,(pos[0]*scale[0],pos[1]*scale[1],pos[2]*scale[2])))
def _matmul(a,b):
    return tuple(sum(a[r*4+k]*b[k*4+c] for k in range(4)) for r in range(4) for c in range(4))

def _matvec(m,v):
    x,y,z=v; return (m[0]*x+m[1]*y+m[2]*z+m[3],m[4]*x+m[5]*y+m[6]*z+m[7],m[8]*x+m[9]*y+m[10]*z+m[11])

def _quat_mul(a,b):
    ax,ay,az,aw=a; bx,by,bz,bw=b
    return (aw*bx+ax*bw+ay*bz-az*by,aw*by-ax*bz+ay*bw+az*bx,aw*bz+ax*by-ay*bx+az*bw,aw*bw-ax*bx-ay*by-az*bz)

def _quat_normalize(q):
    length=math.sqrt(sum(float(x)*float(x) for x in q)) or 1.0
    return tuple(float(x)/length for x in q)

def _quat_from_euler(euler):
    x,y,z=(float(v)*0.5 for v in euler); cx,sx=math.cos(x),math.sin(x); cy,sy=math.cos(y),math.sin(y); cz,sz=math.cos(z),math.sin(z)
    return _quat_normalize((sx*cy*cz-cx*sy*sz,cx*sy*cz+sx*cy*sz,cx*cy*sz-sx*sy*cz,cx*cy*cz+sx*sy*sz))

def _trs_matrix(t,r,s):
    x,y,z,w=r; return ( (1-2*y*y-2*z*z)*s[0], (2*x*y-2*z*w)*s[1], (2*x*z+2*y*w)*s[2], t[0], (2*x*y+2*z*w)*s[0], (1-2*x*x-2*z*z)*s[1], (2*y*z-2*x*w)*s[2], t[1], (2*x*z-2*y*w)*s[0], (2*y*z+2*x*w)*s[1], (1-2*x*x-2*y*y)*s[2], t[2], 0,0,0,1 )

def _world_matrices(doc,overrides=None):
    nodes=doc.get('nodes',[]); parents={}
    for i,node in enumerate(nodes):
        for child in node.get('children',[]): parents[int(child)]=i
    overrides=overrides or {}; cache={}
    def local(i):
        n=nodes[i]; o=overrides.get(i,{})
        if 'matrix' in n and not o:return _column_major_to_row_major(n['matrix'])
        if 'matrix' in n:
            base=_matrix_trs(_column_major_to_row_major(n['matrix'])); return _trs_matrix(tuple(o.get('translation',base[0])),tuple(o.get('rotation',base[1])),tuple(o.get('scale',base[2])))
        return _trs_matrix(tuple(o.get('translation',n.get('translation',[0,0,0]))),tuple(o.get('rotation',n.get('rotation',[0,0,0,1]))),tuple(o.get('scale',n.get('scale',[1,1,1]))))
    def world(i):
        if i in cache:return cache[i]
        m=local(i); parent=parents.get(i); cache[i]=_matmul(world(parent),m) if parent is not None else m; return cache[i]
    return [world(i) for i in range(len(nodes))]

def _column_major_to_row_major(values):
    return tuple(values[c*4+r] for r in range(4) for c in range(4))

def _matrix_trs(m):
    tx,ty,tz=m[3],m[7],m[11]; sx=math.sqrt(m[0]*m[0]+m[4]*m[4]+m[8]*m[8]) or 1.0; sy=math.sqrt(m[1]*m[1]+m[5]*m[5]+m[9]*m[9]) or 1.0; sz=math.sqrt(m[2]*m[2]+m[6]*m[6]+m[10]*m[10]) or 1.0; r00,r01,r02=m[0]/sx,m[1]/sy,m[2]/sz; r10,r11,r12=m[4]/sx,m[5]/sy,m[6]/sz; r20,r21,r22=m[8]/sx,m[9]/sy,m[10]/sz
    if r20<1.0:
        if r20>-1.0: ry=math.asin(-r20); rx=math.atan2(r21,r22); rz=math.atan2(r10,r00)
        else: ry=math.pi/2; rx=math.atan2(r01,r02); rz=0.0
    else: ry=-math.pi/2; rx=math.atan2(-r01,-r02); rz=0.0
    # Quaternion from the normalized rotation matrix.
    tr=r00+r11+r22
    if tr>0: q=math.sqrt(tr+1.0)*2; quat=((r21-r12)/q,(r02-r20)/q,(r10-r01)/q,0.25*q)
    elif r00>r11 and r00>r22: q=math.sqrt(1+r00-r11-r22)*2; quat=(0.25*q,(r01+r10)/q,(r02+r20)/q,(r21-r12)/q)
    elif r11>r22: q=math.sqrt(1+r11-r00-r22)*2; quat=((r01+r10)/q,0.25*q,(r12+r21)/q,(r02-r20)/q)
    else: q=math.sqrt(1+r22-r00-r11)*2; quat=((r02+r20)/q,(r12+r21)/q,(r21-r12)/q,0.25*q)
    return (tx,ty,tz),quat,(sx,sy,sz)

def _world_nodes(doc, overrides=None):
    nodes=doc.get('nodes',[]); parents={}
    for i,node in enumerate(nodes):
        for child in node.get('children',[]): parents[int(child)]=i
    matrices=_world_matrices(doc,overrides)
    return [(i,str(n.get('name',f'node_{i}')),(m[3],m[7],m[11]),parents.get(i)) for i,(n,m) in enumerate(zip(nodes,matrices))]

def _sample(a,b,alpha,step=False):
    if step:return a
    if isinstance(a,tuple): return tuple(float(a[i])+(float(b[i])-float(a[i]))*alpha for i in range(len(a)))
    return float(a)+(float(b)-float(a))*alpha

def _animation_overrides(path,doc,buffers,clip_index,time,animation_edits=None):
    clips=doc.get('animations',[])
    if not clips or clip_index>=len(clips): return {},0.0
    clip=clips[clip_index]; overrides={}; duration=0.0
    for sampler in clip.get('samplers',[]):
        times=_accessor(path,doc,buffers,sampler['input']); duration=max(duration,float(times[-1]) if times else 0.0)
    t=(time%duration) if duration>0 else 0.0
    for channel in clip.get('channels',[]):
        sampler=clip['samplers'][channel['sampler']]; times=_accessor(path,doc,buffers,sampler['input']); vals=_accessor(path,doc,buffers,sampler['output'])
        if not times: continue
        if t<=times[0]: value=vals[0]
        elif t>=times[-1]: value=vals[-1]
        else:
            hi=next(i for i,x in enumerate(times) if x>=t); lo=hi-1; alpha=(t-times[lo])/(times[hi]-times[lo]); value=_sample(vals[lo],vals[hi],alpha,sampler.get('interpolation')=='STEP')
        target=channel['target']; node=int(target['node']); overrides.setdefault(node,{})[target['path']]=value
    frame=int(round(float(time)*60.0))
    for edit in animation_edits or ():
        if not isinstance(edit,dict) or int(edit.get("clip",clip_index))!=int(clip_index) or int(edit.get("frame",-1))!=frame: continue
        node=int(edit.get("joint",-1)); delta=edit.get("position",(0.0,0.0,0.0))
        if not 0<=node<len(doc.get("nodes",[])) or not isinstance(delta,(list,tuple)) or len(delta)!=3: continue
        node_doc=doc["nodes"][node]; matrix_base=_matrix_trs(_column_major_to_row_major(node_doc["matrix"])) if "matrix" in node_doc else None; base_translation=matrix_base[0] if matrix_base else node_doc.get("translation",[0.0,0.0,0.0]); base_rotation=matrix_base[1] if matrix_base else node_doc.get("rotation",[0.0,0.0,0.0,1.0]); base_scale=matrix_base[2] if matrix_base else node_doc.get("scale",[1.0,1.0,1.0]); current=overrides.setdefault(node,{})
        base=current.get("translation",base_translation); current["translation"]=[float(base[i])+float(delta[i]) for i in range(3)]
        rotation=edit.get("rotation")
        if isinstance(rotation,(list,tuple)) and len(rotation)==3 and any(abs(float(x))>1e-12 for x in rotation):
            base_rotation=current.get("rotation",base_rotation); current["rotation"]=_quat_normalize(_quat_mul(tuple(float(x) for x in base_rotation),_quat_from_euler(rotation)))
        scale=edit.get("scale")
        if isinstance(scale,(list,tuple)) and len(scale)==3 and any(abs(float(x))>1e-12 for x in scale):
            base_scale=current.get("scale",base_scale); current["scale"]=[float(base_scale[i])+float(scale[i]) for i in range(3)]
    return overrides,duration

def animation_durations(model):
    """Return authored glTF animation durations in seconds."""
    path=Path(model); doc,errors=_json(path)
    if errors or doc is None:return []
    buffers=_buffers(path,doc); durations=[]
    for clip in doc.get("animations",[]):
        duration=0.0
        for sampler in clip.get("samplers",[]):
            try:
                times=_accessor(path,doc,buffers,sampler["input"]); duration=max(duration,float(times[-1]) if times else 0.0)
            except (KeyError,IndexError,TypeError,ValueError,struct.error): pass
        durations.append(duration)
    return durations

def _gltf_image(path,doc,buffers,image_index):
    images=doc.get("images",[])
    if image_index is None or not isinstance(image_index,int) or image_index<0 or image_index>=len(images): return None
    image=images[image_index]; raw=None
    uri=image.get("uri")
    try:
        if uri and uri.startswith("data:"): raw=base64.b64decode(uri.split(",",1)[1])
        elif uri: raw=(path.parent/uri).read_bytes()
        elif "bufferView" in image:
            view=doc["bufferViews"][image["bufferView"]]; raw=buffers[view["buffer"]][view.get("byteOffset",0):view.get("byteOffset",0)+view["byteLength"]]
        if raw and (image.get("mimeType","image/png")=="image/png" or bytes(raw[:8])==b"\x89PNG\r\n\x1a\n"): return decode_png_rgba(raw)
    except (OSError,ValueError,KeyError,IndexError,base64.binascii.Error): return None
    return None

def _gltf_material(path,doc,buffers,material_index):
    materials=doc.get("materials",[]); material=materials[material_index] if isinstance(material_index,int) and 0<=material_index<len(materials) else {}; pbr=material.get("pbrMetallicRoughness",{}); factor=tuple(float(x) for x in pbr.get("baseColorFactor",[0.72,0.72,0.72,1.0])); result={"diffuse":factor}
    texture_ref=pbr.get("baseColorTexture",{}).get("index") if isinstance(pbr.get("baseColorTexture",{}),dict) else None; textures=doc.get("textures",[])
    if isinstance(texture_ref,int) and 0<=texture_ref<len(textures):
        texture_info=textures[texture_ref]; image_index=texture_info.get("source"); image=_gltf_image(path,doc,buffers,image_index)
        if image:
            result["texture"]=image; result["texture"]["mapped"]=True; sampler_index=texture_info.get("sampler"); sampler=doc.get("samplers",[])[sampler_index] if isinstance(sampler_index,int) and 0<=sampler_index<len(doc.get("samplers",[])) else {}; result["texture"]["wrap_s"]=0 if sampler.get("wrapS")==33071 else (2 if sampler.get("wrapS")==33648 else 1); result["texture"]["wrap_t"]=0 if sampler.get("wrapT")==33071 else (2 if sampler.get("wrapT")==33648 else 1)
    return result

def preview_scene(model,clip_index=None,time=0.0,animation_edits=None):
    path=Path(model); doc,errors=_json(path)
    if errors or doc is None: raise ValueError('invalid model: '+ '; '.join(x.message for x in errors))
    report=validate_model(path)
    if not report.valid: raise ValueError('model validation failed: '+ '; '.join(x.message for x in report.diagnostics))
    buffers=_buffers(path,doc); overrides,duration=_animation_overrides(path,doc,buffers,clip_index,time,animation_edits) if clip_index is not None else ({},0.0)
    nodes=_world_nodes(doc,overrides); joints=[{'index':i,'name':name,'position':pos,'parent':parent} for i,name,pos,parent in nodes]; world_matrices=_world_matrices(doc,overrides)
    skin_matrices={}
    for skin_index,skin in enumerate(doc.get('skins',[])):
        inverse=[_column_major_to_row_major(x) for x in _accessor(path,doc,buffers,skin['inverseBindMatrices'])] if 'inverseBindMatrices' in skin else [(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1) for _ in skin.get('joints',[])]
        skin_matrices[skin_index]=[_matmul(world_matrices[int(joint)],inverse[i]) for i,joint in enumerate(skin.get('joints',[]))]
    meshes=[]
    for node_index,node in enumerate(doc.get('nodes',[])):
        if 'mesh' not in node: continue
        for primitive in doc['meshes'][node['mesh']].get('primitives',[]):
            attrs=primitive.get('attributes',{}); pos=attrs.get('POSITION')
            if pos is None: continue
            vertices=_accessor(path,doc,buffers,pos); indices=_accessor(path,doc,buffers,primitive['indices']) if 'indices' in primitive else list(range(len(vertices))); uv_data=_accessor(path,doc,buffers,attrs['TEXCOORD_0']) if 'TEXCOORD_0' in attrs else []; normal_data=_accessor(path,doc,buffers,attrs['NORMAL']) if 'NORMAL' in attrs else []
            skin_index=node.get('skin'); joint_attr=attrs.get('JOINTS_0'); weight_attr=attrs.get('WEIGHTS_0')
            if skin_index is not None and joint_attr is not None and weight_attr is not None and skin_index in skin_matrices:
                joints_data=_accessor(path,doc,buffers,joint_attr); weights_data=_accessor(path,doc,buffers,weight_attr); mats=skin_matrices[skin_index]; deformed=[]
                for vertex,joints_v,weights_v in zip(vertices,joints_data,weights_data):
                    value=[0.0,0.0,0.0]; total=0.0
                    for joint,weight in zip(joints_v,weights_v):
                        if float(weight) and int(joint)<len(mats):
                            moved=_matvec(mats[int(joint)],tuple(float(x) for x in vertex)); total+=float(weight)
                            for axis in range(3):value[axis]+=moved[axis]*float(weight)
                    deformed.append(tuple(x/total for x in value) if total else tuple(float(x) for x in vertex))
                vertices=deformed
            else:
                node_matrix=world_matrices[node_index] if node_index<len(world_matrices) else (1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1); vertices=[_matvec(node_matrix,tuple(float(x) for x in vertex)) for vertex in vertices]
            meshes.append({'vertices':vertices,'indices':indices,'uvs':uv_data,'normals':normal_data,'node':node.get('name','mesh'),'material':_gltf_material(path,doc,buffers,primitive.get('material'))})
    return {'model':str(path.resolve()),'meshes':report.meshes,'joints':joints,'geometry':meshes,'duration':duration}

def render_svg(model, output):
    scene=preview_scene(model); joints=scene['joints']; coords=[j['position'] for j in joints] or [(0,0,0)]; xs=[p[0] for p in coords]; ys=[p[1] for p in coords]; xmin,xmax=min(xs),max(xs); ymin,ymax=min(ys),max(ys); sx=520/max(xmax-xmin,1); sy=400/max(ymax-ymin,1); scale=min(sx,sy); ox=60-(xmin*scale); oy=440+(ymin*scale)
    def xy(p): return (ox+p[0]*scale,oy-p[1]*scale)
    lines=[]
    for j in joints:
        if j['parent'] is not None and j['parent']<len(joints): x1,y1=xy(j['position']); x2,y2=xy(joints[j['parent']]['position']); lines.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" stroke="#777"/>')
    circles=[]
    for j in joints:
        x,y=xy(j['position']); circles.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="5" fill="#e44"/><text x="{x+7:.3f}" y="{y-7:.3f}" font-size="11">{j["name"]}</text>')
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480"><rect width="100%" height="100%" fill="#111"/><text x="12" y="22" fill="#fff" font-size="14">Melee Character Studio skeleton preview</text>'+''.join(lines+circles)+'</svg>\n'
    out=Path(output).expanduser().resolve(); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(svg,encoding="utf-8"); return out
