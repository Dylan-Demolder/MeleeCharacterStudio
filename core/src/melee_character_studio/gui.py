from __future__ import annotations
import json
import shutil
from pathlib import Path
from .authoring import ProjectEditor
from .gltf import _json, validate_model
from .move_library import load_library
from .preview import preview_scene, animation_durations
from .base_fighters import list_base_fighters, find_base_fighter
from .game_assets import discover_fighter_assets
from .asset_worker import AssetWorker, LoadResult
from .hsd_model import hsd_scene, hsd_animation_scene
from .hsd_animation import scan_figatree, export_hsd_animation
from .hsd_to_gltf import export_hsd_gltf, export_scene_gltf
from .hsd_patch import position_edits_from_hsd_scene, patch_hsd_streams
from .game_assets import FIGHTER_CODES

DEFAULT_MOVES={
    "jab":{"display_name":"Jab","slot":"attack_neutral","startup_frames":2,"active_frames":3,"recovery_frames":8},
    "ftilt":{"display_name":"Forward Tilt","slot":"attack_forward","startup_frames":5,"active_frames":4,"recovery_frames":18},
    "nair":{"display_name":"Neutral Air","slot":"attack_air_neutral","startup_frames":4,"active_frames":5,"recovery_frames":24},
    "fair":{"display_name":"Forward Air","slot":"attack_air_forward","startup_frames":8,"active_frames":5,"recovery_frames":28},
    "up-special":{"display_name":"Up Special","slot":"special_up","startup_frames":6,"active_frames":8,"recovery_frames":35},
}

