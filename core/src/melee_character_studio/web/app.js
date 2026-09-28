// Character Studio web editor. Rendering and interaction run here; every file
// operation (retarget, bake, export, PascalPatch) is done by studio_server.py.
import * as THREE from 'three';
import { OrbitControls } from './vendor/addons/OrbitControls.js';
import { TransformControls } from './vendor/addons/TransformControls.js';
import { koPercent, strongestHit, tumblePercent } from './knockback.js';

const $ = (s, root = document) => root.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) n.append(kid.nodeType ? kid : document.createTextNode(kid));
  return n;
};
const api = {
  async get(path) { const r = await fetch(path); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j; },
  async post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j;
  },
};
function status(text, kind = '') { const s = $('#status-text'); s.textContent = text; s.className = kind; }

// ------------------------------------------------------------------ state
let S = null;                                   // server state
let project = null;                             // {character, moveset, rig} editable copies
const history = { undo: [], redo: [] };
let unsaved = false;
let mode = 'fit';
let rigData = null;                             // retarget result (bind space)
let rigStale = true, rigRequest = null;
let movesInfo = null;
let anim = { name: null, frames: null, frame: 0, playing: false, speed: 1, acc: 0 };
let selectedLandmark = null, selectedSegment = 'head', heatSegment = null;
const brush = { radius: 2.5, strength: 0.6, erase: false, tool: 'grab', mirror: true };

const snapshot = () => JSON.stringify(project);
function markDirty() { unsaved = true; $('#save').textContent = 'Save •'; $('#status-right').textContent = 'Unsaved changes'; scheduleAutosave(); }
function pushUndo() { history.undo.push(snapshot()); if (history.undo.length > 150) history.undo.shift(); history.redo = []; markDirty(); }
function restore(text) { project = JSON.parse(text); markDirty(); rigStale = true; afterProjectChange(); }
function undo() { if (!history.undo.length) return; history.redo.push(snapshot()); restore(history.undo.pop()); status('Undo'); }
function redo() { if (!history.redo.length) return; history.undo.push(snapshot()); restore(history.redo.pop()); status('Redo'); }

// ------------------------------------------------------------------ three
const canvas = $('#viewport');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
renderer.outputColorSpace = THREE.SRGBColorSpace;
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 2000);
camera.position.set(0, 20, 60);
const orbit = new OrbitControls(camera, canvas);
orbit.enableDamping = true; orbit.dampingFactor = 0.12;
orbit.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN };
scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x2a2420, 1.4));
const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(20, 40, 30); scene.add(sun);
const rim = new THREE.DirectionalLight(0x88aaff, 0.6); rim.position.set(-30, 20, -30); scene.add(rim);
const grid = new THREE.GridHelper(80, 40, 0x3a414d, 0x262b33); scene.add(grid);

const gizmo = new TransformControls(camera, canvas);
gizmo.setSize(0.8);
scene.add(gizmo.getHelper());
gizmo.addEventListener('dragging-changed', (e) => { orbit.enabled = !e.value; });
gizmo.addEventListener('mouseDown', () => pushUndo());
gizmo.addEventListener('objectChange', () => {
  if (!selectedLandmark) return;
  const m = landmarkMeshes[selectedLandmark];
  if (gizmo.mode === 'rotate') {
    const q = m.quaternion; (project.rig.joint_rotations ||= {})[selectedLandmark] = [q.x, q.y, q.z, q.w].map(v => +v.toFixed(5));
    updateLandmarkReadout(); rigStale = true; return;
  }
  const p = m.position;
  project.rig.landmarks[selectedLandmark] = [+p.x.toFixed(3), +p.y.toFixed(3), +p.z.toFixed(3)];
  updateFitLines(); updateLandmarkReadout(); rigStale = true;
});
gizmo.addEventListener('mouseUp', () => scheduleRetarget());

const textureLoader = new THREE.TextureLoader();
let texture = null;
const sourceGroup = new THREE.Group(), rigGroup = new THREE.Group();
scene.add(sourceGroup, rigGroup);
let sourceMesh, rigMesh, boneLines, jointDots;
const landmarkMeshes = {};
let fitLines;
const segmentColor = {};

function makeMaterial(opts = {}) {
  return new THREE.MeshStandardMaterial({ map: $('#toggle-texture').checked && !opts.noTexture ? texture : null, roughness: 0.85, metalness: 0.0,
                                         side: THREE.DoubleSide, vertexColors: !!opts.vertexColors, transparent: !!opts.opacity, opacity: opts.opacity ?? 1,
                                         wireframe: $('#toggle-wire').checked });
}

// ------------------------------------------------------------------ geometry
function buildSource() {
  const m = S.model;
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(m.positions, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(m.normals, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(m.uvs, 2));
  g.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(m.positions.length).fill(1), 3));
  g.setIndex(m.indices);
  g.computeBoundingSphere();
  sourceMesh = new THREE.Mesh(g, makeMaterial());
  sourceGroup.add(sourceMesh);
  const radius = g.boundingSphere.radius * 0.022;
  const sphere = new THREE.SphereGeometry(radius, 18, 12);
  for (const name of landmarkNames()) {
    const mat = new THREE.MeshBasicMaterial({ color: 0xf2a33a, depthTest: false, transparent: true, opacity: 0.95 });
    const s = new THREE.Mesh(sphere, mat); s.renderOrder = 10; s.userData.landmark = name;
    const axes = new THREE.AxesHelper(radius * 4); axes.material.depthTest = false; axes.renderOrder = 11; s.add(axes);
    landmarkMeshes[name] = s; sourceGroup.add(s);
  }
  fitLines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x5fb3ff, depthTest: false, transparent: true, opacity: 0.8 }));
  fitLines.renderOrder = 9; sourceGroup.add(fitLines);
}

function landmarkNames() {
  const names = new Set();
  for (const s of S.base.segments) { names.add(s.start); names.add(s.end); }
  names.delete('upper_chest');
  return [...names];
}

function placeLandmarks() {
  for (const [name, mesh] of Object.entries(landmarkMeshes)) {
    const p = project.rig.landmarks[name];
    if (p) mesh.position.set(p[0], p[1], p[2]);
    const q = (project.rig.joint_rotations || {})[name];
    if (q) mesh.quaternion.set(q[0], q[1], q[2], q[3]); else mesh.quaternion.identity();
    mesh.material.color.set(name === selectedLandmark ? 0xffffff : (name.startsWith('l_') ? 0x5fb3ff : name.startsWith('r_') ? 0xe0503a : 0xf2a33a));
  }
  updateFitLines();
}

function landmarkPos(name) {
  const lm = project.rig.landmarks;
  if (name === 'upper_chest' && !lm.upper_chest) {
    const n = lm.neck, c = lm.chest; return [n[0] + (c[0] - n[0]) * 0.3, n[1] + (c[1] - n[1]) * 0.3, n[2] + (c[2] - n[2]) * 0.3];
  }
  return lm[name];
}

function updateFitLines() {
  const pts = [];
  for (const s of S.base.segments) { const a = landmarkPos(s.start), b = landmarkPos(s.end); if (a && b) pts.push(...a, ...b); }
  fitLines.geometry.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
}

function buildRig() {
  const g = new THREE.BufferGeometry();
  const n = S.model.positions.length;
  g.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(n), 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(new Float32Array(n), 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(S.model.uvs, 2));
  g.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(n).fill(1), 3));
  g.setIndex(S.model.indices);
  rigMesh = new THREE.Mesh(g, makeMaterial());
  rigGroup.add(rigMesh);
  boneLines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x7CFC9A, depthTest: false, transparent: true, opacity: 0.85 }));
  boneLines.renderOrder = 9;
  jointDots = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial({ color: 0xb6ffc9, size: 5, sizeAttenuation: false, depthTest: false }));
  jointDots.renderOrder = 10;
  rigGroup.add(boneLines, jointDots);
}

function colorForSegment(name) {
  if (!segmentColor[name]) {
    const i = S.base.segments.findIndex(s => s.name === name);
    segmentColor[name] = new THREE.Color().setHSL((i * 0.618034) % 1, 0.65, 0.55);
  }
  return segmentColor[name];
}

// Vertex colors for body-part painting (source) or weight heat (rig).
function paintColors() {
  const c = sourceMesh.geometry.attributes.color, overrides = project.rig.segment_overrides || {};
  for (let v = 0; v < c.count; v++) {
    const seg = overrides[v] || (rigData && rigData.segments[v]) || 'head';
    const col = colorForSegment(seg); const bright = overrides[v] ? 1 : 0.8;
    c.setXYZ(v, col.r * bright + (1 - bright) * 0.3, col.g * bright + (1 - bright) * 0.3, col.b * bright + (1 - bright) * 0.3);
  }
  c.needsUpdate = true;
}

function heatColors() {
  const c = rigMesh.geometry.attributes.color;
  if (!heatSegment || !rigData) { for (let v = 0; v < c.count; v++) c.setXYZ(v, 1, 1, 1); c.needsUpdate = true; return; }
  const owner = S.base.segments.find(s => s.name === heatSegment).owner;
  for (let v = 0; v < c.count; v++) {
    const w = (rigData.weights[v].find(x => x[0] === owner) || [0, 0])[1];
    const col = new THREE.Color().setHSL(0.66 - 0.66 * w, 0.9, 0.35 + 0.25 * w);
    c.setXYZ(v, col.r, col.g, col.b);
  }
  c.needsUpdate = true;
}

// ------------------------------------------------------------------ rig evaluation
const offsetsOf = () => (project.rig.vertex_offsets ||= {});

function bindPositions() {
  const base = rigData.positions, off = offsetsOf(), out = new Float32Array(base.length);
  out.set(base);
  for (const [v, d] of Object.entries(off)) { const i = +v * 3; out[i] += d[0]; out[i + 1] += d[1]; out[i + 2] += d[2]; }
  return out;
}

function mul4(a, b) {
  const o = new Array(16);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) o[r * 4 + c] = a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] + a[r * 4 + 2] * b[8 + c] + a[r * 4 + 3] * b[12 + c];
  return o;
}

let lockRoot = true;
// One animation frame's joint matrices; TopN translation is root motion the engine turns into
// movement, so with lockRoot the model stays in place.
function frameMatrices(f, lock = lockRoot) {
  const J = S.base.joints.length, out = [];
  const dx = lock ? f[1 * 16 + 3] : 0, dz = lock ? f[1 * 16 + 11] : 0;
  for (let j = 0; j < J; j++) { const m = f.slice(j * 16, j * 16 + 16); m[3] -= dx; m[11] -= dz; out.push(m); }
  return out;
}
function worldMatrices() {
  if (mode === 'animate' && anim.frames) return frameMatrices(anim.frames[anim.frame]);
  return S.base.joints.map(j => j.bind);
}

// The rigged model's vertices and normals posed by `world` (bind pose when null).
function skinVertices(world, pos, nrm) {
  const bind = bindPositions(), bn = rigData.normals, n = bind.length / 3;
  const skin = world && world.map((w, j) => mul4(w, S.base.joints[j].inverse));
  for (let v = 0; v < n; v++) {
    const x = bind[v * 3], y = bind[v * 3 + 1], z = bind[v * 3 + 2];
    if (!skin) { pos.setXYZ(v, x, y, z); nrm.setXYZ(v, bn[v * 3], bn[v * 3 + 1], bn[v * 3 + 2]); continue; }
    let px = 0, py = 0, pz = 0, nx = 0, ny = 0, nz = 0;
    const nx0 = bn[v * 3], ny0 = bn[v * 3 + 1], nz0 = bn[v * 3 + 2];
    for (const [j, w] of rigData.weights[v]) {
      const m = skin[j];
      px += w * (m[0] * x + m[1] * y + m[2] * z + m[3]); py += w * (m[4] * x + m[5] * y + m[6] * z + m[7]); pz += w * (m[8] * x + m[9] * y + m[10] * z + m[11]);
      nx += w * (m[0] * nx0 + m[1] * ny0 + m[2] * nz0); ny += w * (m[4] * nx0 + m[5] * ny0 + m[6] * nz0); nz += w * (m[8] * nx0 + m[9] * ny0 + m[10] * nz0);
    }
    const l = Math.hypot(nx, ny, nz) || 1;
    pos.setXYZ(v, px, py, pz); nrm.setXYZ(v, nx / l, ny / l, nz / l);
  }
  pos.needsUpdate = true; nrm.needsUpdate = true;
}

function updateRigMesh() {
  if (!rigData) return;
  const world = worldMatrices();
  skinVertices(mode === 'animate' && anim.frames ? world : null, rigMesh.geometry.attributes.position, rigMesh.geometry.attributes.normal);
  rigMesh.geometry.computeBoundingSphere(); rigMesh.geometry.computeBoundingBox();
  // bones
  const lines = [], dots = [];
  S.base.joints.forEach((j, i) => {
    const m = world[i]; dots.push(m[3], m[7], m[11]);
    const b = j.bind; const atOrigin = Math.hypot(b[3], b[7], b[11]) < 0.01;
    if (j.parent !== null && j.parent !== undefined && j.parent > 1 && !atOrigin) { const p = world[j.parent]; lines.push(p[3], p[7], p[11], m[3], m[7], m[11]); }
  });
  boneLines.geometry.setAttribute('position', new THREE.Float32BufferAttribute(lines, 3));
  jointDots.geometry.setAttribute('position', new THREE.Float32BufferAttribute(dots, 3));
}

function rigRequestBody() {
  const r = project.rig;
  // everything retarget_mesh reads, so the preview matches the built costume
  return { landmarks: r.landmarks, segment_overrides: r.segment_overrides || {}, segment_scale: r.segment_scale || {}, joint_rotations: r.joint_rotations || {},
           segment_modes: r.segment_modes || null, segment_ranges: r.segment_ranges || null, rigid: r.rigid || null, scale_factor: r.scale_factor ?? 1.0 };
}

let retargetTimer = null;
function scheduleRetarget(delay = 250) {
  rigStale = true; clearTimeout(retargetTimer);
  retargetTimer = setTimeout(() => ensureRig(), delay);
}

async function ensureRig() {
  if (!rigStale && rigData) return rigData;
  const body = rigRequestBody(), key = JSON.stringify(body);
  if (rigRequest && rigRequest.key === key) return rigRequest.promise;
  status('Retargeting…');
  const promise = api.post('/api/retarget', body).then((r) => {
    rigData = r; rigStale = JSON.stringify(rigRequestBody()) !== key;
    buildMirrorMap(); updateRigMesh(); heatColors();
    if (mode === 'paint') paintColors();
    status(`Rig ready · scale ${r.scale.toFixed(3)} · ${r.positions.length / 3} vertices`, 'ok');
    renderPanel(true);
    return r;
  }).catch((e) => { status('Retarget failed: ' + e.message, 'err'); throw e; }).finally(() => { rigRequest = null; });
  rigRequest = { key, promise };
  return promise;
}

// mirror map for symmetric sculpting (bind space, across x = 0)
let mirror = null;
function buildMirrorMap() {
  const p = rigData.positions, n = p.length / 3, cell = 0.4, hash = new Map();
  const key = (x, y, z) => `${Math.round(x / cell)},${Math.round(y / cell)},${Math.round(z / cell)}`;
  for (let v = 0; v < n; v++) { const k = key(p[v * 3], p[v * 3 + 1], p[v * 3 + 2]); if (!hash.has(k)) hash.set(k, []); hash.get(k).push(v); }
  mirror = new Int32Array(n).fill(-1);
  for (let v = 0; v < n; v++) {
    const x = -p[v * 3], y = p[v * 3 + 1], z = p[v * 3 + 2]; let best = -1, bd = 0.35 * 0.35;
    const cx = Math.round(x / cell), cy = Math.round(y / cell), cz = Math.round(z / cell);
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      for (const u of hash.get(`${cx + dx},${cy + dy},${cz + dz}`) || []) {
        const d = (p[u * 3] - x) ** 2 + (p[u * 3 + 1] - y) ** 2 + (p[u * 3 + 2] - z) ** 2; if (d < bd) { bd = d; best = u; }
      }
    }
    mirror[v] = best;
  }
}

