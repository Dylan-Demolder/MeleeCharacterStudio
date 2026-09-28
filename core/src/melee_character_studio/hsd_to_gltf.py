from __future__ import annotations
import base64, json, struct, math
from pathlib import Path
from .hsd_model import HsdReader, decode_hsd_geometry, _world_transforms, _hsd_world_matrices, _inverse_affine4, _inverse_transform, _matvec4, _matmul4
from .hsd_animation import scan_figatree, clip_transforms
from .hsd_texture import rgba_png

def _quat(e):
    x,y,z=e; cx,sx=math.cos(x/2),math.sin(x/2); cy,sy=math.cos(y/2),math.sin(y/2); cz,sz=math.cos(z/2),math.sin(z/2)
    return [sx*cy*cz-cx*sy*sz,cx*sy*cz+sx*cy*sz,cx*cy*sz-sx*sy*cz,cx*cy*cz+sx*sy*sz]

def _inverse_matrix(transform):
    origin=_inverse_transform((0,0,0),transform); x=_inverse_transform((1,0,0),transform); y=_inverse_transform((0,1,0),transform); z=_inverse_transform((0,0,1),transform)
    return [x[0]-origin[0],x[1]-origin[1],x[2]-origin[2],0, y[0]-origin[0],y[1]-origin[1],y[2]-origin[2],0, z[0]-origin[0],z[1]-origin[1],z[2]-origin[2],0, origin[0],origin[1],origin[2],1]

def _material_doc(material):
    diffuse=material.get("diffuse",(0.72,0.72,0.72,1.0)) if material else (0.72,0.72,0.72,1.0)
    return {"name":"HSD diffuse","pbrMetallicRoughness":{"baseColorFactor":[float(x) for x in diffuse],"metallicFactor":0.0,"roughnessFactor":0.8}}

def _attach_texture(doc,material):
    texture=material.get("texture") if material else None
    if not texture or not texture.get("mapped"):return
    png=rgba_png(texture); doc["images"]=[{"uri":"data:image/png;base64,"+base64.b64encode(png).decode("ascii"),"mimeType":"image/png"}]; doc["samplers"]=[{"magFilter":9729,"minFilter":9729,"wrapS":10497,"wrapT":10497}]; doc["textures"]=[{"sampler":0,"source":0}]; doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]={"index":0}