class StudioController:
    def __init__(self, project): self.editor=ProjectEditor(project); self._model_source=None
    def set_model_source(self,path,validate=True):
        source=Path(path).expanduser().resolve()
        report=validate_model(source) if validate else None
        if report is not None and not report.valid: raise ValueError("model failed validation: "+"; ".join(d.message for d in report.diagnostics))
        self._model_source=source; self.editor.character["model_source"]=str(source); return report
    def moves(self): return list(self.editor.moveset.get("moves",[]))
    def mesh_edits(self): return list(self.editor.character.get("mesh_edits",[]))
    def set_mesh_edits(self,edits): self.editor.character["mesh_edits"]=[dict(edit) for edit in edits]; return self.mesh_edits()
    def uv_edits(self): return list(self.editor.character.get("uv_edits",[]))
    def set_uv_edits(self,edits): self.editor.character["uv_edits"]=[dict(edit) for edit in edits]; return self.uv_edits()
    def normal_edits(self): return list(self.editor.character.get("normal_edits",[]))
    def set_normal_edits(self,edits): self.editor.character["normal_edits"]=[dict(edit) for edit in edits]; return self.normal_edits()
    def mesh_topology_ops(self): return list(self.editor.character.get("mesh_topology_ops",[]))
    def set_mesh_topology_ops(self,operations): self.editor.character["mesh_topology_ops"]=[dict(operation) for operation in operations]; return self.mesh_topology_ops()
    def base_fighters(self): return list_base_fighters()
    def infer_hsd_fighter(self,path):
        stem=Path(path).stem
        code=stem[2:4].lower() if stem.lower().startswith("pl") else ""
        for fighter_id,fighter_code in FIGHTER_CODES.items():
            if fighter_code.lower()==code: return self.set_base_fighter(fighter_id)
        return self.selected_base_fighter()
    def selected_base_fighter(self): return find_base_fighter(self.editor.character.get("base_fighter", "mario"))
    def set_base_fighter(self, identifier):
        fighter=find_base_fighter(identifier)
        self.editor.character["base_fighter"]=fighter.id
        return fighter
    def add_move(self,name,slot,reference=None,animation=None,timing=None,hitboxes=None,hurtboxes=None):
        move=self.editor.add_move(name,slot,reference,hitboxes=hitboxes,hurtboxes=hurtboxes)
        if animation: move["animation"]=animation
        if timing is not None:
            for key in ("startup_frames","active_frames","recovery_frames"):
                if key in timing: move[key]=int(timing[key])
        return move
    def update_move(self,old_name,name=None,slot=None,reference=None,animation=None,timing=None,hitboxes=None,hurtboxes=None):
        move=self.editor.update_move(old_name,name=name,slot=slot,reference=reference,timing=timing,hitboxes=hitboxes,hurtboxes=hurtboxes)
        if animation is not None: move["animation"]=animation
        return move
    def remove_move(self,name): return self.editor.remove_move(name)
    def set_attribute(self,name,value): return self.editor.set_attribute(name,value)
    def diagnostics(self): return self.editor.validate()
    def save(self,output):
        diagnostics=self.diagnostics()
        if diagnostics: raise ValueError("project is invalid: "+"; ".join(d.message for d in diagnostics))
        target=self.editor.save(output)
        if self._model_source:
            for stale in (target/"model.gltf",target/"model.glb"): stale.unlink(missing_ok=True)
            shutil.copy2(self._model_source,target/("model.glb" if self._model_source.suffix.lower()==".glb" else "model.gltf"))
        return target
    def model_path(self):
        if self._model_source: return self._model_source
        candidate=self.editor.project/"model.gltf"
        return candidate if candidate.is_file() else self.editor.project/"model.glb"
    def model_report(self):
        path=self.model_path()
        return (path,validate_model(path)) if path.is_file() else (path,None)
    def model_scene(self):
        path,report=self.model_report()
        if report is None: raise ValueError("add model.gltf or model.glb to the project")
        return preview_scene(path)
    def load_model_scene(self):
        path,report=self.model_report()
        if report is None: raise ValueError("add model.gltf or model.glb to the project")
        if not report.valid: raise ValueError("model validation failed: "+"; ".join(d.message for d in report.diagnostics))
        return path,report,preview_scene(path)
    def load_hsd_scene(self,path,symbol=None,texture_source=None): return hsd_scene(path,symbol,texture_source)
    def hsd_animation_clips(self,path): return scan_figatree(path)
    def load_hsd_animation_scene(self,model_path,animation_path,clip_index,frame,texture_source=None,joint_edits=None): return hsd_animation_scene(model_path,animation_path,clip_index,frame,texture_source,joint_edits)
    def animation_edits(self): return list(self.editor.character.get("animation_edits",[]))
    def set_animation_edits(self,edits): self.editor.character["animation_edits"]=[dict(edit) for edit in edits]; return self.animation_edits()
    def convert_hsd(self,source,output,animation_source=None,clip_index=0,texture_source=None): return export_hsd_gltf(source,output,animation_source=animation_source,clip_index=clip_index,texture_source=texture_source)
    def export_hsd_animation(self,source,output,clip_index,edits): return export_hsd_animation(source,output,clip_index,edits)
    def patch_hsd_scene(self,scene,source,output,vertex_edits,uv_edits=None,normal_edits=None,texture_source=None,texture_output=None):
        edits=position_edits_from_hsd_scene(scene,vertex_edits,uv_edits,normal_edits); main=[dict(edit) for edit in edits if edit.get("archive","model")=="model"]; texture=[dict(edit) for edit in edits if edit.get("archive")=="texture"]
        for edit in main: edit.pop("archive",None)
        result=patch_hsd_streams(source,output,main)
        if texture:
            if not texture_source or not texture_output: raise ValueError("costume UV/normal edits require texture source and output paths")
            for edit in texture: edit.pop("archive",None)
            texture_result=patch_hsd_streams(texture_source,texture_output,texture); return (result[0],result[1],texture_result[0],texture_result[1])
        return result
    def export_scene(self,scene,output): return export_scene_gltf(scene,output)
    def animation_scene(self,clip_index,time_seconds,animation_edits=None):
        path,report=self.model_report()
        if report is None or not report.valid: raise ValueError("valid model required")
        return preview_scene(path,clip_index,time_seconds,animation_edits)
    def animation_clips(self):
        path=self.model_path()
        if not path.is_file(): return []
        doc,errors=_json(path)
        if errors or doc is None: return []
        result=[]
        durations=animation_durations(path)
        for i,clip in enumerate(doc.get("animations",[])):
            result.append({"index":i,"name":clip.get("name",f"clip_{i}"),"channels":len(clip.get("channels",[])),"samplers":len(clip.get("samplers",[])),"duration":durations[i] if i<len(durations) else 0.0})
        return result
    def animation_requirements(self): return tuple(self.selected_base_fighter().animation_slots)
    def import_animation_source(self, path, slot):
        source=Path(path).expanduser().resolve()
        report=validate_model(source)
        if not report.valid: raise ValueError("animation source model failed validation")
        self.editor.character.setdefault("animation_sources",{})[slot]=str(source)
        return {"slot":slot,"source":str(source),"meshes":report.meshes,"joints":report.joints}
    def library(self):
        path=self.editor.project/"approved-moves.json"
        if path.is_file(): return load_library(path).moves
        return DEFAULT_MOVES.copy()
    def select_library_move(self,ident):
        move=self.library()[ident]
        return self.add_move(move["display_name"],move["slot"],ident)