// ------------------------------------------------------------------ interaction
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
function setPointer(e) { const r = canvas.getBoundingClientRect(); pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); raycaster.setFromCamera(pointer, camera); }

let stroke = null;
canvas.addEventListener('pointerdown', (e) => {
  if (e.button !== 0 || e.altKey) return;
  setPointer(e);
  if (mode === 'fit') {
    const hit = raycaster.intersectObjects(Object.values(landmarkMeshes), false)[0];
    if (hit) { selectLandmark(hit.object.userData.landmark); orbit.enabled = false; stroke = { kind: 'select' }; }
    return;
  }
  if (mode === 'paint' || mode === 'sculpt') {
    const target = mode === 'paint' ? sourceMesh : rigMesh;
    const hit = raycaster.intersectObject(target, false)[0];
    if (!hit) return;
    orbit.enabled = false; pushUndo();
    stroke = { kind: mode, last: hit.point.clone(), plane: new THREE.Plane().setFromNormalAndCoplanarPoint(camera.getWorldDirection(new THREE.Vector3()).negate(), hit.point) };
    if (mode === 'paint') paintAt(hit.point);
    else if (brush.tool !== 'grab') sculptAt(hit.point, null);
    else stroke.affected = gather(hit.point);
    canvas.setPointerCapture(e.pointerId);
  }
}, { capture: true });

canvas.addEventListener('pointermove', (e) => {
  setPointer(e); updateBrushRing(e);
  if (!stroke) return;
  if (stroke.kind === 'paint') { const hit = raycaster.intersectObject(sourceMesh, false)[0]; if (hit) paintAt(hit.point); }
  if (stroke.kind === 'sculpt') {
    if (brush.tool === 'grab') {
      const p = new THREE.Vector3(); if (!raycaster.ray.intersectPlane(stroke.plane, p)) return;
      const d = p.clone().sub(stroke.last); stroke.last.copy(p); sculptAt(null, d);
    } else { const hit = raycaster.intersectObject(rigMesh, false)[0]; if (hit) sculptAt(hit.point, null); }
  }
});
window.addEventListener('pointerup', () => {
  if (!stroke) return;
  const kind = stroke.kind; stroke = null; orbit.enabled = true;
  if (kind === 'paint') scheduleRetarget(50);
});

function updateBrushRing(e) {
  const ring = $('#brush-ring');
  if (!(mode === 'paint' || mode === 'sculpt')) { ring.hidden = true; return; }
  const target = mode === 'paint' ? sourceMesh : rigMesh; const hit = raycaster.intersectObject(target, false)[0];
  if (!hit) { ring.hidden = true; return; }
  const a = hit.point.clone().project(camera);
  const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0).multiplyScalar(brush.radius);
  const b = hit.point.clone().add(right).project(camera);
  const r = canvas.getBoundingClientRect(); const px = Math.abs((b.x - a.x) * r.width / 2);
  ring.hidden = false; ring.style.left = `${e.clientX - r.left}px`; ring.style.top = `${e.clientY - r.top}px`;
  ring.style.width = ring.style.height = `${px * 2}px`;
}

function paintAt(point) {
  const pos = sourceMesh.geometry.attributes.position, r2 = brush.radius ** 2, ov = (project.rig.segment_overrides ||= {});
  let n = 0;
  for (let v = 0; v < pos.count; v++) {
    const d = (pos.getX(v) - point.x) ** 2 + (pos.getY(v) - point.y) ** 2 + (pos.getZ(v) - point.z) ** 2;
    if (d <= r2) { if (brush.erase) delete ov[v]; else ov[v] = selectedSegment; n++; }
  }
  if (n) { paintColors(); rigStale = true; }
}

function gather(point) {
  const bind = bindPositions(), out = [], r = brush.radius;
  for (let v = 0; v < bind.length / 3; v++) {
    const d = Math.hypot(bind[v * 3] - point.x, bind[v * 3 + 1] - point.y, bind[v * 3 + 2] - point.z);
    if (d < r) out.push([v, (1 - (d / r) ** 2) ** 2]);
  }
  return out;
}

function addOffset(v, dx, dy, dz) {
  const off = offsetsOf(); const o = off[v] || [0, 0, 0];
  o[0] += dx; o[1] += dy; o[2] += dz; off[v] = o.map(x => +x.toFixed(4));
}

function sculptAt(point, delta) {
  if (!rigData) return;
  if (brush.tool === 'grab') {
    for (const [v, f] of stroke.affected) {
      const k = f * brush.strength * 1.6; addOffset(v, delta.x * k, delta.y * k, delta.z * k);
      if (brush.mirror && mirror[v] >= 0 && mirror[v] !== v) addOffset(mirror[v], -delta.x * k, delta.y * k, delta.z * k);
    }
  } else {
    const bind = bindPositions(), affected = gather(point);
    if (brush.tool === 'smooth') {
      let cx = 0, cy = 0, cz = 0, tw = 0;
      for (const [v, f] of affected) { cx += bind[v * 3] * f; cy += bind[v * 3 + 1] * f; cz += bind[v * 3 + 2] * f; tw += f; }
      if (!tw) return; cx /= tw; cy /= tw; cz /= tw;
      for (const [v, f] of affected) { const k = f * brush.strength * 0.15; addOffset(v, (cx - bind[v * 3]) * k, (cy - bind[v * 3 + 1]) * k, (cz - bind[v * 3 + 2]) * k); }
    } else {  // inflate / deflate along normals
      const sign = brush.tool === 'deflate' ? -1 : 1, n = rigData.normals;
      for (const [v, f] of affected) { const k = sign * f * brush.strength * 0.04; addOffset(v, n[v * 3] * k, n[v * 3 + 1] * k, n[v * 3 + 2] * k); }
    }
  }
  updateRigMesh();
}

function setGizmoMode(m) {
  if (m === 'rotate' && !S.base.segments.some(s => s.start === selectedLandmark)) return;
  gizmo.setMode(m); gizmo.setSpace(m === 'rotate' ? 'local' : 'world'); renderPanel();
}

function selectLandmark(name) {
  selectedLandmark = name;
  if (gizmo.mode === 'rotate' && !S.base.segments.some(s => s.start === name)) { gizmo.setMode('translate'); gizmo.setSpace('world'); }
  if (name) gizmo.attach(landmarkMeshes[name]); else gizmo.detach();
  placeLandmarks(); renderPanel();
}

// ------------------------------------------------------------------ camera
function frameObject(obj) {
  const box = new THREE.Box3().setFromObject(obj); if (box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3()).length();
  const dir = camera.position.clone().sub(orbit.target).normalize();
  orbit.target.copy(c); camera.position.copy(c).add(dir.multiplyScalar(size * 1.35));
  camera.near = size / 200; camera.far = size * 40; camera.updateProjectionMatrix();
  grid.position.y = box.min.y; grid.scale.setScalar(size / 50);
}
function viewFrom(axis) {
  const obj = mode === 'fit' || mode === 'paint' ? sourceMesh : rigMesh; frameObject(obj);
  const d = camera.position.distanceTo(orbit.target);
  camera.position.copy(orbit.target).add(axis === 'side' ? new THREE.Vector3(d, 0, 0) : new THREE.Vector3(0, 0, d));
}