def export_hsd_gltf(source,output,symbol=None,animation_source=None,clip_index=0,texture_source=None):
    source=Path(source).expanduser().resolve(); report,parts=decode_hsd_geometry(source,symbol,texture_source); vertices=[]; indices=[]; joint_values=[]; weight_values=[]; uv_values=[]; normal_values=[]; has_normals=False; part_meta=[]
    for part in parts:
        start=len(vertices); index_start=len(indices); part_vertices=[_matvec4(part['right_matrix'],v) for v in part['vertices']] if part.get('right_matrix') else part['vertices']; vertices.extend(part_vertices); indices.extend(start+int(i) for i in part['indices']); uv_values.extend(part.get('uvs',[(0.0,0.0)]*len(part_vertices))); has_normals=has_normals or bool(part.get('normals')); normal_values.extend(part.get('normals',[(0.0,0.0,1.0)]*len(part_vertices))); part_meta.append((index_start,len(part['indices']),part.get('material',{})))
        for weights in part.get('skin',[]):
            ordered=sorted(weights,key=lambda x:x[1],reverse=True)[:4]; total=sum(float(x[1]) for x in ordered) or 1.0; ordered=[(int(x[0]),float(x[1])/total) for x in ordered]; ordered += [(0,0.0)]*(4-len(ordered)); joint_values.append(tuple(x[0] for x in ordered)); weight_values.append(tuple(x[1] for x in ordered))
    raw=bytearray(); views=[]; accessors=[]
    def align():
        while len(raw)%4:raw.append(0)
    def add_blob(blob,target=None):
        align(); offset=len(raw); raw.extend(blob); view=len(views); item={'buffer':0,'byteOffset':offset,'byteLength':len(blob)}
        if target:item['target']=target
        views.append(item); return view
    def add_accessor(view,component,count,kind,**extra):
        idx=len(accessors); item={'bufferView':view,'componentType':component,'count':count,'type':kind}; item.update(extra); accessors.append(item); return idx
    pos_view=add_blob(b''.join(struct.pack('<3f',*v[:3]) for v in vertices),34962); pos_acc=add_accessor(pos_view,5126,len(vertices),'VEC3')
    tex_acc=None; normal_acc=None
    if len(uv_values)==len(vertices) and any(u!=(0.0,0.0) for u in uv_values):
        tex_view=add_blob(b''.join(struct.pack('<2f',*u) for u in uv_values),34962); tex_acc=add_accessor(tex_view,5126,len(uv_values),'VEC2')
    if has_normals and len(normal_values)==len(vertices):
        normal_view=add_blob(b''.join(struct.pack('<3f',*n) for n in normal_values),34962); normal_acc=add_accessor(normal_view,5126,len(normal_values),'VEC3')
    joints_view=add_blob(b''.join(struct.pack('<4B',*j) for j in joint_values),34962); joints_acc=add_accessor(joints_view,5121,len(joint_values),'VEC4')
    weights_view=add_blob(b''.join(struct.pack('<4f',*w) for w in weight_values),34962); weights_acc=add_accessor(weights_view,5126,len(weight_values),'VEC4')
    idx_view=add_blob(b''.join(struct.pack('<I',i) for i in indices),34963); idx_acc=add_accessor(idx_view,5125,len(indices),'SCALAR')
    local=[{'position':list(j.position),'rotation':list(j.rotation),'scale':list(j.scale)} for j in report.joints]; world,_=_hsd_world_matrices(report.joints,local); bind=[tuple(j.envelope_matrix)+(0,0,0,1) if j.envelope_matrix else _inverse_affine4(world[i]) for i,j in enumerate(report.joints)]; ibm_view=add_blob(b''.join(struct.pack('<16f',*(matrix[r*4+c] for c in range(4) for r in range(4))) for matrix in bind)); ibm_acc=add_accessor(ibm_view,5126,len(report.joints),'MAT4')
    local_matrices=[]
    for i,j in enumerate(report.joints): local_matrices.append(world[i] if j.parent is None else _matmul4(_inverse_affine4(world[j.parent]),world[i]))
    nodes=[]
    for i,j in enumerate(report.joints):
        matrix=local_matrices[i]; node={'name':j.name,'matrix':[matrix[r*4+c] for c in range(4) for r in range(4)]}; children=list(j.children)
        if children:node['children']=children
        nodes.append(node)
    roots=[j.index for j in report.joints if j.parent is None]
    if roots:nodes[roots[0]]['mesh']=0; nodes[roots[0]]['skin']=0
    animations=[]
    if animation_source:
        clips=scan_figatree(animation_source)
        if not clips: raise ValueError('animation source has no FigaTree clips')
        clip=clips[int(clip_index)%len(clips)]; frame_count=max(1,round(clip.frames)); times=[i/60.0 for i in range(frame_count+1)]; time_view=add_blob(b''.join(struct.pack('<f',x) for x in times)); time_acc=add_accessor(time_view,5126,len(times),'SCALAR',min=[0.0],max=[times[-1]])
        samples=[clip_transforms(animation_source,clip,i) for i in range(frame_count+1)]; samplers=[]; channels=[]
        for joint_index in range(len(report.joints)):
            for path,kind in (('rotation','VEC4'),('translation','VEC3'),('scale','VEC3')):
                outputs=[]
                for frame_values in samples:
                    current={'rotation':list(report.joints[joint_index].rotation),'translation':list(report.joints[joint_index].position),'scale':list(report.joints[joint_index].scale)}
                    if joint_index<len(frame_values):
                        for obj_type,value in frame_values[joint_index]:
                            if obj_type==1:current['rotation'][0]=value
                            elif obj_type==2:current['rotation'][1]=value
                            elif obj_type==3:current['rotation'][2]=value
                            elif obj_type==5:current['translation'][0]=value
                            elif obj_type==6:current['translation'][1]=value
                            elif obj_type==7:current['translation'][2]=value
                            elif obj_type==8:current['scale'][0]=value
                            elif obj_type==9:current['scale'][1]=value
                            elif obj_type==10:current['scale'][2]=value
                    outputs.append(_quat(current[path]) if path=='rotation' else tuple(current[path]))
                out_view=add_blob(b''.join(struct.pack('<'+('4f' if kind=='VEC4' else '3f'),*value) for value in outputs)); out_acc=add_accessor(out_view,5126,len(outputs),kind); sampler_index=len(samplers); samplers.append({'input':time_acc,'output':out_acc,'interpolation':'LINEAR'}); channels.append({'sampler':sampler_index,'target':{'node':joint_index,'path':path}})
        animations=[{'name':clip.name.replace('_figatree',''),'samplers':samplers,'channels':channels}]
    material_docs=[]; material_indices=[]; material_keys={}; texture_docs=[]
    for _,_,material in part_meta:
        texture=material.get('texture') if material else None; key=('tex',bytes(texture.get('rgba',b''))) if texture and texture.get('mapped') else ('diff',tuple(material.get('diffuse',(0.72,0.72,0.72,1.0))))
        if key not in material_keys: material_keys[key]=len(material_docs); material_docs.append(_material_doc(material)); texture_docs.append(texture if texture and texture.get('mapped') else None)
        material_indices.append(material_keys[key])
    attrs={'POSITION':pos_acc,'JOINTS_0':joints_acc,'WEIGHTS_0':weights_acc}
    if tex_acc is not None:attrs['TEXCOORD_0']=tex_acc
    if normal_acc is not None:attrs['NORMAL']=normal_acc
    primitives=[]
    for part_no,(index_start,index_count,_) in enumerate(part_meta):
        part_idx_acc=add_accessor(idx_view,5125,index_count,'SCALAR',byteOffset=index_start*4); primitives.append({'attributes':dict(attrs),'indices':part_idx_acc,'material':material_indices[part_no]})
    uri='data:application/octet-stream;base64,'+base64.b64encode(raw).decode('ascii')
    doc={'asset':{'version':'2.0','generator':'Melee Character Studio HSD importer'},'buffers':[{'uri':uri,'byteLength':len(raw)}],'bufferViews':views,'accessors':accessors,'meshes':[{'name':source.stem,'primitives':primitives}],'materials':material_docs,'nodes':nodes,'skins':[{'joints':[j.index for j in report.joints],'skeleton':roots[0] if roots else 0,'inverseBindMatrices':ibm_acc}]}
    if any(texture_docs):
        doc['images']=[]; doc['samplers']=[]; doc['textures']=[]; image_index=0; sampler_indices={}
        for material,texture in zip(material_docs,texture_docs):
            if not texture:continue
            key=(int(texture.get('wrap_s',1)),int(texture.get('wrap_t',1)))
            if key not in sampler_indices:
                mode={0:33071,1:10497,2:33648}; sampler_indices[key]=len(doc['samplers']); doc['samplers'].append({'magFilter':9729,'minFilter':9729,'wrapS':mode.get(key[0],10497),'wrapT':mode.get(key[1],10497)})
            png=rgba_png(texture); doc['images'].append({'uri':'data:image/png;base64,'+base64.b64encode(png).decode('ascii'),'mimeType':'image/png'}); doc['textures'].append({'sampler':sampler_indices[key],'source':image_index}); material['pbrMetallicRoughness']['baseColorTexture']={'index':image_index}; image_index+=1
    if animations:doc['animations']=animations
    out=Path(output).expanduser().resolve(); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(doc,indent=2)+'\n',encoding='utf-8'); return out