def run(project, output=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
    controller=StudioController(project); app=tk.Tk(); app.title("Melee Character Studio"); app.geometry("1180x760"); app.minsize(960,620)
    style=ttk.Style(app)
    try: style.theme_use("clam")
    except tk.TclError: pass
    style.configure("Title.TLabel",font=("TkDefaultFont",18,"bold")); style.configure("Sub.TLabel",foreground="#667085")
    header=ttk.Frame(app,padding=12); header.pack(fill="x")
    ttk.Label(header,text="Melee Character Studio",style="Title.TLabel").pack(side="left")
    ttk.Label(header,text=f"  {Path(project).name}  •  source-preserving authoring",style="Sub.TLabel").pack(side="left",pady=6)
    actions=ttk.Frame(header); actions.pack(side="right")
    output_text=tk.Text(app,height=5,state="disabled",wrap="word"); output_text.pack(side="bottom",fill="x",padx=12,pady=(0,12))
    notebook=ttk.Notebook(app); notebook.pack(fill="both",expand=True,padx=12)
    def report(value):
        output_text.configure(state="normal"); output_text.delete("1.0","end"); output_text.insert("end",str(value)); output_text.configure(state="disabled")
    def validate():
        d=controller.diagnostics(); report(json.dumps({"valid":not d,"diagnostics":[x.__dict__ for x in d]},indent=2))
    def save():
        try:
            target=output or filedialog.askdirectory(title="Save edited project")
            if target: report(controller.save(target))
        except Exception as exc: messagebox.showerror("Character Studio",str(exc))
    ttk.Button(actions,text="Validate",command=validate).pack(side="left",padx=3); ttk.Button(actions,text="Export project",command=save).pack(side="left",padx=3)

    # Character setup tab: metadata-only roster and skeleton capability selector.
    setup_tab=ttk.Frame(notebook,padding=10); notebook.add(setup_tab,text="Character Setup")
    setup_left=ttk.Frame(setup_tab); setup_left.pack(side="left",fill="both",expand=True)
    setup_right=ttk.LabelFrame(setup_tab,text="Base fighter details",padding=12); setup_right.pack(side="right",fill="y",padx=(12,0))
    roster_tree=ttk.Treeview(setup_left,columns=("name","skeleton","mode"),show="headings",selectmode="browse")
    for col,label,w in (("name","Base fighter",230),("skeleton","Skeleton mode",180),("mode","Authoring mode",130)):
        roster_tree.heading(col,text=label); roster_tree.column(col,width=w)
    roster_tree.pack(fill="both",expand=True)
    setup_title=ttk.Label(setup_right,text="Select a base fighter",font=("TkDefaultFont",14,"bold")); setup_title.pack(anchor="w")
    setup_note=tk.Text(setup_right,width=34,height=10,wrap="word",state="disabled",borderwidth=0,background=app.cget("background")); setup_note.pack(fill="both",expand=True,pady=10)
    def refresh_roster():
        roster_tree.delete(*roster_tree.get_children())
        chosen=controller.editor.character.get("base_fighter","mario")
        for fighter in controller.base_fighters():
            roster_tree.insert("","end",iid=fighter.id,values=(fighter.name,fighter.skeleton,fighter.mode))
        if chosen in roster_tree.get_children(): roster_tree.selection_set(chosen)
        select_roster()
    def select_roster(_event=None):
        sel=roster_tree.selection()
        if not sel: return
        fighter=find_base_fighter(sel[0]); setup_title.configure(text=fighter.name)
        setup_note.configure(state="normal"); setup_note.delete("1.0","end"); setup_note.insert("end",f"ID: {fighter.id}\nSkeleton: {fighter.skeleton}\nMode: {fighter.mode}\n\n{fighter.notes}\n\nThis roster entry is metadata only. Studio does not ship Nintendo assets."); setup_note.configure(state="disabled")
    def choose_roster():
        sel=roster_tree.selection()
        if not sel: return
        fighter=controller.set_base_fighter(sel[0]); report(f"Selected base fighter: {fighter.name} ({fighter.skeleton} skeleton mode). Validate before export.")
    ttk.Button(setup_right,text="Use selected base fighter",command=choose_roster).pack(fill="x")
    def discover_assets():
        root=filedialog.askdirectory(title="Choose extracted Melee files or decomp root")
        if not root: return
        try:
            assets=discover_fighter_assets(root,controller.selected_base_fighter().id)
            if not assets: raise ValueError("No fighter Pl*.dat files found for the selected fighter.")
            controller.editor.character["game_asset_source"] = str(Path(root).expanduser().resolve())
            report({"asset_source":str(root),"fighter":controller.selected_base_fighter().name,"files":[{"name":x.path.name,"kind":x.kind,"bytes":x.size} for x in assets],"notice":"Local source recorded; assets are not copied into the project."})
        except Exception as exc: messagebox.showerror("Melee asset discovery",str(exc))
    ttk.Button(setup_right,text="Discover local ISO/decomp assets",command=discover_assets).pack(fill="x",pady=(6,0))
    roster_tree.bind("<<TreeviewSelect>>",select_roster); refresh_roster()

    # Model viewer tab: deterministic skeleton/canvas viewer, with diagnostics.
    model_tab=ttk.Frame(notebook,padding=10); notebook.add(model_tab,text="Model Viewer")
    viewer_left=ttk.Frame(model_tab); viewer_left.pack(side="left",fill="both",expand=True)
    viewer_right=ttk.LabelFrame(model_tab,text="Model diagnostics",padding=10); viewer_right.pack(side="right",fill="y",padx=(10,0))
    canvas=tk.Canvas(viewer_left,background="#111827",highlightthickness=0); canvas.pack(fill="both",expand=True)
    model_status=ttk.Label(viewer_right,text="No model loaded",wraplength=280,justify="left"); model_status.pack(anchor="w")
    ttk.Separator(viewer_right,orient="horizontal").pack(fill="x",pady=12)
    ttk.Label(viewer_right,text="Base fighter",font=("TkDefaultFont",10,"bold")).pack(anchor="w")
    ttk.Label(viewer_right,text=controller.selected_base_fighter().name).pack(anchor="w")
    ttk.Label(viewer_right,text="Animation state",font=("TkDefaultFont",10,"bold")).pack(anchor="w",pady=(10,2))
    model_anim_var=tk.StringVar(value="idle")
    model_anim_box=ttk.Combobox(viewer_right,textvariable=model_anim_var,state="readonly",width=28); model_anim_box.pack(anchor="w")
    def open_model_animation():
        state=model_anim_var.get()
        notebook.select(anim_tab)
        for i,clip in enumerate(controller.animation_clips()):
            if str(clip["name"]).lower().replace(" ","_")==state:
                anim_list.selection_clear(0,"end"); anim_list.selection_set(i); select_anim(); return
        report({"animation_slot":state,"status":"missing","message":"Import an author-owned glTF/GLB clip for this required slot."})
    ttk.Button(viewer_right,text="Preview selected animation",command=open_model_animation).pack(fill="x",pady=(8,0))
    asset_worker=AssetWorker(2); scene_future=[None]; scene_generation=[0]
    def render_scene(value):
        path,rep,scene=value; canvas.delete("all"); joints=scene["joints"]; points=[j["position"] for j in joints]
        for mesh in scene.get("geometry",[]): points.extend(mesh["vertices"])
        coords=points or [(0,0,0)]; xs=[p[0] for p in coords]; ys=[p[1] for p in coords]; sx=max(max(xs)-min(xs),1); sy=max(max(ys)-min(ys),1)
        def xy(p): return (80+(p[0]-min(xs))/sx*max(canvas.winfo_width()-160,100),canvas.winfo_height()-80-(p[1]-min(ys))/sy*max(canvas.winfo_height()-140,100))
        for mesh in scene.get("geometry",[]):
            verts=mesh["vertices"]; inds=mesh["indices"]
            for k in range(0,len(inds)-2,3):
                tri=[]
                for index in inds[k:k+3]: tri.extend(xy(verts[int(index)][:3]))
                if len(tri)==6: canvas.create_polygon(*tri,fill="#2563eb",outline="#93c5fd")
        for j in joints:
            if j["parent"] is not None and j["parent"]<len(joints): canvas.create_line(*xy(j["position"]),*xy(joints[j["parent"]]["position"]),fill="#f8fafc",width=2)
        for j in joints:
            x,y=xy(j["position"]); canvas.create_oval(x-5,y-5,x+5,y+5,fill="#38bdf8",outline=""); canvas.create_text(x+8,y-8,text=j["name"],fill="#e2e8f0",anchor="w")
        if rep.meshes==0: canvas.create_text(24,24,text="Skeleton preview — no renderable mesh in this project",fill="#fbbf24",anchor="nw",font=("TkDefaultFont",11,"bold")); canvas.create_text(24,48,text="Import an author-owned glTF/GLB model with mesh data to see the model.",fill="#cbd5e1",anchor="nw")
        model_status.configure(text=f"{path.name}\n\nMeshes: {rep.meshes}\nPrimitives: {rep.primitives}\nMaterials: {rep.materials}\nSkeleton nodes: {len(joints)}\n\n{'Skeleton-only preview' if rep.meshes == 0 else 'Loaded mesh preview'}")
    def poll_scene():
        future=scene_future[0]
        if future is None or not future.done():
            if future is not None: app.after(40,poll_scene)
            return
        try: render_scene(future.result())
        except Exception as exc: model_status.configure(text=str(exc)); canvas.delete("all"); canvas.create_text(24,24,text="Model load failed",fill="#f87171",anchor="nw")
    def draw_scene():
        scene_generation[0]+=1; canvas.delete("all"); canvas.create_text(24,24,text="Loading model in worker…",fill="#cbd5e1",anchor="nw")
        scene_future[0]=asset_worker.submit(controller.load_model_scene); app.after(40,poll_scene)
    def import_model():
        source=filedialog.askopenfilename(title="Choose author-owned model glTF/GLB",filetypes=(("glTF/GLB","*.gltf *.glb"),("All files","*.*")))
        if not source: return
        try: rep=controller.set_model_source(source); report({"model_source":source,"meshes":rep.meshes,"primitives":rep.primitives,"notice":"Source will be copied only when you export the edited project."}); draw_scene(); refresh_anims()
        except Exception as exc: messagebox.showerror("Model import",str(exc))
    ttk.Button(viewer_right,text="Import model…",command=import_model).pack(fill="x",pady=(12,0)); ttk.Button(viewer_right,text="Reload model",command=draw_scene).pack(anchor="w",pady=(6,0)); canvas.bind("<Configure>",lambda _e: draw_scene()); app.after(100,draw_scene)

    # Animation tab: threaded clip loading, mesh/skeleton playback and slot status.
    anim_tab=ttk.Frame(notebook,padding=10); notebook.add(anim_tab,text="Animation Viewer")
    anim_left=ttk.Frame(anim_tab); anim_left.pack(side="left",fill="y")
    ttk.Label(anim_left,text="Imported clips",font=("TkDefaultFont",11,"bold")).pack(anchor="w")
    anim_list=tk.Listbox(anim_left,width=34,exportselection=False); anim_list.pack(fill="y",expand=True)
    ttk.Label(anim_left,text="Required slots",font=("TkDefaultFont",11,"bold")).pack(anchor="w",pady=(12,0))
    required_list=tk.Listbox(anim_left,width=34,height=14,exportselection=False); required_list.pack(fill="y")
    anim_panel=ttk.Frame(anim_tab,padding=(12,0)); anim_panel.pack(side="left",fill="both",expand=True)
    anim_name=ttk.Label(anim_panel,text="Select an animation",font=("TkDefaultFont",14,"bold")); anim_name.pack(anchor="w")
    anim_canvas=tk.Canvas(anim_panel,background="#111827",height=360,highlightthickness=0); anim_canvas.pack(fill="both",expand=True,pady=10)
    timeline=ttk.Scale(anim_panel,from_=0,to=120,orient="horizontal"); timeline.pack(fill="x")
    anim_frame_label=ttk.Label(anim_panel,text="Frame 0  •  60 fps",style="Sub.TLabel"); anim_frame_label.pack(anchor="w")
    anim_controls=ttk.Frame(anim_panel); anim_controls.pack(fill="x")
    playing=[False]; anim_future=[None]; anim_clip=[0]; anim_time=[0.0]; anim_polling=[False]
    def draw_anim_scene(scene):
        anim_canvas.delete("all"); joints=scene["joints"]; points=[j["position"] for j in joints]
        for mesh in scene.get("geometry",[]): points.extend(mesh["vertices"])
        coords=points or [(0,0,0)]; xs=[p[0] for p in coords]; ys=[p[1] for p in coords]; sx=max(max(xs)-min(xs),1); sy=max(max(ys)-min(ys),1)
        def xy(p): return (80+(p[0]-min(xs))/sx*max(anim_canvas.winfo_width()-160,100),anim_canvas.winfo_height()-80-(p[1]-min(ys))/sy*max(anim_canvas.winfo_height()-140,100))
        for mesh in scene.get("geometry",[]):
            for k in range(0,len(mesh["indices"])-2,3):
                tri=[]
                for idx in mesh["indices"][k:k+3]: tri.extend(xy(mesh["vertices"][int(idx)][:3]))
                if len(tri)==6: anim_canvas.create_polygon(*tri,fill="#2563eb",outline="#93c5fd")
        for j in joints:
            if j["parent"] is not None and j["parent"]<len(joints): anim_canvas.create_line(*xy(j["position"]),*xy(joints[j["parent"]]["position"]),fill="#f8fafc",width=2)
        for j in joints:
            x,y=xy(j["position"]); anim_canvas.create_oval(x-5,y-5,x+5,y+5,fill="#38bdf8",outline=""); anim_canvas.create_text(x+8,y-8,text=j["name"],fill="#e2e8f0",anchor="w")
        if not scene.get("geometry"): anim_canvas.create_text(20,20,text="Skeleton-only animation preview",fill="#fbbf24",anchor="nw")
        duration=scene.get("duration",0.0); anim_frame_label.configure(text=f"Frame {round(anim_time[0]*60)}  •  60 fps  •  duration {duration:.2f}s")
    def poll_anim():
        f=anim_future[0]
        if f is None or not f.done():
            if f is not None: app.after(25,poll_anim)
            return
        try:
            draw_anim_scene(f.result())
            if playing[0]: anim_time[0]+=1/60; timeline.set(anim_time[0]*60); request_anim_frame()
        except Exception as exc: anim_canvas.delete("all"); anim_canvas.create_text(20,20,text=f"Animation load failed: {exc}",fill="#f87171",anchor="nw")
    def request_anim_frame():
        if not anim_list.curselection(): return
        anim_future[0]=asset_worker.submit(controller.animation_scene,anim_clip[0],anim_time[0]); app.after(25,poll_anim)
    def refresh_anims():
        anim_list.delete(0,"end"); required_list.delete(0,"end")
        clips=controller.animation_clips(); names={str(x["name"]).lower().replace(" ","_") for x in clips}; model_anim_box.configure(values=tuple(controller.animation_requirements()))
        for clip in clips: anim_list.insert("end",f"{clip['name']}  ({clip['channels']} channels)")
        for slot in controller.animation_requirements(): required_list.insert("end",f"{'[imported]' if slot in names else '[missing]'} {slot}")
        if anim_list.size(): anim_list.selection_set(0); select_anim()
        else: anim_name.configure(text="No animation clips in model")
    def select_anim(_e=None):
        if not anim_list.curselection(): return
        idx=anim_list.curselection()[0]; anim_clip[0]=idx; anim_time[0]=0.0; clip=controller.animation_clips()[idx]; anim_name.configure(text=f"{clip['name']}  •  {clip['channels']} channels  •  {clip['samplers']} samplers"); timeline.set(0); request_anim_frame()
    def import_anim():
        source=filedialog.askopenfilename(title="Choose author-owned animation glTF/GLB",filetypes=(("glTF/GLB","*.gltf *.glb"),("All files","*.*")))
        if not source: return
        slot=simpledialog.askstring("Animation slot","Required slot for this animation:",parent=app)
        if not slot: return
        try: report(controller.import_animation_source(source,slot)); refresh_anims()
        except Exception as exc: messagebox.showerror("Character Studio",str(exc))
    def play(): playing[0]=not playing[0]; play_button.configure(text="Pause" if playing[0] else "Play"); request_anim_frame()
    def step(delta): playing[0]=False; play_button.configure(text="Play"); anim_time[0]=max(0,anim_time[0]+delta/60); timeline.set(anim_time[0]*60); request_anim_frame()
    def scrub(value):
        anim_time[0]=max(0,float(value)/60); request_anim_frame()
    timeline.configure(command=scrub)
    play_button=ttk.Button(anim_controls,text="Play",command=play); play_button.pack(side="left"); ttk.Button(anim_controls,text="Frame -",command=lambda:step(-1)).pack(side="left",padx=4); ttk.Button(anim_controls,text="Frame +",command=lambda:step(1)).pack(side="left"); ttk.Button(anim_controls,text="Import source",command=import_anim).pack(side="right")
    anim_list.bind("<<ListboxSelect>>",select_anim); refresh_anims()

    # Move selection tab: approved library -> explicit input/state slots.
    moves_tab=ttk.Frame(notebook,padding=10); notebook.add(moves_tab,text="Moveset")
    library_tree=ttk.Treeview(moves_tab,columns=("name","slot","startup","active","recovery"),show="headings",selectmode="browse"); library_tree.pack(side="left",fill="both",expand=True)
    for col,label,w in (("name","Approved move",220),("slot","Slot",170),("startup","Start",70),("active","Active",70),("recovery","Recovery",80)): library_tree.heading(col,text=label); library_tree.column(col,width=w)
    move_side=ttk.Frame(moves_tab,padding=(12,0)); move_side.pack(side="right",fill="y")
    ttk.Label(move_side,text="Assigned moves",font=("TkDefaultFont",12,"bold")).pack(anchor="w")
    assigned=tk.Listbox(move_side,width=36,height=15); assigned.pack(fill="y",pady=8)
    def refresh_moves():
        library_tree.delete(*library_tree.get_children()); assigned.delete(0,"end")
        for ident,m in controller.library().items(): library_tree.insert("","end",iid=ident,values=(m["display_name"],m["slot"],m["startup_frames"],m["active_frames"],m["recovery_frames"]))
        for m in controller.moves(): assigned.insert("end",f"{m.get('name')}  [{m.get('slot')}]  ref={m.get('reference','custom')}")
    def choose_move():
        sel=library_tree.selection()
        if not sel: return
        try: controller.select_library_move(sel[0]); refresh_moves(); report("Move assigned. Validate before export.")
        except Exception as exc: messagebox.showerror("Character Studio",str(exc))
    ttk.Button(move_side,text="Assign selected move",command=choose_move).pack(fill="x"); ttk.Label(move_side,text="Moves are copied by reference; the source library stays unchanged.",wraplength=240,style="Sub.TLabel").pack(anchor="w",pady=10); refresh_moves()

    # Attributes tab.
    attr_tab=ttk.Frame(notebook,padding=10); notebook.add(attr_tab,text="Attributes")
    attr_grid=ttk.Frame(attr_tab); attr_grid.pack(anchor="nw")
    entries={}; attrs=controller.editor.character.get("attributes",{})
    for row,key in enumerate(("walk_speed","dash_speed","run_speed","gravity","fall_speed","weight","size","air_speed","jump_velocity")):
        ttk.Label(attr_grid,text=key,width=20).grid(row=row,column=0,sticky="w",pady=3); var=tk.StringVar(value=str(attrs.get(key,""))); entries[key]=var; ttk.Entry(attr_grid,textvariable=var,width=16).grid(row=row,column=1,pady=3)
    def apply_attrs():
        try:
            for key,var in entries.items(): controller.set_attribute(key,float(var.get()))
            report("Attributes updated in memory. Validate before export.")
        except Exception as exc: messagebox.showerror("Character Studio",str(exc))
    ttk.Button(attr_grid,text="Apply attributes",command=apply_attrs).grid(row=10,column=0,columnspan=2,pady=12)
    ttk.Label(attr_tab,text="Units and ranges are validated by the core. Values are project data until exported.",style="Sub.TLabel").pack(anchor="nw",pady=12)

    validate()
    app.protocol("WM_DELETE_WINDOW",lambda: (asset_worker.close(),app.destroy()))
    app.mainloop()