// ------------------------------------------------------------------ modes
const MODES = { fit: 'Fit landmarks', paint: 'Body parts', sculpt: 'Sculpt', animate: 'Animate', moves: 'Moves', stats: 'Stats', export: 'Build' };
async function setMode(next) {
  const prevSpace = mode === 'fit' || mode === 'paint';
  mode = next; stroke = null;
  document.querySelectorAll('#modes button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  $('#mode-select').value = mode;
  $('#hud-mode').textContent = MODES[mode];
  const hints = {
    fit: 'Click a joint dot or pick one in the panel, then drag the gizmo. Drag empty space to orbit, right-drag to pan.',
    paint: 'Pick a body part in the panel, then paint on the model. Alt+drag to orbit.',
    sculpt: 'Drag on the model to reshape it. Alt+drag to orbit, [ ] brush size.',
    animate: 'Pick an animation in the panel. Space plays and pauses.',
    moves: 'Pick specials and borrowed attacks, then tune hitboxes. Preview shows a move on your model.',
    stats: 'Tick a stat to change it from the base fighter.',
    export: 'Build the character, then launch the game offline.',
  };
  $('#hud-hint').textContent = hints[mode] || '';
  const sourceSpace = mode === 'fit' || mode === 'paint';
  sourceGroup.visible = sourceSpace; rigGroup.visible = !sourceSpace;
  gizmo.enabled = mode === 'fit'; gizmo.getHelper().visible = mode === 'fit' && !!selectedLandmark;
  for (const m of Object.values(landmarkMeshes)) m.visible = mode === 'fit';
  fitLines.visible = mode === 'fit';
  $('#timeline').hidden = mode !== 'animate';
  applyMaterials();
  if (!sourceSpace) {
    await ensureRig().catch(() => {});
    if (mode === 'animate' && !anim.frames) await loadAnimation(anim.name || S.animations.find(a => a === 'Wait1') || S.animations[0]);
    updateRigMesh();
  }
  if (mode === 'paint') { paintColors(); if (rigStale) ensureRig().catch(() => {}); }
  if (sourceSpace !== prevSpace || !framedOnce[sourceSpace]) { frameObject(sourceSpace ? sourceMesh : rigMesh); framedOnce[sourceSpace] = true; }
  if ((mode === 'moves') && !movesInfo) { movesInfo = await api.post('/api/moves', { moveset: project.moveset }).catch(e => ({ error: e.message })); }
  renderPanel();
}
const framedOnce = {};

function applyMaterials() {
  const paint = mode === 'paint', heat = mode === 'animate' && heatSegment;
  sourceMesh.material.dispose(); sourceMesh.material = makeMaterial({ vertexColors: paint, noTexture: paint && !paintShowTexture, opacity: mode === 'fit' ? (xray ? 0.45 : undefined) : undefined });
  if (mode === 'fit' && xray) sourceMesh.material.depthWrite = false;
  rigMesh.material.dispose(); rigMesh.material = makeMaterial({ vertexColors: !!heat });
  boneLines.visible = jointDots.visible = $('#toggle-bones').checked;
}
let xray = true, paintShowTexture = false;

// ------------------------------------------------------------------ animation
async function loadAnimation(name, url) {
  status(`Loading ${name}…`);
  const r = await api.get(url || ('/api/animation?name=' + encodeURIComponent(name)));
  anim = { ...anim, name, frames: r.frames, frame: 0, acc: 0 };
  $('#frame').max = r.frames.length - 1; $('#frame').value = 0;
  status(`${name}: ${r.frames.length} frames`, 'ok');
  updateRigMesh(); updateFrameLabel();
}
function updateFrameLabel() { $('#frame-label').textContent = anim.frames ? `${anim.frame} / ${anim.frames.length - 1}` : '0 / 0'; }
function setPlaying(p) { anim.playing = p; $('#play').textContent = p ? '❚❚' : '▶'; }

// ------------------------------------------------------------------ panels
function row(label, input, value) { return el('div', { class: 'row' }, el('label', {}, label), input, value ? el('span', { class: 'val' }, value) : null); }

function rangeRow(label, min, max, step, value, onInput, fmt = (v) => (+v).toFixed(2)) {
  const val = el('span', { class: 'val' }, fmt(value));
  const input = el('input', { type: 'range', min, max, step, value, oninput: (e) => { val.textContent = fmt(e.target.value); onInput(+e.target.value, e); } });
  return el('div', { class: 'row' }, el('label', {}, label), input, val);
}

// A dropdown. Items are [value, label, disabled?] or {group, items}.
function dropdown(items, value, onChange, attrs = {}) {
  const opt = ([v, l, off]) => el('option', { value: v, selected: String(v) === String(value ?? ''), disabled: !!off }, l);
  return el('select', { ...attrs, onchange: (e) => onChange(e.target.value, e) },
    ...items.map(it => it.group ? (it.items.length ? el('optgroup', { label: it.group }, ...it.items.map(opt)) : null) : opt(it)));
}

const PART_WORDS = { clav: 'collarbone', upper: 'upper arm', fore: 'forearm', thigh: 'thigh', shin: 'shin', upper_chest: 'upper chest' };
const pretty = (s) => { const side = s.startsWith('l_') ? 'left ' : s.startsWith('r_') ? 'right ' : ''; const rest = side ? s.slice(2) : s;
  return (side + (PART_WORDS[rest] || rest.replace(/_/g, ' '))).replace(/^./, c => c.toUpperCase()); };
const fighterLabel = (id) => (S.options.fighters.find(f => f.id === id) || { label: id }).label;
const slotInfo = (id) => S.options.slots.find(s => s.id === id);
const slotLabel = (id) => slotInfo(id)?.label || (id.startsWith('borrow_') ? `Borrowed ${actionLabel(id.slice(7), false)}` : pretty(id));

const ACTION_NAMES = {
  Attack11: 'Jab 1', Attack12: 'Jab 2', Attack13: 'Jab 3', Attack100Start: 'Rapid jab (start)', Attack100Loop: 'Rapid jab', Attack100End: 'Rapid jab (end)',
  AttackDash: 'Dash attack', AttackS3Hi: 'Forward tilt (high)', AttackS3HiS: 'Forward tilt (mid-high)', AttackS3S: 'Forward tilt',
  AttackS3LwS: 'Forward tilt (mid-low)', AttackS3Lw: 'Forward tilt (low)', AttackHi3: 'Up tilt', AttackLw3: 'Down tilt',
  AttackS4Hi: 'Forward smash (high)', AttackS4HiS: 'Forward smash (mid-high)', AttackS4S: 'Forward smash', AttackS4LwS: 'Forward smash (mid-low)',
  AttackS4Lw: 'Forward smash (low)', AttackHi4: 'Up smash', AttackLw4: 'Down smash', AttackAirN: 'Neutral air', AttackAirF: 'Forward air',
  AttackAirB: 'Back air', AttackAirHi: 'Up air', AttackAirLw: 'Down air',
};
function actionLabel(a, withCode = true) {
  const tail = withCode ? ` · ${a}` : '';
  if (a.endsWith('*')) return `Every ${actionLabel(a.slice(0, -1), false)} action${tail}`;
  if (ACTION_NAMES[a]) return ACTION_NAMES[a] + tail;
  const m = a.match(/^Special(Air)?(N|S|Hi|Lw)(.*)$/);
  if (m) return `${{ N: 'Neutral', S: 'Side', Hi: 'Up', Lw: 'Down' }[m[2]]} B${m[1] ? ' in the air' : ''}${m[3] ? ` (${m[3].replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase()})` : ''}${tail}`;
  return a;
}
function actionGroups(actions, current) {
  const patterns = [...new Set(S.options.slots.flatMap(s => s.actions).filter(a => a.includes('*')))];
  const groups = [
    { group: 'Ground attacks', items: actions.filter(a => a.startsWith('Attack') && !a.startsWith('AttackAir')) },
    { group: 'Aerials', items: actions.filter(a => a.startsWith('AttackAir')) },
    { group: 'Specials', items: actions.filter(a => a.startsWith('Special')) },
    { group: 'Whole special (every part)', items: patterns },
    { group: 'Other', items: actions.filter(a => !a.startsWith('Attack') && !a.startsWith('Special')) },
  ].map(g => ({ group: g.group, items: g.items.map(a => [a, actionLabel(a)]) }));
  if (current && !actions.includes(current) && !patterns.includes(current)) groups.unshift({ group: 'Current', items: [[current, actionLabel(current)]] });
  return groups;
}

function renderPanel(soft = false) {
  const p = $('#panel');
  if (soft && (mode === 'moves' || mode === 'stats' || mode === 'export')) return;
  const scroll = p.scrollTop;
  p.replaceChildren();
  ({ fit: panelFit, paint: panelPaint, sculpt: panelSculpt, animate: panelAnimate, moves: panelMoves, stats: panelStats, export: panelExport })[mode](p);
  p.scrollTop = scroll;
}

function updateLandmarkReadout() {
  const r = $('#lm-readout'); if (!r || !selectedLandmark) return;
  const q = project.rig.landmarks[selectedLandmark];
  const rot = (project.rig.joint_rotations || {})[selectedLandmark];
  let deg = '0°, 0°, 0°';
  if (rot) { const e = new THREE.Euler().setFromQuaternion(new THREE.Quaternion(...rot)); deg = [e.x, e.y, e.z].map(a => `${THREE.MathUtils.radToDeg(a).toFixed(1)}°`).join(', '); }
  r.textContent = `Position ${q.map(x => x.toFixed(2)).join(', ')} · rotation ${deg}`;
}

function snapLandmark(name) {
  const pos = sourceMesh.geometry.attributes.position, c = project.rig.landmarks[name]; const r = 2.2;
  let sx = 0, sy = 0, sz = 0, n = 0;
  for (let v = 0; v < pos.count; v++) {
    const x = pos.getX(v), y = pos.getY(v), z = pos.getZ(v);
    if ((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2 < r * r) { sx += x; sy += y; sz += z; n++; }
  }
  if (n < 3) { status('Too few vertices near this joint to snap', 'err'); return; }
  pushUndo(); project.rig.landmarks[name] = [+(sx / n).toFixed(3), +(sy / n).toFixed(3), +(sz / n).toFixed(3)];
  placeLandmarks(); updateLandmarkReadout(); scheduleRetarget();
}

function panelFit(p) {
  p.append(el('h2', {}, 'Fit the skeleton'),
    el('p', {}, 'Each dot is a joint of the ', el('b', {}, fighterLabel(S.base.fighter)), ' skeleton, placed on your model. Put them where the model bends: shoulders, elbows, wrists, hips, knees, ankles. Blue = left, red = right.'));
  if (project.rig.landmarks_estimated) {
    p.append(el('div', { class: 'estimate' }, el('b', {}, 'First guess. '),
      'These joints were placed automatically from the model\'s shape. Check each one, especially elbows, wrists and knees, then mark them as checked.',
      el('div', { class: 'row' }, el('button', { onclick: () => { pushUndo(); delete project.rig.landmarks_estimated; renderPanel(); } }, 'Joints checked'))));
  }
  const names = landmarkNames();
  const side = (pre) => names.filter(n => n.startsWith(pre)).map(n => [n, pretty(n)]);
  p.append(row('Joint', dropdown([['', 'Choose a joint (or click one)…'],
    { group: 'Centre', items: names.filter(n => !/^[lr]_/.test(n)).map(n => [n, pretty(n)]) },
    { group: 'Left side', items: side('l_') }, { group: 'Right side', items: side('r_') }], selectedLandmark, (v) => selectLandmark(v || null))));
  if (selectedLandmark) {
    const ownsPart = S.base.segments.some(s => s.start === selectedLandmark);
    p.append(el('div', { class: 'card' }, el('div', { id: 'lm-readout', class: 'muted' }),
      row('Gizmo', dropdown([['translate', 'Move (W)'], ['rotate', ownsPart ? 'Rotate body part (E)' : 'Rotate (no body part starts here)', !ownsPart]], gizmo.mode, (v) => setGizmoMode(v))),
      ownsPart ? el('p', {}, `Rotating turns the ${S.base.segments.filter(s => s.start === selectedLandmark).map(s => pretty(s.name).toLowerCase()).join(' + ')} before fitting. Use it when hands, feet or head are modelled at a different angle.`) : null,
      el('div', { class: 'row' },
        el('button', { onclick: () => snapLandmark(selectedLandmark), title: 'Move to the centre of the nearby geometry' }, 'Snap to limb centre'),
        el('button', { disabled: !(project.rig.joint_rotations || {})[selectedLandmark], onclick: () => { pushUndo(); delete project.rig.joint_rotations[selectedLandmark]; placeLandmarks(); updateLandmarkReadout(); scheduleRetarget(); renderPanel(); } }, 'Reset rotation'),
        el('button', { onclick: () => selectLandmark(null) }, 'Done'))));
    updateLandmarkReadout();
  }
  p.append(el('label', { class: 'row check' }, el('input', { type: 'checkbox', checked: xray, onchange: (e) => { xray = e.target.checked; applyMaterials(); } }), 'X-ray model (see joints inside)'));
  const scales = (project.rig.segment_scale ||= {});
  const sizes = el('details', { class: 'card' }, el('summary', {}, `Body part sizes${Object.keys(scales).length ? ` (${Object.keys(scales).length} changed)` : ''}`),
    el('p', {}, 'Scale a part around its joint after fitting (1.00 = automatic).'));
  for (const s of S.base.segments) {
    sizes.append(rangeRow(pretty(s.name), 0.5, 1.6, 0.01, scales[s.name] ?? 1, (v, e) => {
      if (e.type === 'input' && !e.target.dataset.pushed) { pushUndo(); e.target.dataset.pushed = 1; setTimeout(() => delete e.target.dataset.pushed, 600); }
      if (Math.abs(v - 1) < 1e-6) delete scales[s.name]; else scales[s.name] = v; scheduleRetarget(400);
    }));
  }
  sizes.append(el('button', { disabled: !Object.keys(scales).length, onclick: () => { pushUndo(); project.rig.segment_scale = {}; scheduleRetarget(); renderPanel(); } }, 'Reset all sizes'));
  p.append(sizes, el('p', { class: 'small' }, `Model is turned ${project.rig.rotate_y_degrees || 0}° to face the camera (rig.json rotate_y_degrees); changing it means refitting the joints.`));
}

function panelPaint(p) {
  p.append(el('h2', {}, 'Body parts'),
    el('p', {}, 'Every area of the model follows one body part. The automatic pass picks the nearest bone; paint over areas that follow the wrong part (baggy clothes, hair, capes). Bright = painted, dim = automatic.'));
  const swatch = el('span', { class: 'swatch big', style: `background:#${colorForSegment(selectedSegment).getHexString()}` });
  p.append(el('div', { class: 'row' }, el('label', {}, 'Body part'),
    dropdown(S.base.segments.map(s => [s.name, pretty(s.name)]), selectedSegment, (v) => { selectedSegment = v; brush.erase = false; renderPanel(); }), swatch),
    row('Brush', dropdown([['paint', 'Paint this body part'], ['erase', 'Erase (back to automatic)']], brush.erase ? 'erase' : 'paint', (v) => { brush.erase = v === 'erase'; })),
    rangeRow('Brush radius', 0.3, 8, 0.1, brush.radius, (v) => { brush.radius = v; }),
    el('label', { class: 'row check' }, el('input', { type: 'checkbox', checked: paintShowTexture, onchange: (e) => { paintShowTexture = e.target.checked; applyMaterials(); } }), 'Show texture under the colours'),
    el('div', { class: 'row' }, el('span', { class: 'muted' }, `${Object.keys(project.rig.segment_overrides || {}).length} painted vertices`),
      el('button', { onclick: () => { if (!confirm('Clear all painted areas?')) return; pushUndo(); project.rig.segment_overrides = {}; paintColors(); scheduleRetarget(); renderPanel(); } }, 'Clear painting')));
}

const SCULPT_TOOLS = [['grab', 'Grab — drag an area'], ['smooth', 'Smooth — even out bumps'], ['inflate', 'Inflate — puff outward'], ['deflate', 'Deflate — pull inward']];
function panelSculpt(p) {
  p.append(el('h2', {}, 'Sculpt'),
    el('p', {}, 'Reshape the model as it sits on the skeleton. Edits are stored on top of the fit, so refitting keeps them. Alt+drag orbits; [ and ] change the brush size.'),
    row('Tool', dropdown(SCULPT_TOOLS, brush.tool, (v) => { brush.tool = v; })),
    rangeRow('Radius', 0.2, 6, 0.05, brush.radius, (v) => { brush.radius = v; }),
    rangeRow('Strength', 0.05, 1, 0.01, brush.strength, (v) => { brush.strength = v; }),
    el('label', { class: 'row check' }, el('input', { type: 'checkbox', checked: brush.mirror, onchange: (e) => { brush.mirror = e.target.checked; } }), 'Mirror left ↔ right'),
    el('div', { class: 'row' }, el('span', { class: 'muted' }, `${Object.keys(project.rig.vertex_offsets || {}).length} sculpted vertices`),
      el('button', { onclick: () => { if (!confirm('Undo all sculpting?')) return; pushUndo(); project.rig.vertex_offsets = {}; updateRigMesh(); renderPanel(); } }, 'Reset sculpt')));
}

const ANIM_GROUPS = [
  ['Idle & movement', /^(Wait|Walk|Dash|Run|Turn|Jump|Fall|Landing(?!Air)|Squat|Pass|Ottotto|Kneebend|Appeal|Entry|Guard|Escape|Rebirth)/],
  ['Ground attacks', /^Attack(?!Air)/], ['Aerials', /^(AttackAir|LandingAir)/], ['Specials', /^Special/],
  ['Grabs & throws', /^(Catch|Throw|Capture)/], ['Hit & knockdown', /^(Damage|Down|Dead|Heavy|Swing|Item|Fura|Miss|Cliff|Passive|Stop|Wall|Bury|Bound)/],
];
function animationItems() {
  const groups = ANIM_GROUPS.map(([g]) => ({ group: g, items: [] })), other = { group: 'Other', items: [] };
  for (const name of S.animations) {
    const i = ANIM_GROUPS.findIndex(([, re]) => re.test(name));
    (i >= 0 ? groups[i] : other).items.push([name, ACTION_NAMES[name] ? `${ACTION_NAMES[name]} · ${name}` : name]);
  }
  const out = [...groups, other];
  if (anim.name && !S.animations.includes(anim.name)) out.unshift({ group: 'Preview', items: [[anim.name, `${anim.name} (retargeted)`]] });
  return out;
}
async function stepAnimation(d) {
  const i = S.animations.indexOf(anim.name), next = S.animations[(i + d + S.animations.length) % S.animations.length];
  await loadAnimation(next); setPlaying(true); renderPanel();
}
function panelAnimate(p) {
  p.append(el('h2', {}, 'Animate'), el('p', {}, `Real ${fighterLabel(S.base.fighter)} animations from your disc, played on your model exactly as the game interpolates them. Space plays and pauses.`));
  p.append(el('div', { class: 'row' }, el('label', {}, 'Animation'),
    dropdown(animationItems(), anim.name, async (v) => { await loadAnimation(v); setPlaying(true); renderPanel(); }, { style: 'flex:1;min-width:0' })),
    el('div', { class: 'row' }, el('label', {}, ''), el('button', { onclick: () => stepAnimation(-1), title: 'Previous animation' }, '◀ Previous'),
      el('button', { onclick: () => stepAnimation(1), title: 'Next animation' }, 'Next ▶')));
  p.append(el('h3', {}, 'Display'),
    row('Colouring', dropdown([['', 'Texture'], { group: 'Show how strongly a part moves the model', items: S.base.segments.map(s => [s.name, `Weights: ${pretty(s.name)}`]) }],
      heatSegment || '', (v) => { heatSegment = v || null; applyMaterials(); heatColors(); })),
    el('label', { class: 'row check' }, el('input', { type: 'checkbox', checked: lockRoot, onchange: (e) => { lockRoot = e.target.checked; updateRigMesh(); } }), 'Keep the model in place (ignore root motion)'));
  if (anim.name && anim.name.includes(':')) p.append(el('p', {}, `Previewing ${anim.name.replace(':', "'s ")}, retargeted onto ${fighterLabel(S.base.fighter)} and decoded from the animation data the game will load.`));
}

function tuningPreview(box, tuning) {
  if (!box.damage) return { ...box, skipped: true };
  const out = { ...box };
  if (tuning.damage !== undefined) out.damage = tuning.damage;
  else if (tuning.damage_scale !== undefined) out.damage = Math.max(1, Math.round(box.damage * tuning.damage_scale));
  for (const k of ['angle', 'knockback_growth', 'base_knockback', 'weight_set_knockback']) if (tuning[k] !== undefined) out[k] = tuning[k];
  return out;
}

async function refreshMoves() {
  movesInfo = await api.post('/api/moves', { moveset: project.moveset }).catch(e => ({ error: e.message }));
  if (mode === 'moves') renderPanel();
}
const donorMoves = {};
async function movesOf(fighter) {
  donorMoves[fighter] ||= api.get('/api/fighter_moves?fighter=' + encodeURIComponent(fighter)).then(r => r.moves);
  return donorMoves[fighter];
}
async function previewSpecial(fighter, slot) {
  const key = { special_neutral: 'N', special_side: 'S', special_up: 'Hi', special_down: 'Lw' }[slot];
  const moves = (await movesOf(fighter)).filter(m => m.frames && m.action.startsWith('Special' + key) && !/^Special(N|S|Hi|Lw)/.test(m.action.slice(7 + key.length)));
  if (!moves.length) { status(`No ${fighterLabel(fighter)} animation to preview for that special`, 'err'); return; }
  const best = moves.sort((a, b) => (!!b.hitboxes.length - !!a.hitboxes.length) || b.frames - a.frames)[0];
  await previewBorrow(fighter, best.action);
}

const openMoves = new Set();
function panelMoves(p) {
  p.append(el('h2', {}, 'Moveset'), el('p', {}, 'Take specials from other fighters, borrow normal attacks, and retune the hitboxes of every move.'));
  if (!movesInfo || movesInfo.error) { p.append(el('p', {}, movesInfo ? movesInfo.error : 'Loading…')); return; }
  p.append(el('h3', {}, 'Special moves (B)'), specialsCard(),
    el('h3', {}, 'Borrow a normal attack'), borrowCard(),
    el('h3', {}, `All moves (${project.moveset.moves.length})`));
  project.moveset.moves.forEach((move, mi) => p.append(moveCard(move, mi)));
  const newSlot = { value: 'jab' };
  p.append(el('div', { class: 'row' }, el('label', {}, 'New move for'),
    dropdown(S.options.slots.map(s => [s.id, s.label]), newSlot.value, (v) => { newSlot.value = v; }),
    el('button', { onclick: () => {
      const s = slotInfo(newSlot.value); pushUndo();
      project.moveset.moves.push({ name: `New ${s.label.toLowerCase()}`, slot: s.id, description: '', actions: s.actions.map(a => ({ action: a, tuning: {} })) });
      openMoves.add(project.moveset.moves.length - 1); refreshMoves();
    } }, '+ Add')));
}

function specialsCard() {
  const card = el('div', { class: 'card' }, el('p', {}, `Keep ${fighterLabel(S.base.fighter)}'s own special or give the slot another fighter's. A borrowed special runs that fighter's real game code with your animations (PascalPatch's move-graft plugin), so it plays exactly like theirs.`));
  const moves = project.moveset.moves;
  for (const slot of S.options.slots.filter(s => s.special)) {
    const move = moves.find(m => m.slot === slot.id), donor = move?.graft?.fighter || '';
    const choices = [['', `${fighterLabel(S.base.fighter)}'s own`],
      { group: 'From another fighter', items: S.options.grafts[slot.id].map(g => [g.id, g.blocked ? `${g.label} — not yet (${g.blocked.split(',')[0]})` : g.label, !!g.blocked]) }];
    const sel = dropdown(choices, donor, (v) => {
      pushUndo();
      let m = moves.find(x => x.slot === slot.id);
      if (!m) { m = { name: slot.label, slot: slot.id, description: '', actions: [] }; moves.push(m); }
      if (v) {
        const wasOwn = !m.graft;
        m.graft = { fighter: v }; m.actions = [];
        if (wasOwn || !m.name || m.name === slot.label || /'s /.test(m.name)) m.name = `${fighterLabel(v)}'s ${slot.label}`;
      } else {
        delete m.graft; m.actions = slot.actions.map(a => ({ action: a, tuning: {} }));
        if (/'s /.test(m.name)) m.name = slot.label;
      }
      status(v ? `${slot.label} now uses ${fighterLabel(v)}'s` : `${slot.label} back to ${fighterLabel(S.base.fighter)}'s own`, 'ok');
      refreshMoves();
    }, { style: 'flex:1;min-width:0' });
    card.append(el('div', { class: 'row' }, el('label', { style: 'flex:0 0 70px' }, slot.label), sel,
      el('button', { title: donor ? `Preview ${fighterLabel(donor)}'s ${slot.label} on your model` : 'Preview on your model',
        onclick: () => donor ? previewSpecial(donor, slot.id) : previewOwnSpecial(slot) }, 'Preview')));
  }
  return card;
}
async function previewOwnSpecial(slot) {
  const prefix = slot.actions[0].replace('*', '');
  const name = S.animations.find(a => a.startsWith(prefix) && movesInfo.actions.includes(a)) || S.animations.find(a => a.startsWith(prefix));
  if (!name) { status('No animation for that special', 'err'); return; }
  await setMode('animate'); await loadAnimation(name); setPlaying(true); renderPanel();
}

function moveCard(move, mi) {
  const edit = (fn) => (v, e) => { pushUndo(); fn(v, e); };
  const info = movesInfo.moves[mi] || { base: [] };
  const details = el('details', { class: 'card move', open: openMoves.has(mi), ontoggle: (e) => { e.target.open ? openMoves.add(mi) : openMoves.delete(mi); } });
  const tag = move.graft ? `${fighterLabel(move.graft.fighter)}'s` : move.borrow ? `from ${fighterLabel(move.borrow.fighter)}` : null;
  details.append(el('summary', {}, el('b', {}, move.name || '(unnamed)'), ' ', el('span', { class: 'chip' }, slotLabel(move.slot)), tag ? el('span', { class: 'chip accent' }, tag) : null));
  details.append(row('Name', el('input', { type: 'text', value: move.name, onchange: (e) => { pushUndo(); move.name = e.target.value; renderPanel(); } })));
  const slotItems = S.options.slots.map(s => [s.id, s.label]);
  if (!slotInfo(move.slot)) slotItems.unshift([move.slot, slotLabel(move.slot)]);
  details.append(row('Button', dropdown(slotItems, move.slot, edit((v) => {
    move.slot = v; if (move.graft && !slotInfo(v)?.special) delete move.graft;
    if (!move.borrow && !move.graft && !(move.actions || []).length) move.actions = slotInfo(v).actions.map(a => ({ action: a, tuning: {} }));
    refreshMoves();
  }))));
  details.append(row('Description', el('textarea', { rows: 2, onchange: (e) => { pushUndo(); move.description = e.target.value; } }, move.description || '')));
  if (move.graft) {
    details.append(el('p', {}, `Runs ${fighterLabel(move.graft.fighter)}'s own ${slotLabel(move.slot)} code and timing. Change the source under Special moves.`),
      el('button', { onclick: () => previewSpecial(move.graft.fighter, move.slot) }, 'Preview on rig'));
  }
  if (move.borrow) {
    details.append(el('p', {}, `${fighterLabel(move.borrow.fighter)}'s ${actionLabel(move.borrow.action, false)}, replacing ${actionLabel(move.borrow.target_action || move.borrow.action, false)}.`),
      el('button', { onclick: () => previewBorrow(move.borrow.fighter, move.borrow.action) }, 'Preview on rig'));
  }
  if (!move.actions && move.action) { move.actions = [{ action: move.action, tuning: move.tuning || {} }]; delete move.action; delete move.tuning; }
  move.actions ||= [];
  if (!move.graft) {
    details.append(el('h4', {}, 'Hitbox tuning'));
    if (!move.actions.length) details.append(el('p', {}, move.borrow ? 'Borrowed hitboxes are used as they are. Add an action to retune them.' : 'No actions yet.'));
    move.actions.forEach((variant, vi) => details.append(variantCard(move, variant, vi, info)));
    details.append(el('div', { class: 'row' },
      el('button', { onclick: () => { pushUndo(); move.actions.push({ action: slotInfo(move.slot)?.actions[0] || movesInfo.actions[0], tuning: {} }); refreshMoves(); } }, '+ Tune another action'),
      el('button', { class: 'danger', onclick: () => { if (!confirm(`Delete "${move.name}"?`)) return; pushUndo(); project.moveset.moves.splice(mi, 1); openMoves.clear(); refreshMoves(); } }, 'Delete move')));
  } else {
    details.append(el('div', { class: 'row' }, el('button', { class: 'danger', onclick: () => { if (!confirm(`Delete "${move.name}"? The slot goes back to ${fighterLabel(S.base.fighter)}'s own special.`)) return; pushUndo(); project.moveset.moves.splice(mi, 1); openMoves.clear(); refreshMoves(); } }, 'Delete move')));
  }
  return details;
}

function variantCard(move, variant, vi, info) {
  const t = (variant.tuning ||= {});
  const bases = (info.base || []).filter(b => b.pattern === variant.action || b.action === variant.action);
  const v = el('div', { class: 'card inner' });
  v.append(el('div', { class: 'row' }, dropdown(actionGroups(movesInfo.actions, variant.action), variant.action,
    (val) => { pushUndo(); variant.action = val; refreshMoves(); }, { style: 'flex:1;min-width:0' }),
    el('button', { title: 'Stop tuning this action', onclick: () => { pushUndo(); move.actions.splice(vi, 1); refreshMoves(); } }, '✕')));
  const num = (key, label, step = 1, hint = '') => el('label', { class: 'field', title: hint }, label,
    el('input', { type: 'number', step, value: t[key] ?? '', placeholder: 'base', onchange: (e) => { pushUndo(); if (e.target.value === '') delete t[key]; else t[key] = +e.target.value; renderPanel(); } }));
  v.append(el('div', { class: 'fields' },
    num('damage', 'Damage %', 1, 'Set every hitbox to this damage'), num('damage_scale', 'Damage ×', 0.05, 'Multiply the base damage'),
    num('angle', 'Angle °', 1, 'Launch angle (361 = Sakurai angle)'), num('knockback_growth', 'KB growth', 1, 'Knockback growth'),
    num('base_knockback', 'Base KB', 1, 'Base knockback'),
    el('label', { class: 'field' }, 'Effect', dropdown([['', 'Base'], ...S.options.elements.map(x => [x, pretty(x)])], t.element || '',
      (val) => { pushUndo(); if (val) t.element = val; else delete t.element; }))));
  for (const base of bases) {
    if (base.error) { v.append(el('p', { class: 'err' }, base.error)); continue; }
    if (base.timing && base.timing.startup) v.append(frameDataCard(base, t));
    if (!base.hitboxes.length) continue;
    const table = el('table', { class: 'hb' }, el('caption', {}, actionLabel(base.action)),
      el('tr', {}, el('th', {}, 'Hitbox'), el('th', {}, 'Dmg'), el('th', {}, 'Angle'), el('th', {}, 'KBG'), el('th', {}, 'BKB')));
    for (const b of base.hitboxes) {
      const a = tuningPreview(b, t);
      const cell = (k) => el('td', { class: a[k] > b[k] ? 'delta-up' : a[k] < b[k] ? 'delta-down' : '' }, a[k] === b[k] ? `${b[k]}` : `${b[k]}→${a[k]}`);
      table.append(el('tr', {}, el('td', {}, `#${b.id}${a.skipped ? ' (grab)' : ''}`), cell('damage'), cell('angle'), cell('knockback_growth'), cell('base_knockback')));
    }
    v.append(table);
  }
  return v;
}

// Frame data read from the move's script (fighter_moves.timeline): when it hits, for how long,
// when the player can act again, and a shield-advantage estimate that follows damage tuning.
const shieldStun = (dmg) => Math.floor(0.45 * dmg + 2);   // hard shield, after hitlag (checked in game)
const signed = (n) => (n > 0 ? `+${n}` : `${n}`);
function frameDataCard(base, tuning) {
  const tm = base.timing;
  const firstHit = tm.hitboxes.filter(h => h.start === tm.startup && h.damage > 0);
  const dmg = Math.max(...firstHit.map(h => tuningPreview({ damage: h.damage }, tuning).damage));
  const ranges = tm.active.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(', ');
  const box = el('div', { class: 'fdata' });
  const stat = (label, value, hint) => el('span', { class: 'stat', title: hint || '' }, el('small', {}, label), el('b', {}, value));
  box.append(el('div', { class: 'stats' },
    stat('Startup', `F${tm.startup}`, 'First frame a hitbox is out'),
    stat('Active', ranges, 'Frames with hitboxes out'),
    stat('Can act', tm.ready ? `F${tm.ready}` : '—', tm.iasa ? `IASA from frame ${tm.iasa}` : 'When the animation ends'),
    stat('Total', tm.total ? `${tm.total}` : '—', 'Animation length in frames')));
  let shield;
  if (tm.landing && tm.landing.lag != null) {
    const s = shieldStun(dmg);
    shield = `Landing lag ${tm.landing.lag} (${tm.landing.l_cancel} L-cancelled). On shield, hitting just before landing: ` +
      `${signed(s - tm.landing.lag)}, ${signed(s - tm.landing.l_cancel)} L-cancelled.`;
    const ac = autoCancelText(tm.landing.lag_windows, tm.total);
    if (ac) shield += ` ${ac}`;
  } else if (tm.lag_after_hit != null && !tm.landing) {
    shield = `On shield: ${signed(shieldStun(dmg) - tm.lag_after_hit)} (first hit, ${dmg}% fresh, hard shield).`;
  }
  if (shield) box.append(el('p', { class: 'hint' }, shield));
  box.append(timelineStrip(tm));
  const ko = koLine(base.hitboxes || [], tuning);
  if (ko) box.append(ko);
  return box;
}

// Kill power: the percent the move's hardest-hitting hitbox KOs a few fighters from the centre of
// Final Destination (no DI), with the untuned figure beside it while tuning. See knockback.js.
const KO_TARGETS = ['fox', 'marth', 'peach', 'bowser'];
function koLine(hitboxes, tuning) {
  if (!castInfo) { loadCast(); return null; }
  const tuned = strongestHit(hitboxes.map(b => tuningPreview(b, tuning)).filter(b => !b.skipped));
  if (!tuned) return null;
  const orig = strongestHit(hitboxes);
  const targets = KO_TARGETS.filter(f => castInfo.fighters[f]);
  if (!targets.length) return null;
  const pct = (v) => (v == null ? 'never' : `${v}%`);
  const parts = targets.map((f) => {
    const t = castInfo.fighters[f].attributes, now = koPercent(tuned, t), was = orig ? koPercent(orig, t) : null;
    const cls = now === was ? '' : (now != null && (was == null || now < was)) ? ' up' : ' down';
    return el('span', { class: 'ko' + cls }, `${fighterLabel(f)} ${pct(now)}`, now !== was ? el('small', {}, ` (was ${pct(was)})`) : null);
  });
  const fox = castInfo.fighters.fox ? tumblePercent(tuned, castInfo.fighters.fox.attributes) : null;
  const line = el('p', { class: 'hint ko-line', title: `Hitbox #${tuned.id}: ${tuned.damage}%, angle ${tuned.angle}, growth ${tuned.knockback_growth}, base ${tuned.base_knockback}. ` +
    'Standing at the centre of Final Destination, no DI; KO if a blast zone is crossed while still launched.' },
    el('b', {}, 'KOs from centre FD: '));
  parts.forEach((x, i) => { if (i) line.append(' · '); line.append(x); });
  if (fox != null) line.append(`. Tumbles Fox from ${fox}%.`);
  return line;
}
function autoCancelText(windows, total) {
  if (!windows || !windows.length) return '';
  const [a, b] = windows[0];
  const parts = [];
  if (a > 1) parts.push(`before F${a}`);
  if (total && b < total) parts.push(`from F${b + 1}`);
  return parts.length ? `Auto-cancels ${parts.join(' and ')}.` : '';
}
function timelineStrip(tm) {
  const n = Math.max(tm.total || 0, ...tm.active.map(r => r[1]), tm.ready || 0);
  const strip = el('div', { class: 'ftl', title: 'Grey: startup · red: hitbox out · blue: recovery · green: can act' });
  const active = (f) => tm.active.some(([a, b]) => f >= a && f <= b);
  for (let f = 1; f <= n; f++) {
    const kind = active(f) ? 'hit' : f < tm.startup ? 'start' : tm.ready && f >= tm.ready ? 'free' : 'rec';
    strip.append(el('i', { class: kind, title: `Frame ${f}` }));
  }
  return strip;
}

const borrowState = { fighter: 'falco', moves: null, action: null, target: null, loading: false };
async function previewBorrow(fighter, action) {
  await setMode('animate');
  await loadAnimation(`${fighter}:${action}`, `/api/borrow_preview?fighter=${encodeURIComponent(fighter)}&action=${encodeURIComponent(action)}`);
  setPlaying(true); renderPanel();
}
function borrowCard() {
  const card = el('div', { class: 'card' }, el('p', {}, 'Jabs, tilts, smashes, aerials and the dash attack are retargeted onto your skeleton with their hitboxes, replacing one of your attacks.'));
  if (borrowState.fighter === S.base.fighter) borrowState.fighter = S.options.fighters.find(f => f.id !== S.base.fighter).id;
  card.append(row('From', dropdown(S.options.fighters.filter(f => f.id !== S.base.fighter).map(f => [f.id, f.label]), borrowState.fighter,
    (v) => { borrowState.fighter = v; borrowState.moves = null; borrowState.action = null; renderPanel(); })));
  if (!borrowState.moves) {
    if (!borrowState.loading) {
      borrowState.loading = true;
      movesOf(borrowState.fighter).then(m => { borrowState.moves = m; }).catch(e => { borrowState.moves = []; status(e.message, 'err'); }).finally(() => { borrowState.loading = false; renderPanel(); });
    }
    card.append(el('p', {}, 'Loading moves…')); return card;
  }
  const label = (m) => { const d = m.hitboxes.filter(h => h.damage).map(h => h.damage); const st = m.timing && m.timing.startup ? ` · hits F${m.timing.startup}` : ''; return `${actionLabel(m.action, false)}${st} · ${m.frames}f${d.length ? ` · ${Math.min(...d)}–${Math.max(...d)}%` : ''}`; };
  const ok = borrowState.moves.filter(m => m.borrowable);
  card.append(row('Move', dropdown([['', 'Choose a move…'],
    { group: 'Ground attacks', items: ok.filter(m => !m.action.startsWith('AttackAir')).map(m => [m.action, label(m)]) },
    { group: 'Aerials', items: ok.filter(m => m.action.startsWith('AttackAir')).map(m => [m.action, label(m)]) }],
    borrowState.action || '', (v) => { borrowState.action = v || null; borrowState.target = v ? guessTarget(v) : null; renderPanel(); })));
  if (borrowState.action) {
    const targets = (movesInfo.actions || []).filter(a => a.startsWith('Attack'));
    card.append(row('Replaces', dropdown(targets.map(a => [a, actionLabel(a)]), borrowState.target, (v) => { borrowState.target = v; })),
      el('div', { class: 'row' },
        el('button', { onclick: () => previewBorrow(borrowState.fighter, borrowState.action) }, 'Preview on rig'),
        el('button', { class: 'primary', onclick: () => {
          pushUndo();
          const target = borrowState.target || borrowState.action;
          project.moveset.moves = project.moveset.moves.filter(mv => !(mv.borrow && (mv.borrow.target_action || mv.borrow.action) === target));
          project.moveset.moves.push({ name: `${fighterLabel(borrowState.fighter)}'s ${actionLabel(borrowState.action, false)}`, slot: `borrow_${target}`,
            description: `Borrowed from ${fighterLabel(borrowState.fighter)}.`, borrow: { fighter: borrowState.fighter, action: borrowState.action, target_action: target }, actions: [] });
          status(`Added ${fighterLabel(borrowState.fighter)}'s ${actionLabel(borrowState.action, false)} in place of ${actionLabel(target, false)}`, 'ok');
          borrowState.action = null; refreshMoves();
        } }, 'Add to moveset')));
  }
  return card;
}
function guessTarget(action) {
  const t = (movesInfo.actions || []);
  if (t.includes(action)) return action;
  if (action === 'AttackS4' && t.includes('AttackS4S')) return 'AttackS4S';
  return t.find(a => a.startsWith(action)) || t.find(a => a.startsWith('Attack'));
}

// [label, hint, min, max, whole numbers?] grouped as the panel shows them
const STAT_GROUPS = [
  ['Ground movement', {
    walk_speed: ['Walk speed', 'Top walking speed', 0, 3], dash_speed: ['Dash speed', 'Initial dash speed', 0, 4],
    run_speed: ['Run speed', 'Top running speed', 0, 4], ground_friction: ['Traction', 'How quickly you slow down on the ground', 0, 0.3] }],
  ['Jumping & air', {
    jump_velocity: ['Jump height', 'Full hop launch speed', 0, 6], short_hop_velocity: ['Short hop height', 'Short hop launch speed', 0, 4],
    jump_squat_frames: ['Jump squat', 'Frames before leaving the ground', 1, 10, true], max_jumps: ['Jumps', 'Total jumps including the ground jump', 1, 6, true],
    air_jump_multiplier: ['Air jump height', 'Air jump strength compared to the ground jump', 0, 2],
    gravity: ['Gravity', 'How fast falling speeds up', 0.01, 0.4], fall_speed: ['Fall speed', 'Maximum falling speed', 0.5, 5],
    fast_fall_speed: ['Fast-fall speed', 'Falling speed when fast-falling', 0.5, 6], air_speed: ['Air speed', 'Top horizontal speed in the air', 0, 2],
    air_acceleration: ['Air acceleration', 'How fast you reach air speed', 0, 0.3], air_friction: ['Air friction', 'How fast you drift to a stop in the air', 0, 0.2] }],
  ['Body', {
    weight: ['Weight', 'Heavier = launched less far', 50, 160, true], size: ['Size', 'Model and hurtbox scale', 0.5, 1.6],
    shield_size: ['Shield size', 'Radius of the full shield', 5, 25] }],
  ['Landing lag', {
    landing_lag: ['Normal landing', 'Frames of lag after landing from a jump', 0, 30, true], landing_lag_nair: ['After neutral air', 'Frames (halved when L-cancelled)', 0, 60, true],
    landing_lag_fair: ['After forward air', '', 0, 60, true], landing_lag_bair: ['After back air', '', 0, 60, true],
    landing_lag_uair: ['After up air', '', 0, 60, true], landing_lag_dair: ['After down air', '', 0, 60, true] }],
];
function statRow(name, attrs, [label, hint, lo, hi, whole]) {
  const base = S.base_attributes[name], on = name in attrs, value = on ? attrs[name] : base, step = whole ? 1 : (hi - lo) / 400;
  hi = Math.max(hi, base, value); lo = Math.min(lo, base, value);
  const fmt = (v) => whole ? String(Math.round(v)) : (+v).toFixed(3);
  const set = (v) => { attrs[name] = whole ? Math.round(v) : +(+v).toFixed(4); };
  const numIn = el('input', { type: 'number', step, value: fmt(value), disabled: !on, class: 'stat-num', onchange: (e) => { pushUndo(); set(e.target.value); renderPanel(); } });
  const slider = el('input', { type: 'range', min: lo, max: hi, step, value, disabled: !on,
    oninput: (e) => { set(e.target.value); numIn.value = fmt(e.target.value); markDirty(); }, onchange: () => { pushUndo(); renderPanel(); } });
  const check = el('input', { type: 'checkbox', checked: on, title: on ? 'Go back to the base value' : 'Change this stat', onchange: (e) => { pushUndo(); if (e.target.checked) attrs[name] = base; else delete attrs[name]; renderPanel(); } });
  const diff = on && attrs[name] !== base ? ` · ${attrs[name] > base ? '+' : ''}${base ? `${(100 * (attrs[name] - base) / base).toFixed(0)}%` : (attrs[name] - base)}` : '';
  return el('div', { class: 'stat' + (on ? '' : ' off'), title: hint }, el('label', { class: 'check' }, check, label), slider, numIn,
    el('div', { class: 'muted small' }, [hint, `base ${base}${diff}`].filter(Boolean).join(' · ')), castStrip(name, value));
}

// Where a stat sits among Melee's 26 fighters: a tick per fighter, the character's value highlighted.
let castInfo = null, castLoading = false;
function loadCast() {
  if (castInfo || castLoading) return;
  castLoading = true;
  api.get('/api/cast').catch(() => ({ fighters: {} })).then((c) => { castInfo = c; castLoading = false; if (mode === 'stats' || mode === 'moves') renderPanel(); });
}
const ordinal = (n) => { const t = n % 100, u = n % 10; return `${n}${t >= 11 && t <= 13 ? 'th' : u === 1 ? 'st' : u === 2 ? 'nd' : u === 3 ? 'rd' : 'th'}`; };
const fmtStat = (v) => (Number.isInteger(v) ? `${v}` : (+v).toFixed(3).replace(/0+$/, '').replace(/[.]$/, ''));
function castStrip(name, value) {
  if (!castInfo) { loadCast(); return null; }
  const column = Object.entries(castInfo.fighters).map(([f, d]) => [f, d.attributes[name]]).filter(([, v]) => v != null);
  if (!column.length) return null;
  const vals = column.map(([, v]) => v), lo = Math.min(...vals, value), hi = Math.max(...vals, value), span = hi - lo || 1;
  const x = (v) => `${(100 * (v - lo) / span).toFixed(2)}%`;
  const rank = 1 + vals.filter(v => v > value).length;
  const [maxF, maxV] = column.reduce((a, b) => (b[1] > a[1] ? b : a)), [minF, minV] = column.reduce((a, b) => (b[1] < a[1] ? b : a));
  const strip = el('div', { class: 'cast-strip' });
  for (const [f, v] of column) strip.append(el('i', { style: `left:${x(v)}`, title: `${fighterLabel(f)}: ${fmtStat(v)}`, class: f === S.base.fighter ? 'base' : '' }));
  strip.append(el('b', { style: `left:${x(value)}`, title: `${project.character.display_name || 'Yours'}: ${fmtStat(value)}` }));
  const where = value > maxV ? 'above every fighter' : value < minV ? 'below every fighter' : `${ordinal(rank)} highest of ${vals.length + 1}`;
  return el('div', { class: 'cast' }, strip, el('div', { class: 'muted small' }, `${where} · ${fighterLabel(minF)} ${fmtStat(minV)} … ${fighterLabel(maxF)} ${fmtStat(maxV)}`));
}
// How long the character lives: Melee's signature kill moves on it, next to the whole cast.
// Weight, gravity and fall speed all move these numbers (knockback.js flies the launch).
const SURVIVAL_MOVES = [['marth', 'smash_forward', 'forward smash'], ['fox', 'smash_up', 'up smash'],
  ['captain-falcon', 'attack_air_forward', 'forward air'], ['sheik', 'attack_air_forward', 'forward air']];
function effectiveAttributes() {
  const out = { ...S.base_attributes };
  for (const [k, sc] of Object.entries(project.character.attribute_scales || {})) if (k in out) out[k] *= +sc;
  for (const [k, v] of Object.entries(project.character.attributes || {})) if (k in out && typeof v === 'number') out[k] = v;
  return out;
}
function survivalCard() {
  const card = el('details', { class: 'card survival', open: true }, el('summary', {}, el('b', {}, 'Survival')));
  if (!castInfo) { loadCast(); card.append(el('p', { class: 'muted' }, "Loading Melee's cast…")); return card; }
  card.append(el('p', { class: 'muted small' }, 'The percent each move KOs at, standing at the centre of Final Destination with no DI. Weight, gravity and fall speed change it.'));
  const me = effectiveAttributes(), table = el('table', { class: 'hb' },
    el('tr', {}, el('th', {}, 'Kill move'), el('th', {}, 'You'), el('th', {}, fighterLabel(S.base.fighter)), el('th', {}, "Melee's cast")));
  for (const [owner, slot, what] of SURVIVAL_MOVES) {
    const hit = castInfo.fighters[owner]?.moves?.[slot]?.ko_hit;
    if (!hit) continue;
    const kos = Object.entries(castInfo.fighters).map(([f, d]) => [f, koPercent(hit, d.attributes)]).filter(([, v]) => v != null);
    if (!kos.length) continue;
    const mine = koPercent(hit, me), base = kos.find(([f]) => f === S.base.fighter)?.[1];
    const vals = kos.map(([, v]) => v), rank = mine == null ? null : 1 + vals.filter(v => v > mine).length;
    const lo = kos.reduce((a, b) => (b[1] < a[1] ? b : a)), hi = kos.reduce((a, b) => (b[1] > a[1] ? b : a));
    const delta = mine != null && base != null && mine !== base ? el('small', { class: mine > base ? 'delta-up' : 'delta-down' }, ` ${mine > base ? '+' : ''}${mine - base}`) : null;
    table.append(el('tr', {}, el('td', {}, `${fighterLabel(owner)}'s ${what}`), el('td', {}, el('b', {}, mine == null ? 'never' : `${mine}%`), delta),
      el('td', {}, base == null ? '—' : `${base}%`),
      el('td', { class: 'muted small' }, rank ? `${ordinal(rank)} longest of ${vals.length + 1} · ` : '', `${fighterLabel(lo[0])} ${lo[1]}% … ${fighterLabel(hi[0])} ${hi[1]}%`)));
  }
  card.append(table);
  return card;
}
function panelStats(p) {
  const attrs = (project.character.attributes ||= {});
  p.append(el('h2', {}, 'Attributes'), el('p', {}, `Tick a stat to change it; unticked stats keep ${fighterLabel(S.base.fighter)}'s value.`));
  const known = new Set();
  STAT_GROUPS.forEach(([title, stats], gi) => {
    const names = Object.keys(stats).filter(n => S.attribute_names.includes(n)); names.forEach(n => known.add(n));
    const changed = names.filter(n => n in attrs).length;
    const box = el('details', { class: 'card', open: gi < 2 || changed > 0 }, el('summary', {}, el('b', {}, title), changed ? el('span', { class: 'chip accent' }, `${changed} changed`) : null));
    names.forEach(n => box.append(statRow(n, attrs, stats[n])));
    p.append(box);
  });
  const rest = S.attribute_names.filter(n => !known.has(n));
  if (rest.length) { const box = el('details', { class: 'card' }, el('summary', {}, el('b', {}, 'Other'))); rest.forEach(n => box.append(statRow(n, attrs, [pretty(n), '', 0, Math.max(1, S.base_attributes[n] * 3)]))); p.append(box); }
  p.append(survivalCard());
  p.append(el('button', { disabled: !Object.keys(attrs).length, onclick: () => { if (!confirm('Reset every stat to the base fighter?')) return; pushUndo(); project.character.attributes = {}; renderPanel(); } }, `Reset all to ${fighterLabel(S.base.fighter)}`));
}

// ------------------------------------------------------------------ select-screen pictures
// The character select screen shows a character twice: its icon in the grid (64 x 56) and its
// portrait on the player's door (136 x 188). Both are rendered here from the rigged model in
// its idle pose, the way the game's own are (a close 3/4 view, lit from the front), and stored
// in the project by the server (icon.png, portrait.png). The door can show a photo instead: a
// crop of any picture (the original photo is kept so the crop can be changed later). PascalPatch
// puts both on screen while the game runs.
const DOOR_W = 136, DOOR_H = 188, PLATE = 0.78;       // the name plate covers the door below 78%
const ICON_W = 64, ICON_H = 56, ICON_Y_SCALE = 1.25;  // the grid draws icons 1.25x taller than wide
const STOCK_W = 24, STOCK_H = 24;                     // a stock icon, shown once per life above the damage
const portrait = { img: null, crop: { zoom: 1, x: 0, y: 0 }, loadedFor: null, saveTimer: null, sourceUrl: null, note: '',
                   icon: null, iconFor: null, rendered: null, stock: null, stockFor: null };
const pictureMode = () => project.character.portrait_mode || (project.character.portrait_source ? 'photo' : 'model');
const pictureView = () => ({ turn: 20, zoom: 1, ...(project.character.portrait_view || {}) });

let idleFrame = null, shotRenderer = null;
async function idlePose() {
  if (idleFrame) return idleFrame;
  const name = S.animations.find(a => a === 'Wait1') || S.animations.find(a => a.startsWith('Wait'));
  if (!name) return null;
  const r = await api.get('/api/animation?name=' + encodeURIComponent(name));
  return (idleFrame = r.frames[0]);
}

// Render the posed model into a width x height picture (transparent background). `frame`
// picks what to show from the model's vertices as seen by the camera: {top, height, cx} in
// camera units. yScale: how much taller than wide the game draws the picture's texels.
function renderShot(geometry, width, height, turn, frame, yScale = 1) {
  const SS = 4;   // rendered 4x larger, then scaled down: smooth edges like the game's pictures
  if (!shotRenderer) {
    shotRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    shotRenderer.outputColorSpace = THREE.SRGBColorSpace; shotRenderer.setClearColor(0x000000, 0);
  }
  shotRenderer.setPixelRatio(1); shotRenderer.setSize(width * SS, height * SS, false);
  const shot = new THREE.Scene();
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ map: texture, color: texture ? 0xffffff : 0xb8bcc8, roughness: 0.75, metalness: 0, side: THREE.DoubleSide }));
  shot.add(mesh);
  const a = THREE.MathUtils.degToRad(turn), view = new THREE.Vector3(Math.sin(a), 0.12, Math.cos(a)).normalize();
  geometry.computeBoundingSphere();
  const c = geometry.boundingSphere.center, r = geometry.boundingSphere.radius;
  const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, r * 8);
  cam.position.copy(c).addScaledVector(view, r * 3); cam.lookAt(c); cam.updateMatrixWorld();
  const f = frame(geometry.attributes.position, cam.matrixWorldInverse);
  const halfW = f.height * width / (height * yScale) / 2;
  Object.assign(cam, { left: f.cx - halfW, right: f.cx + halfW, top: f.top, bottom: f.top - f.height }); cam.updateProjectionMatrix();
  // key light from the upper front-left, soft fill, and a cool rim that separates the silhouette
  shot.add(new THREE.HemisphereLight(0xf4f6ff, 0x3a3440, 1.3));
  const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.copy(cam.position).add(new THREE.Vector3(-r, r * 1.5, 0)); key.target = mesh; shot.add(key);
  const rim = new THREE.DirectionalLight(0xa8c4ff, 1.1); rim.position.copy(c).addScaledVector(view, -r * 3).add(new THREE.Vector3(0, r, 0)); rim.target = mesh; shot.add(rim);
  shotRenderer.render(shot, cam);
  mesh.material.dispose();
  const out = el('canvas', { width, height }), g = out.getContext('2d');
  let src = shotRenderer.domElement, w = src.width, h = src.height;
  while (w > width * 2) {   // halve step by step: a clean box filter
    const half = el('canvas', { width: w / 2, height: h / 2 }); half.getContext('2d').drawImage(src, 0, 0, w / 2, h / 2);
    src = half; w /= 2; h /= 2;
  }
  g.imageSmoothingQuality = 'high'; g.drawImage(src, 0, 0, width, height);
  return out;
}