def export_scene_gltf(scene, output):
    """Export the currently displayed authoring scene without game data."""
    vertices=[]; indices=[]; joint_values=[]; weight_values=[]; uv_values=[]; normal_values=[]; has_normals=False; part_meta=[]
    for part in scene.get('geometry',[]):
        start=len(vertices); index_start=len(indices); part_vertices=part.get('vertices',[]); vertices.extend(part_vertices); indices.extend(start+int(i) for i in part.get('indices',[])); uv_values.extend(part.get('uvs',[(0.0,0.0)]*len(part_vertices))); has_normals=has_normals or bool(part.get('normals')); normal_values.extend(part.get('normals',[(0.0,0.0,1.0)]*len(part_vertices))); part_meta.append((index_start,len(part.get('indices',[])),part.get('material',{})))
        for weights in part.get('skin',[]):
            ordered=sorted(weights,key=lambda x:x[1],reverse=True)[:4]; total=sum(float(x[1]) for x in ordered) or 1.0; ordered=[(int(x[0]),float(x[1])/total) for x in ordered]; ordered += [(0,0.0)]*(4-len(ordered)); joint_values.append(tuple(x[0] for x in ordered)); weight_values.append(tuple(x[1] for x in ordered))
    raw=bytearray(); views=[]
    def align():
        while len(raw)%4: raw.append(0)
    def blob(data,target=None):
        align(); off=len(raw); raw.extend(data); item={'buffer':0,'byteOffset':off,'byteLength':len(data)}
        if target:item['target']=target
        views.append(item); return len(views)-1
    pos=blob(b''.join(struct.pack('<3f',*v[:3]) for v in vertices),34962); joints=blob(b''.join(struct.pack('<4B',*j) for j in joint_values),34962); weights=blob(b''.join(struct.pack('<4f',*w) for w in weight_values),34962); ind=blob(b''.join(struct.pack('<I',i) for i in indices),34963)
    accessors=[{'bufferView':pos,'componentType':5126,'count':len(vertices),'type':'VEC3'},{'bufferView':joints,'componentType':5121,'count':len(joint_values),'type':'VEC4'},{'bufferView':weights,'componentType':5126,'count':len(weight_values),'type':'VEC4'},{'bufferView':ind,'componentType':5125,'count':len(indices),'type':'SCALAR'}]
    tex_acc=None
    if len(uv_values)==len(vertices) and any(u!=(0.0,0.0) for u in uv_values):
        tex=blob(b''.join(struct.pack('<2f',*u) for u in uv_values),34962); tex_acc=len(accessors); accessors.append({'bufferView':tex,'componentType':5126,'count':len(uv_values),'type':'VEC2'})
    normal_acc=None
    if has_normals and len(normal_values)==len(vertices):
        normal_view=blob(b''.join(struct.pack('<3f',*n) for n in normal_values),34962); normal_acc=len(accessors); accessors.append({'bufferView':normal_view,'componentType':5126,'count':len(normal_values),'type':'VEC3'})
    nodes=[]; js=scene.get('joints',[])
    for j in js:
        n={'name':j.get('name',f'joint_{j["index"]}'),'translation':list(j.get('local_position',j.get('position',(0,0,0))))}; children=[x['index'] for x in js if x.get('parent')==j['index']]
        if children:n['children']=children
        nodes.append(n)
    roots=[j['index'] for j in js if j.get('parent') is None]
    material_docs=[]; material_indices=[]; material_keys={}; texture_docs=[]
    for _,_,material in part_meta:
        texture=material.get('texture') if material else None; key=('tex',bytes(texture.get('rgba',b''))) if texture and texture.get('mapped') else ('diff',tuple(material.get('diffuse',(0.72,0.72,0.72,1.0))))
        if key not in material_keys: material_keys[key]=len(material_docs); material_docs.append(_material_doc(material)); texture_docs.append(texture if texture and texture.get('mapped') else None)
        material_indices.append(material_keys[key])
    attrs={'POSITION':0}
    if joint_values:attrs.update({'JOINTS_0':1,'WEIGHTS_0':2})
    if tex_acc is not None:attrs['TEXCOORD_0']=tex_acc
    if normal_acc is not None:attrs['NORMAL']=normal_acc
    primitives=[]
    for part_no,(index_start,index_count,_) in enumerate(part_meta):
        part_idx=len(accessors); accessors.append({'bufferView':ind,'byteOffset':index_start*4,'componentType':5125,'count':index_count,'type':'SCALAR'}); primitives.append({'attributes':dict(attrs),'indices':part_idx,'material':material_indices[part_no]})
    uri='data:application/octet-stream;base64,'+base64.b64encode(raw).decode('ascii')
    doc={'asset':{'version':'2.0','generator':'Melee Character Studio scene exporter'},'buffers':[{'uri':uri,'byteLength':len(raw)}],'bufferViews':views,'accessors':accessors,'meshes':[{'name':Path(scene.get('model','character')).stem,'primitives':primitives}],'materials':material_docs,'nodes':nodes}
    if any(texture_docs):
        doc['images']=[]; doc['samplers']=[]; doc['textures']=[]; image_index=0; sampler_indices={}
        for material,texture in zip(material_docs,texture_docs):
            if not texture:continue
            key=(int(texture.get('wrap_s',1)),int(texture.get('wrap_t',1)))
            if key not in sampler_indices:
                mode={0:33071,1:10497,2:33648}; sampler_indices[key]=len(doc['samplers']); doc['samplers'].append({'magFilter':9729,'minFilter':9729,'wrapS':mode.get(key[0],10497),'wrapT':mode.get(key[1],10497)})
            png=rgba_png(texture); doc['images'].append({'uri':'data:image/png;base64,'+base64.b64encode(png).decode('ascii'),'mimeType':'image/png'}); doc['textures'].append({'sampler':sampler_indices[key],'source':image_index}); material['pbrMetallicRoughness']['baseColorTexture']={'index':image_index}; image_index+=1
    if roots:doc['nodes'][roots[0]]['mesh']=0
    out=Path(output).expanduser().resolve(); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(doc,indent=2)+'\n',encoding='utf-8'); return out