const viewPoint = new THREE.Vector3();
const viewY = (pos, inv, i) => viewPoint.fromBufferAttribute(pos, i).applyMatrix4(inv).y;
// Which way the posed model faces, in degrees about the vertical (0: +Z, the bind pose's front):
// the turn of the joint that carries the most of the mesh. Idle poses often stand turned.
function poseYaw(world) {
  const load = new Float32Array(world.length);
  for (const ws of rigData.weights) for (const [j, w] of ws) load[j] += w;
  const j = load.indexOf(Math.max(...load)), m = mul4(world[j], S.base.joints[j].inverse);
  return THREE.MathUtils.radToDeg(Math.atan2(m[2], m[0]));
}

// What a camera sees of a set of vertices: their extent in camera units.
function viewExtent(pos, inv, pick = null) {
  const v = new THREE.Vector3(); let top = -Infinity, bottom = Infinity, left = Infinity, right = -Infinity, n = 0;
  for (let i = 0; i < pos.count; i++) {
    if (pick && !pick(i)) continue;
    v.fromBufferAttribute(pos, i).applyMatrix4(inv); n++;
    top = Math.max(top, v.y); bottom = Math.min(bottom, v.y); left = Math.min(left, v.x); right = Math.max(right, v.x);
  }
  return { top, bottom, left, right, n };
}

// The door portrait and the grid icon, rendered from the model: {door, icon} canvases.
// The model's head: its vertices near the fitted head joint and above the neck (in the model
// as imported, where the landmarks are), or null if the rig has no head fitted.
const ROUND_FIGHTERS = ['jigglypuff', 'kirby'];
function headVertices() {
  const lm = project.rig.landmarks || {}, hd = lm.head, nk = lm.neck, src = S.model.positions;
  if (!hd || !nk) return null;
  // Within about a neck's length of the head joint, and clear of the shoulders (which a short
  // neck puts about as high as the neck joint).
  const r = Math.max(1e-3, Math.hypot(hd[0] - nk[0], hd[1] - nk[1], hd[2] - nk[2]) * 1.25), n = src.length / 3;
  const floor = nk[1] + (hd[1] - nk[1]) * 0.3;
  const out = new Uint8Array(n); let count = 0;
  for (let v = 0; v < n; v++) {
    const x = src[v * 3], y = src[v * 3 + 1], z = src[v * 3 + 2];
    if (y > floor && Math.hypot(x - hd[0], y - hd[1], z - hd[2]) < r) { out[v] = 1; count++; }
  }
  // A round fighter (built on Jigglypuff or Kirby: a body that is all head) has its head joint
  // near the top, so that finds a small cap: its head is everything above the shoulders.
  const shoulders = [lm.l_shoulder, lm.r_shoulder].filter(Boolean).map(p => p[1]);
  if (shoulders.length && (count < 8 || ROUND_FIGHTERS.includes(S.base.fighter))) {
    const line = Math.max(...shoulders); count = 0;
    for (let v = 0; v < n; v++) { out[v] = src[v * 3 + 1] > line ? 1 : 0; count += out[v]; }
    return count >= 8 ? out : null;   // its painted "head" is some part of it (an eye, an ear)
  }
  // Where the body parts are painted, the head part alone (a heavyweight's shoulders can
  // reach up around its head); a round fighter painted all one part keeps the whole set.
  const segs = rigData.segments || [];
  let painted = 0;
  for (let v = 0; v < n; v++) if (out[v] && segs[v] === 'head') painted++;
  if (painted >= 8) for (let v = 0; v < n; v++) if (out[v] && segs[v] !== 'head') { out[v] = 0; count--; }
  return count >= 8 ? out : null;
}

// How far (degrees) the pose tips the head from upright: the turn of "up" by the joint that
// carries most of the head.
function headTilt(world, head) {
  const load = new Float32Array(world.length);
  rigData.weights.forEach((ws, v) => { if (head[v]) for (const [j, w] of ws) load[j] += w; });
  const j = load.indexOf(Math.max(...load)), m = mul4(world[j], S.base.joints[j].inverse);
  return THREE.MathUtils.radToDeg(Math.acos(Math.min(1, Math.max(-1, m[5] / Math.hypot(m[1], m[5], m[9])))));
}

async function renderPictures() {
  await ensureRig();
  const pose = await idlePose().catch(() => null);
  const n = S.model.positions.length;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(n), 3));
  geo.setAttribute('normal', new THREE.Float32BufferAttribute(new Float32Array(n), 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(S.model.uvs, 2));
  geo.setIndex(S.model.indices);
  const world = pose ? frameMatrices(pose, true) : null;
  skinVertices(world, geo.attributes.position, geo.attributes.normal);
  const { zoom } = pictureView(), turn = pictureView().turn + (world ? poseYaw(world) : 0);
  // The door shows head to thighs and as much of the width as fits, framed from the
  // silhouette, which works for any body shape.
  const door = renderShot(geo, DOOR_W, DOOR_H, turn, (pos, inv) => {
    const e = viewExtent(pos, inv), H = e.top - e.bottom, W = e.right - e.left;
    const height = Math.max(H * 0.8, Math.min(W * DOOR_H / DOOR_W * 0.75, H * 1.3)) / zoom, top = e.top + height * 0.04;
    const band = viewExtent(pos, inv, (i) => viewY(pos, inv, i) > top - height * PLATE);
    return { top, height, cx: (band.left + band.right) / 2 };
  });
  const head = headVertices();
  // An idle pose that bows the head (a hunched heavyweight looks at the floor) would show the
  // top of the head; the icon then shows the model standing as it was built.
  const bowed = head && world && headTilt(world, head) > 35;
  let iconGeo = geo, iconTurn = turn;
  if (bowed) {
    iconGeo = geo.clone();
    skinVertices(null, iconGeo.attributes.position, iconGeo.attributes.normal);
    iconTurn = pictureView().turn;
  }
  const art = renderShot(iconGeo, ICON_W, ICON_H, iconTurn, (pos, inv) => {
    // Head and shoulders, as in the game's icons: the head is the skin around the fitted head
    // joint (above the neck), found in the model as imported and followed into the pose, so a
    // hunched idle pose still shows the face. The head sits high enough that the name bar
    // covers no more than the chin.
    const e = viewExtent(pos, inv), H = e.top - e.bottom;
    if (head) {
      const h = viewExtent(pos, inv, (i) => head[i]), hh = h.top - h.bottom;
      const height = Math.min(H * 0.95, Math.max(H * 0.2, hh * 2.3)) / zoom;
      return { top: Math.min(e.top + height * 0.05, h.top + height * 0.12), height, cx: (h.left + h.right) / 2 };
    }
    const height = H * 0.55 / zoom;
    return { top: e.top + height * 0.05, height, cx: (e.left + e.right) / 2 };
  }, ICON_Y_SCALE);
  // The stock icon: the head alone, filling the square, like the game's little heads.
  const face = renderShot(iconGeo, STOCK_W, STOCK_H, iconTurn, (pos, inv) => {
    const e = viewExtent(pos, inv), H = e.top - e.bottom;
    const h = head ? viewExtent(pos, inv, (i) => head[i]) : { top: e.top, bottom: e.top - H * 0.3, left: e.left, right: e.right };
    // a wide head fills the square, but arms held out at head height don't shrink it
    const hh = h.top - h.bottom, height = Math.max(hh, Math.min(h.right - h.left, hh * 1.3) * 0.9) * 1.12 / zoom;
    return { top: (h.top + h.bottom) / 2 + height / 2, height, cx: (h.left + h.right) / 2 };
  });
  if (iconGeo !== geo) iconGeo.dispose();
  geo.dispose();
  return { door, icon: composeIcon(art, gameName(project.character.display_name).toUpperCase()), stock: composeStock(face) };
}

// A stock icon reads at a glance over any stage: the head with a dark outline.
function composeStock(art) {
  const c = el('canvas', { width: STOCK_W, height: STOCK_H }), g = c.getContext('2d');
  const shadow = el('canvas', { width: STOCK_W, height: STOCK_H }), s = shadow.getContext('2d');
  s.drawImage(art, 0, 0); s.globalCompositeOperation = 'source-in'; s.fillStyle = '#101014'; s.fillRect(0, 0, STOCK_W, STOCK_H);
  for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) g.drawImage(shadow, dx, dy);
  g.drawImage(art, 0, 0);
  return c;
}

function roundRect(g, x, y, w, h, r) {
  g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
}
// The grid icon as the game draws its own: a grey rounded frame, a dark blue-grey striped
// backdrop, the character, and the name in a dark red bar along the bottom.
function composeIcon(art, name) {
  const c = el('canvas', { width: ICON_W, height: ICON_H }), g = c.getContext('2d');
  roundRect(g, 0, 0, ICON_W, ICON_H, 5); g.fillStyle = '#9c9c9e'; g.fill();
  roundRect(g, 0.5, 0.5, ICON_W - 1, ICON_H - 1, 4.5); g.strokeStyle = '#c8c8ca'; g.lineWidth = 1; g.stroke();
  const ix = 2, iy = 2, iw = ICON_W - 4, ih = ICON_H - 5;
  g.save(); roundRect(g, ix, iy, iw, ih, 3); g.clip();
  const bg = g.createLinearGradient(0, iy, 0, iy + ih); bg.addColorStop(0, '#15222f'); bg.addColorStop(0.55, '#34404f'); bg.addColorStop(1, '#3d4757');
  g.fillStyle = bg; g.fillRect(ix, iy, iw, ih);
  const side = g.createLinearGradient(ix, 0, ix + iw, 0); side.addColorStop(0, 'rgba(120,150,190,0.18)'); side.addColorStop(1, 'rgba(0,0,0,0.12)');
  g.fillStyle = side; g.fillRect(ix, iy, iw, ih);
  for (let y = iy + 1; y < iy + ih; y += 3) { g.fillStyle = y % 2 ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.07)'; g.fillRect(ix, y, iw, 1); }
  g.drawImage(art, 0, 0);
  const barY = 39, barH = ICON_H - 3 - barY;
  const bar = g.createLinearGradient(0, barY, 0, barY + barH); bar.addColorStop(0, '#2c0303'); bar.addColorStop(1, '#170000');
  g.fillStyle = '#000'; g.fillRect(ix, barY - 1, iw, 1); g.fillStyle = bar; g.fillRect(ix, barY, iw, barH);
  g.restore();
  g.save();
  // tall, narrow capitals filling the bar, squeezed sideways to fit like the game's long names
  g.font = 'bold 12px "Arial Narrow", "Roboto Condensed", Arial, sans-serif'; g.textBaseline = 'alphabetic'; g.textAlign = 'center';
  const text = name || '?', fit = Math.min(0.8, (iw - 4) / g.measureText(text).width);
  g.translate(ICON_W / 2, barY + barH - 1.5); g.scale(fit, 1);
  g.lineJoin = 'round'; g.lineWidth = 2; g.strokeStyle = '#000'; g.strokeText(text, 0, 0);
  const ink = g.createLinearGradient(0, -9, 0, 0); ink.addColorStop(0, '#f4f4f4'); ink.addColorStop(1, '#a9a9ab');
  g.fillStyle = ink; g.fillText(text, 0, 0);
  g.restore();
  return c;
}

// Render both pictures and store them in the project. With `iconOnly`, the door keeps its photo.
async function savePictures(iconOnly = false) {
  const shots = await renderPictures();
  const body = { icon: shots.icon.toDataURL('image/png'), stock: shots.stock.toDataURL('image/png') };
  if (!iconOnly) { body.image = shots.door.toDataURL('image/png'); body.generated = true; }
  const r = await api.post('/api/portrait', body);
  for (const k of ['portrait', 'portrait_source', 'portrait_crop', 'portrait_mode', 'icon', 'stock']) { if (k in r.character) project.character[k] = r.character[k]; else delete project.character[k]; }
  portrait.icon = shots.icon; portrait.rendered = shots.door; portrait.stock = shots.stock;
  portrait.iconFor = project.character.icon; portrait.stockFor = project.character.stock;
  if (!iconOnly) { portrait.img = null; portrait.loadedFor = `${project.character.portrait_source || ''}|${project.character.portrait || ''}`; }
  return shots;
}

function loadImage(src) {
  return new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = () => reject(new Error('not a picture this browser can read')); i.src = src; });
}
async function loadSavedPortrait() {
  const c = project.character, key = `${c.portrait_source || ''}|${c.portrait || ''}`;
  if (portrait.loadedFor === key) return false;
  portrait.loadedFor = key; portrait.img = null; portrait.sourceUrl = null;
  if (c.icon && portrait.iconFor !== c.icon) {
    portrait.iconFor = c.icon;
    portrait.icon = await loadImage(`/api/portrait?icon=1&v=${Date.now()}`).catch(() => null);
  }
  if (c.stock && portrait.stockFor !== c.stock) {
    portrait.stockFor = c.stock;
    portrait.stock = await loadImage(`/api/portrait?stock=1&v=${Date.now()}`).catch(() => null);
  }
  if (!c.portrait || pictureMode() === 'model') return true;
  const src = c.portrait_source ? '/api/portrait?source=1' : '/api/portrait';
  try {
    portrait.img = await loadImage(`${src}${src.includes('?') ? '&' : '?'}v=${Date.now()}`);
    portrait.crop = c.portrait_source ? { zoom: 1, x: 0, y: 0, ...(c.portrait_crop || {}) } : { zoom: 1, x: 0, y: 0 };
  } catch { portrait.img = null; }
  return true;
}
function cropGeometry() {
  const { img, crop } = portrait, scale = Math.max(DOOR_W / img.naturalWidth, DOOR_H / img.naturalHeight) * crop.zoom;
  const w = img.naturalWidth * scale, h = img.naturalHeight * scale, freeX = w - DOOR_W, freeY = h - DOOR_H;
  return { w, h, freeX, freeY, left: -freeX * (crop.x + 1) / 2, top: -freeY * (crop.y + 1) / 2 };
}
function drawDoor(canvas, withPlate) {
  const g = canvas.getContext('2d'); g.clearRect(0, 0, DOOR_W, DOOR_H);
  if (pictureMode() === 'model' && withPlate) {   // the preview: the door's backdrop, then the render
    const bg = g.createLinearGradient(0, 0, 0, DOOR_H); bg.addColorStop(0, '#3a3f86'); bg.addColorStop(1, '#1d2050');
    g.fillStyle = bg; g.fillRect(0, 0, DOOR_W, DOOR_H);
    for (let y = 0; y < DOOR_H; y += 4) { g.fillStyle = 'rgba(0,0,0,0.12)'; g.fillRect(0, y, DOOR_W, 2); }
    if (portrait.rendered) g.drawImage(portrait.rendered, 0, 0);
    else { g.fillStyle = '#c9cbe8'; g.font = '11px sans-serif'; g.textAlign = 'center'; g.fillText('Rendering…', DOOR_W / 2, DOOR_H * 0.4); }
  } else if (!portrait.img) {
    g.fillStyle = '#23263a'; g.fillRect(0, 0, DOOR_W, DOOR_H); g.fillStyle = '#8a8fb0'; g.font = '11px sans-serif'; g.textAlign = 'center';
    g.fillText('No photo yet', DOOR_W / 2, DOOR_H * 0.4); g.fillText(`shows ${fighterLabel(S.base.fighter)}'s`, DOOR_W / 2, DOOR_H * 0.4 + 15);
  } else {
    const r = cropGeometry(); g.imageSmoothingQuality = 'high'; g.drawImage(portrait.img, r.left, r.top, r.w, r.h);
  }
  if (withPlate) {   // what the game draws over the photo: the name plate
    g.fillStyle = 'rgba(20, 20, 28, 0.82)'; g.fillRect(8, DOOR_H * PLATE, DOOR_W - 16, 20);
    g.fillStyle = '#fff'; g.font = 'bold 13px sans-serif'; g.textAlign = 'center';
    g.fillText(gameName(project.character.display_name) || '?', DOOR_W / 2, DOOR_H * PLATE + 15);
  }
}
// The game's name font has letters, digits and spaces; anything else shows as '?'.
const gameName = (s) => String(s || '').replace(/[-_]/g, ' ').replace(/[^A-Za-z0-9 ]/g, '?');
async function sourceDataUrl(img, type) {
  const max = 1024, k = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
  const c = el('canvas', { width: Math.round(img.naturalWidth * k), height: Math.round(img.naturalHeight * k) });
  c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL(type === 'image/png' ? 'image/png' : 'image/jpeg', 0.92);
}
function schedulePortraitSave(withSource = false) {
  clearTimeout(portrait.saveTimer); portrait.note = 'Saving photo…'; updatePortraitNote();
  portrait.saveTimer = setTimeout(async () => {
    try {
      const door = el('canvas', { width: DOOR_W, height: DOOR_H }); drawDoor(door, false);
      const body = { image: door.toDataURL('image/png'), crop: portrait.crop };
      if (withSource || portrait.sourceUrl) body.source = portrait.sourceUrl || await sourceDataUrl(portrait.img, 'image/jpeg');
      const r = await api.post('/api/portrait', body);
      for (const k of ['portrait', 'portrait_source', 'portrait_crop', 'portrait_mode']) { if (k in r.character) project.character[k] = r.character[k]; else delete project.character[k]; }
      portrait.loadedFor = `${project.character.portrait_source || ''}|${project.character.portrait || ''}`; portrait.sourceUrl = null;
      portrait.note = 'Photo saved to the project. Build to put it in the game.'; status('Photo saved', 'ok');
    } catch (e) { portrait.note = `Could not save the photo: ${e.message}`; status(portrait.note, 'err'); }
    updatePortraitNote();
  }, 500);
}
function updatePortraitNote() { const n = $('#portrait-note'); if (n) { n.textContent = portrait.note; n.className = 'small ' + (portrait.note.startsWith('Could not') ? 'err' : 'muted'); } }
async function choosePortraitFile(file) {
  if (!file || !file.type.startsWith('image/')) { status('Pick a picture file (PNG, JPEG, WebP or GIF)', 'err'); return; }
  const url = URL.createObjectURL(file);
  try {
    const img = await loadImage(url);
    portrait.img = img; portrait.crop = { zoom: 1, x: 0, y: 0 };
    portrait.sourceUrl = await sourceDataUrl(img, file.type === 'image/png' ? 'image/png' : 'image/jpeg');
    renderPanel(); schedulePortraitSave(true);
  } catch (e) { status(e.message, 'err'); }
  finally { URL.revokeObjectURL(url); }
}

function portraitCard() {
  const c = project.character, modelMode = pictureMode() === 'model';
  const card = el('div', { class: 'card portrait-card' }, el('h3', {}, 'Character select screen'));
  const nameIn = el('input', { type: 'text', value: c.display_name || '', maxlength: 16, placeholder: 'Name on the select screen',
    oninput: (e) => { c.display_name = e.target.value; drawDoor(view, true); nameHint.textContent = nameHintText(); markDirty(); },
    onchange: () => { pushUndo(); refreshPictures(); } });
  const nameHintText = () => { const n = c.display_name || ''; return /[^-_A-Za-z0-9 ]/.test(n) ? 'The game can only show A–Z, 0–9 and spaces (dashes show as spaces); other characters show as "?".'
    : n.length > 10 ? 'Long names may not fit the name plate.' : 'Shown on the door, on the grid icon and in menus.'; };
  const nameHint = el('div', { class: 'small muted' }, nameHintText());
  card.append(row('Name', nameIn), nameHint);

  const view = el('canvas', { width: DOOR_W, height: DOOR_H, class: 'portrait-view' + (modelMode ? ' still' : ''),
    title: modelMode ? 'The door picture, rendered from your model' : 'Drag to move the photo; scroll or use the slider to zoom' });
  const iconView = el('canvas', { width: ICON_W, height: ICON_H, class: 'portrait-icon', title: 'The grid icon, rendered from your model' });
  const stockView = el('canvas', { width: STOCK_W, height: STOCK_H, class: 'portrait-stock', title: 'The stock icon above the damage in a match, rendered from your model' });
  const drawIcon = () => {
    const g = iconView.getContext('2d'); g.clearRect(0, 0, ICON_W, ICON_H); if (portrait.icon) g.drawImage(portrait.icon, 0, 0);
    const s = stockView.getContext('2d'); s.clearRect(0, 0, STOCK_W, STOCK_H); if (portrait.stock) s.drawImage(portrait.stock, 0, 0);
  };
  const file = el('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif', hidden: true, onchange: (e) => choosePortraitFile(e.target.files[0]) });
  const setMode = async (next) => {
    if (next === pictureMode()) return;
    if (next === 'photo') { c.portrait_mode = 'photo'; if (!portrait.img) file.click(); renderPanel(); return; }
    c.portrait_mode = 'model'; portrait.note = 'Rendering from the model…'; renderPanel(); refreshPictures();
  };
  const modes = el('div', { class: 'seg-buttons' },
    el('button', { class: modelMode ? 'active' : '', onclick: () => setMode('model') }, 'From the model'),
    el('button', { class: modelMode ? '' : 'active', onclick: () => setMode('photo') }, 'Photo'));
  card.append(row('Door', modes));

  const side = el('div', { class: 'portrait-side' });
  if (modelMode) {
    const v = pictureView();
    const setView = (k, val) => { c.portrait_view = { ...pictureView(), [k]: val }; markDirty(); };
    side.append(el('p', { class: 'small' }, 'Rendered from your model in its idle pose, like the game\'s own pictures. They update when you build.'),
      el('div', { class: 'portrait-icon-row' }, iconView, el('span', { class: 'small muted' }, 'Grid icon'), stockView, el('span', { class: 'small muted' }, 'Stock icon')),
      rangeRow('Turn', -60, 60, 1, v.turn, (x) => setView('turn', x), (x) => `${Math.round(x)}°`),
      rangeRow('Zoom', 0.6, 1.8, 0.01, v.zoom, (x) => setView('zoom', x), (x) => `${(+x).toFixed(2)}×`),
      el('div', { class: 'row' }, el('button', { onclick: () => refreshPictures() }, 'Render again')),
      el('div', { id: 'portrait-note', class: 'small muted' }, portrait.note));
    side.querySelectorAll('input[type=range]').forEach(i => i.addEventListener('change', () => { pushUndo(); refreshPictures(); }));
    card.append(el('div', { class: 'portrait-layout' }, el('div', { class: 'portrait-drop still' }, view), side));
    drawDoor(view, true); drawIcon();
    portrait.redraw = () => { drawDoor(view, true); drawIcon(); };
    if (!portrait.rendered) refreshPictures();
    return card;
  }
  const has = !!portrait.img;
  const zoom = rangeRow('Zoom', 1, 4, 0.01, portrait.crop.zoom, (v) => { portrait.crop.zoom = v; drawDoor(view, true); }, (v) => `${(+v).toFixed(2)}×`);
  zoom.querySelector('input').addEventListener('change', () => schedulePortraitSave());
  zoom.querySelector('input').disabled = !has;
  let drag = null;
  view.addEventListener('pointerdown', (e) => { if (!portrait.img) { file.click(); return; } drag = { x: e.clientX, y: e.clientY, crop: { ...portrait.crop } }; view.setPointerCapture(e.pointerId); });
  view.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const k = DOOR_W / view.getBoundingClientRect().width, g = cropGeometry();
    const nx = g.freeX > 0 ? drag.crop.x - 2 * (e.clientX - drag.x) * k / g.freeX : 0, ny = g.freeY > 0 ? drag.crop.y - 2 * (e.clientY - drag.y) * k / g.freeY : 0;
    portrait.crop.x = Math.max(-1, Math.min(1, nx)); portrait.crop.y = Math.max(-1, Math.min(1, ny)); drawDoor(view, true);
  });
  view.addEventListener('pointerup', () => { if (drag) { drag = null; schedulePortraitSave(); } });
  view.addEventListener('wheel', (e) => {
    if (!portrait.img) return; e.preventDefault();
    portrait.crop.zoom = Math.max(1, Math.min(4, portrait.crop.zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08)));
    zoom.querySelector('input').value = portrait.crop.zoom; zoom.querySelector('.val').textContent = `${portrait.crop.zoom.toFixed(2)}×`;
    drawDoor(view, true); schedulePortraitSave();
  }, { passive: false });
  const drop = el('div', { class: 'portrait-drop' }, view);
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); choosePortraitFile(e.dataTransfer.files[0]); });
  const buttons = el('div', { class: 'row' },
    el('button', { onclick: () => file.click() }, has ? 'Change photo…' : 'Choose photo…'),
    el('button', { disabled: !has, onclick: () => { portrait.crop = { zoom: 1, x: 0, y: 0 }; renderPanel(); schedulePortraitSave(); } }, 'Reset crop'), file);
  side.append(
    el('p', { class: 'small' }, has ? 'Drag the photo to move it, scroll or use Zoom to size it. The dark band is where the game draws the name. The grid icon and the stock icon are still rendered from your model.'
      : 'Pick or drop a picture. It is cropped to the door\'s shape (136 × 188) and shown when a player picks this character.'),
    zoom, buttons, el('div', { class: 'portrait-icon-row' }, iconView, el('span', { class: 'small muted' }, 'Grid icon'), stockView, el('span', { class: 'small muted' }, 'Stock icon')),
    el('div', { id: 'portrait-note', class: 'small muted' }, portrait.note));
  card.append(el('div', { class: 'portrait-layout' }, drop, side));
  drawDoor(view, true); drawIcon();
  portrait.redraw = () => { drawDoor(view, true); drawIcon(); };
  if (!c.stock) refreshPictures();   // made before stock icons existed: render them now
  return card;
}

// Render the pictures again (after a change to the model, the name or the view) and store them.
let picturesBusy = null;
function refreshPictures() {
  if (picturesBusy) { picturesBusy.again = true; return picturesBusy.promise; }
  const job = { again: false };
  job.promise = (async () => {
    try {
      do {
        job.again = false;
        await savePictures(pictureMode() === 'photo');
      } while (job.again);
      portrait.note = pictureMode() === 'model' ? 'Saved to the project. Build to put them in the game.' : portrait.note;
    } catch (e) { portrait.note = `Could not render the pictures: ${e.message}`; status(portrait.note, 'err'); }
    finally { picturesBusy = null; updatePortraitNote(); if (portrait.redraw) portrait.redraw(); }
  })();
  picturesBusy = job;
  return job.promise;
}

let lastBuild = null;
function panelExport(p) {
  p.append(reviewCard());
  p.append(portraitCard());
  loadSavedPortrait().then((changed) => { if (changed && mode === 'export') renderPanel(); });
  p.append(el('h2', {}, 'Build into the game'),
    el('p', {}, `Builds the fighter data (stats and moves), animations, costume and select-screen pictures on top of ${fighterLabel(S.base.fighter)}${S.pascalpatch ? ', then a PascalPatch offline profile you can launch in melee-unlocked (Slippi off)' : ''}. Unsaved changes are saved first.`));
  const NOT_NEW = ['zelda', 'sheik', 'ice-climbers'], canAdd = !NOT_NEW.includes(S.base.fighter);
  const install = canAdd && project.character.install !== 'replace' ? 'new' : 'replace';
  p.append(row('Add', dropdown([['new', `As a new fighter (${fighterLabel(S.base.fighter)} stays)`, !canAdd], ['replace', `In place of ${fighterLabel(S.base.fighter)}`]], install,
    (v) => { pushUndo(); project.character.install = v; renderPanel(); })),
    el('p', { class: 'small muted' }, install === 'new'
      ? 'Added to the game with its own icon; nobody is replaced. With more than two new fighters the select screen gets pages (the arrow right of Roy). Up to three different new fighters per match, and it can fight its base fighter.'
      : canAdd ? `Takes ${fighterLabel(S.base.fighter)}'s icon and place in every mode.` : `${fighterLabel(S.base.fighter)} changes into another fighter mid-match, so this character can only replace it.`));
  p.append(row('Texture size', dropdown([128, 256, 512, 1024].map(v => [v, `${v} × ${v} (${v * v / 2 / 1024} KB)`]), project.rig.texture_size || 512,
    (v) => { pushUndo(); project.rig.texture_size = +v; })));
  const out = el('div', { class: 'build-result' });
  const show = (r) => {
    out.replaceChildren();
    if (!r) return;
    if (r.error) { out.append(el('p', { class: 'err' }, r.error)); return; }
    const steps = r.steps || [];
    out.append(el('ul', { class: 'steps' }, ...steps.map(s => el('li', { class: s.startsWith('warning') ? 'warn' : '' }, s))));
    if (r.build) out.append(el('p', { class: r.build.exit === 0 ? 'ok' : 'err' }, r.build.exit === 0 ? 'PascalPatch profile built. Ready to launch.' : `PascalPatch build failed (exit ${r.build.exit}).`));
    if (r.output) out.append(el('p', { class: 'small muted' }, `Files: ${r.output}`));
    out.append(el('details', {}, el('summary', {}, 'Full log'), el('pre', { class: 'log' }, JSON.stringify(r, null, 2))));
  };
  const run = async (label, fn) => {
    out.replaceChildren(el('p', {}, `${label}… this can take a minute.`)); status(`${label}…`);
    buttons.forEach(b => { b.disabled = true; });
    try { lastBuild = await fn(); show(lastBuild); status(`${label} done`, 'ok'); }
    catch (e) { lastBuild = { error: e.message }; show(lastBuild); status(`${label} failed: ${e.message}`, 'err'); }
    finally { buttons.forEach(b => { b.disabled = false; }); launch.disabled = test.disabled = !S.pascalpatch; }
  };
  const build = el('button', { class: 'primary', onclick: () => run('Building', async () => {
    const pictures = await refreshPictures().then(() => null, (e) => `warning: the select-screen pictures were not rendered (${e.message})`);
    await save(); const r = await api.post('/api/export');
    if (pictures) r.steps = [pictures, ...(r.steps || [])];
    return r;
  }) }, 'Build');
  const launch = el('button', { disabled: !S.pascalpatch, onclick: () => run('Launching', async () => { const r = await api.post('/api/launch'); return { steps: ['Game starting in a new window (offline, Slippi off).'], ...r }; }) }, 'Launch game');
  const test = el('button', { disabled: !S.pascalpatch, onclick: () => run('Building a test match', async () => {
    await save(); return api.post('/api/test_in_game', testMatch);
  }) }, 'Test in game');
  const buttons = [build, launch, test];
  p.append(el('div', { class: 'row' }, build, launch),
    S.pascalpatch ? el('p', { class: 'small' }, 'Build first after changes; Launch plays the last build.') : el('p', {}, 'PascalPatch is not set up: start the studio with --pascalpatch-repo, --pascalpatch-root and --port to build profiles and launch the game.'));
  // Test in game: straight into a match with the character, no menus (PascalPatch's Quick Match)
  const opponents = [['', `${fighterLabel(S.base.fighter)} (its base fighter)`], ...S.options.fighters.filter(f => f.id !== S.base.fighter).map(f => [f.id, f.label])];
  p.append(el('h2', {}, 'Test in game'),
    el('p', {}, 'Builds, then starts the game straight in a match with this character as player 1: no title or select screens. The test build puts it in place of its base fighter.'),
    row('Against', dropdown(opponents, testMatch.opponent, (v) => { testMatch.opponent = v; })),
    row('Player 2', dropdown([['human', 'A training dummy (Training Lab: Home turns it on)'], ['cpu1', 'CPU level 1'], ['cpu5', 'CPU level 5'], ['cpu9', 'CPU level 9'], ['none', 'Nobody']], testMatch.p2_player, (v) => { testMatch.p2_player = v; })),
    row('Stage', dropdown([['fd', 'Final Destination'], ['bf', 'Battlefield'], ['ys', "Yoshi's Story"], ['fod', 'Fountain of Dreams'], ['ps', 'Pokémon Stadium'], ['dl', 'Dream Land N64']], testMatch.stage, (v) => { testMatch.stage = v; })),
    el('div', { class: 'row' }, test),
    el('p', { class: 'small muted' }, 'No time limit and no stocks. Backspace restarts the match. With Frame Data and Hitbox Viewer installed in PascalPatch you see every move\'s frames and hitboxes as you test.'),
    out);
  show(lastBuild);
}
const testMatch = { opponent: '', p2_player: 'human', stage: 'fd' };

// ------------------------------------------------------------------ check: errors, and the character next to Melee's cast
let reviewInfo = null, reviewFor = null;
function reviewCard() {
  const card = el('div', { class: 'card review' }, el('h2', {}, 'Check'));
  const key = JSON.stringify([project.character.attributes, project.character.attribute_scales, project.moveset]);
  if (reviewFor !== key) {
    reviewFor = key; reviewInfo = null;
    api.post('/api/review', { character: project.character, moveset: project.moveset })
      .then((r) => { reviewInfo = r; }, (e) => { reviewInfo = { error: e.message }; })
      .then(() => { if (mode === 'export' && reviewFor === key) renderPanel(); });
  }
  if (!reviewInfo) { card.append(el('p', { class: 'muted' }, 'Comparing with every fighter in Melee…')); return card; }
  if (reviewInfo.error) { card.append(el('p', { class: 'err' }, reviewInfo.error)); return card; }
  const f = reviewInfo.findings, errors = f.filter(x => x.level === 'error');
  card.append(el('p', { class: errors.length ? 'err' : 'ok' }, errors.length ? `${errors.length} problem${errors.length > 1 ? 's' : ''} to fix before building.` :
    f.length ? "Ready to build. Some things stand out against Melee's cast:" : "Ready to build. Nothing is outside what Melee's own fighters have."));
  if (f.length) card.append(el('ul', { class: 'findings' }, ...f.map(x => el('li', { class: x.level },
    el('span', { class: 'chip' }, x.area === 'stats' ? 'Stats' : 'Moves'), ' ', x.message, ' ',
    el('a', { href: '#', onclick: (e) => { e.preventDefault(); setMode(x.area === 'stats' ? 'stats' : 'moves'); } }, 'Open')))));
  const rows = reviewInfo.moves;
  if (rows.length) {
    const rk = (r, of) => (r == null ? null : el('small', { class: r <= 3 ? 'rank top' : r >= of - 2 ? 'rank bottom' : 'rank' }, ` ${ordinal(r)}`));
    const table = el('table', { class: 'hb cast-moves' },
      el('caption', {}, `Normal attacks next to Melee's ${rows[0].of - 1} fighters (1st = fastest, strongest, safest, earliest KO)`),
      el('tr', {}, el('th', {}, 'Move'), el('th', {}, 'Hits'), el('th', {}, 'Dmg'),
        el('th', { title: 'Hard shield, first hit; aerials L-cancelled, hitting just before landing' }, 'Shield'),
        el('th', { title: 'Percent the hardest hitbox KOs Fox from the centre of Final Destination, no DI' }, 'KOs Fox')));
    for (const m of rows) {
      const tip = `Fastest: ${fighterLabel(m.fastest.fighter)} F${m.fastest.startup} · strongest: ${fighterLabel(m.strongest.fighter)} ${m.strongest.damage}%` +
        (m.safest ? ` · safest: ${fighterLabel(m.safest.fighter)} ${signed(m.safest.shield)}` : '') +
        (m.deadliest ? ` · KOs earliest: ${fighterLabel(m.deadliest.fighter)} ${m.deadliest.ko}%` : '');
      table.append(el('tr', { title: tip, class: m.changed ? 'changed' : '' },
        el('td', {}, m.name, m.source !== S.base.fighter ? el('small', { class: 'muted' }, ` (${fighterLabel(m.source)})`) : null),
        el('td', {}, `F${m.startup}`, rk(m.startup_rank, m.of)), el('td', {}, `${m.damage}%`, rk(m.damage_rank, m.of)),
        el('td', {}, m.shield == null ? '—' : signed(m.shield), rk(m.shield_rank, m.of)),
        el('td', {}, m.ko == null ? '—' : `${m.ko}%`, rk(m.ko_rank, m.of))));
    }
    card.append(el('details', {}, el('summary', {}, 'Compared with the cast'), table));
  }
  return card;
}

// ------------------------------------------------------------------ save / lifecycle
async function save() {
  status('Saving…');
  clearTimeout(autosaveTimer);
  await api.post('/api/save', project);   // the server drops the autosave once the files are written
  unsaved = false; $('#save').textContent = 'Save'; $('#status-right').textContent = ''; $('#autosave-state').textContent = ''; status('Saved', 'ok');
}

// ------------------------------------------------------------------ autosave: edits are kept in <project>/.studio until you save
let autosaveTimer = null;
function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(async () => {
    if (!unsaved) return;
    try { await api.post('/api/autosave', project); $('#autosave-state').textContent = `Autosaved ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`; }
    catch (e) { $('#autosave-state').textContent = 'Autosave failed'; }
  }, 4000);
}

// ------------------------------------------------------------------ dialogs and the File menu
function dialog(title, body, buttons) {
  const d = $('#dialog');
  return new Promise((resolve) => {
    const foot = el('div', { class: 'pp-dialog-foot' }, ...buttons.map(([label, value, primary]) =>
      el('button', { class: `pp-btn ${primary ? 'pp-btn--primary' : 'pp-btn--ghost'}`, onclick: () => { d.close(); resolve(value); } }, label)));
    d.replaceChildren(el('div', { class: 'pp-dialog-head' }, el('div', { class: 'pp-h2' }, title)), el('div', { class: 'pp-dialog-body' }, body), foot);
    d.onclose = () => resolve(null); d.onclick = (e) => { if (e.target === d) d.close(); };
    d.showModal();
  });
}
// Leaving the editor: offer to save first. Returns false when the user cancels.
async function settleUnsaved(action) {
  if (!unsaved) return true;
  const r = await dialog('Save your changes?', el('p', {}, `${project.character.display_name} has changes that are not saved. ${action} anyway?`),
    [['Cancel', null], ["Don't save", 'discard'], ['Save', 'save', true]]);
  if (r === 'save') { await save(); return true; }
  if (r === 'discard') { unsaved = false; await api.post('/api/autosave/discard').catch(() => {}); return true; }
  return false;
}
async function toStart(hash) {
  await api.post('/api/project/close'); location.href = '/' + (hash || '');
}
async function saveAs() {
  const name = el('input', { type: 'text', value: `${project.character.display_name} copy`, maxlength: 30, style: 'width:100%' });
  const body = el('div', { class: 'pp-field' }, el('label', {}, 'Name of the copy'), name,
    el('span', { class: 'pp-hint' }, 'The copy gets its own folder next to this one and opens here. This project keeps its last saved state.'));
  setTimeout(() => { name.focus(); name.select(); }, 0);
  if (await dialog('Save as', body, [['Cancel', null], ['Save copy', 'ok', true]]) !== 'ok') return;
  status('Saving a copy…');
  try {
    const edits = project; const info = await api.post('/api/project/save_as', { name: name.value.trim() });
    const fresh = await api.get('/api/state');   // the copy is open now: keep its id and name, write the current edits into it
    edits.character = { ...edits.character, id: fresh.character.id, display_name: fresh.character.display_name };
    await api.post('/api/save', edits);
    unsaved = false; location.reload();
    return info;
  } catch (e) { status('Save as failed: ' + e.message, 'err'); }
}
async function fileAction(what, arg) {
  $('#file-menu').hidden = true; $('#file-btn').setAttribute('aria-expanded', 'false');
  try {
    if (what === 'save') return await save();
    if (what === 'save_as') return await saveAs();
    if (what === 'folder') return await dialog('Project folder', el('p', { class: 'mono' }, S.path), [['Close', null, true]]);
    if (!(await settleUnsaved(what === 'recent' ? 'Switch projects' : 'Leave'))) return;
    if (what === 'recent') { status('Opening…'); await api.post('/api/project/open', { path: arg }); location.reload(); return; }
    await toStart({ new: '#new', open: '#open', roster: '#roster', close: '' }[what]);
  } catch (e) { status(e.message, 'err'); }
}
async function fillRecent() {
  let info; try { info = await api.get('/api/studio'); } catch (e) { return; }
  const others = info.recent.filter(r => !r.missing && r.path !== S.path).slice(0, 6);
  $('#recent-list').replaceChildren(...(others.length ? others.map(r => el('button', { role: 'menuitem', title: r.path, onclick: () => fileAction('recent', r.path) }, r.name, el('span', { class: 'kbd' }, r.base_fighter)))
    : [el('div', { class: 'none' }, 'No other recent projects')]));
}
function wireFileMenu() {
  const menu = $('#file-menu'), btn = $('#file-btn');
  btn.addEventListener('click', (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; btn.setAttribute('aria-expanded', String(!menu.hidden)); if (!menu.hidden) fillRecent(); });
  document.addEventListener('click', (e) => { if (!menu.hidden && !menu.contains(e.target)) { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); } });
  menu.querySelectorAll('[data-file]').forEach(b => b.addEventListener('click', () => fileAction(b.dataset.file)));
}
async function offerAutosave() {
  const a = S.autosave; if (!a) return;
  const when = new Date(a.saved_at * 1000).toLocaleString();
  const r = await dialog('Restore unsaved changes?', el('p', {}, `Character Studio kept edits to ${S.character.display_name} from ${when} that were never saved.`),
    [['Discard them', 'discard'], ['Restore', 'restore', true]]);
  if (r === 'restore') {
    history.undo.push(snapshot());
    for (const k of ['character', 'moveset', 'rig']) if (a[k]) project[k] = a[k];
    project.rig.segment_overrides ||= {}; project.rig.segment_scale ||= {}; project.rig.vertex_offsets ||= {};
    markDirty(); rigStale = true; afterProjectChange(); status('Restored your unsaved changes. Save to keep them.', 'ok');
  } else if (r === 'discard') {
    await api.post('/api/autosave/discard').catch(() => {});
  }
}

function afterProjectChange() {
  placeLandmarks(); if (mode === 'paint') paintColors();
  if (mode === 'sculpt' || mode === 'animate') { updateRigMesh(); scheduleRetarget(); }
  if (mode === 'moves') { refreshMoves(); return; }
  renderPanel();
}

function resize() {
  const r = canvas.parentElement.getBoundingClientRect();
  renderer.setSize(r.width, r.height, false); camera.aspect = r.width / r.height; camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);

let last = performance.now();
function loop(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (mode === 'animate' && anim.playing && anim.frames) {
    anim.acc += dt * 60 * anim.speed;
    if (anim.acc >= 1) {
      anim.frame = (anim.frame + Math.floor(anim.acc)) % anim.frames.length; anim.acc %= 1;
      $('#frame').value = anim.frame; updateFrameLabel(); updateRigMesh();
    }
  }
  orbit.update(); renderer.render(scene, camera);
  requestAnimationFrame(loop);
}

function wireUi() {
  document.querySelectorAll('#modes button').forEach(b => b.addEventListener('click', () => setMode(b.dataset.mode)));
  $('#mode-select').addEventListener('change', (e) => setMode(e.target.value));
  document.querySelectorAll('#view-tools button').forEach(b => b.addEventListener('click', () => b.dataset.view === 'frame' ? frameObject(mode === 'fit' || mode === 'paint' ? sourceMesh : rigMesh) : viewFrom(b.dataset.view)));
  $('#toggle-wire').addEventListener('change', applyMaterials);
  $('#toggle-texture').addEventListener('change', applyMaterials);
  $('#toggle-bones').addEventListener('change', applyMaterials);
  $('#undo').addEventListener('click', undo); $('#redo').addEventListener('click', redo);
  $('#save').addEventListener('click', () => save().catch(e => status('Save failed: ' + e.message, 'err')));
  $('#play').addEventListener('click', () => setPlaying(!anim.playing));
  $('#frame').addEventListener('input', (e) => { anim.frame = +e.target.value; setPlaying(false); updateFrameLabel(); updateRigMesh(); });
  $('#speed').addEventListener('change', (e) => { anim.speed = +e.target.value; });
  window.addEventListener('keydown', (e) => {
    if (e.target.matches('input[type=text], input[type=number], textarea, select')) return;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 's') { e.preventDefault(); e.shiftKey ? saveAs() : save(); return; }
    if ((e.ctrlKey || e.metaKey) && k === 'n') { e.preventDefault(); fileAction('new'); return; }
    if ((e.ctrlKey || e.metaKey) && k === 'o') { e.preventDefault(); fileAction('open'); return; }
    if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if ((e.ctrlKey || e.metaKey) && k === 'y') { e.preventDefault(); redo(); return; }
    if (k === ' ' && mode === 'animate') { e.preventDefault(); setPlaying(!anim.playing); return; }
    if (k === 'f') frameObject(mode === 'fit' || mode === 'paint' ? sourceMesh : rigMesh);
    if (k === '[') { brush.radius = Math.max(0.2, brush.radius * 0.85); renderPanel(); }
    if (k === ']') { brush.radius = Math.min(10, brush.radius * 1.15); renderPanel(); }
    if (k === 'escape') selectLandmark(null);
    if (mode === 'fit' && k === 'w') setGizmoMode('translate');
    if (mode === 'fit' && k === 'e') setGizmoMode('rotate');
    const idx = '1234567'.indexOf(k); if (idx >= 0) setMode(Object.keys(MODES)[idx]);
  });
  window.addEventListener('beforeunload', (e) => { if (unsaved) { e.preventDefault(); e.returnValue = ''; } });
}

async function main() {
  try {
    S = await api.get('/api/state');
  } catch (e) {
    if (/no project is open/.test(e.message)) { location.href = '/'; return; }   // the studio went back to its start screen
    status('Could not load project: ' + e.message, 'err'); return;
  }
  project = { character: S.character, moveset: S.moveset, rig: S.rig };
  project.rig.segment_overrides ||= {}; project.rig.segment_scale ||= {}; project.rig.vertex_offsets ||= {};
  $('#project-name').textContent = `· ${S.character.display_name} on ${S.base.fighter}`;
  if (S.model.texture) {
    texture = await textureLoader.loadAsync(S.model.texture);
    texture.flipY = false; texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 8;
  }
  buildSource(); buildRig(); placeLandmarks(); wireUi(); wireFileMenu(); resize();
  await setMode('fit');
  status(`Loaded ${S.character.display_name}: ${S.model.positions.length / 3} vertices, ${S.animations.length} animations`, 'ok');
  ensureRig().catch(() => {});
  requestAnimationFrame(loop);
  offerAutosave();
}
main();
