// Character Studio start screen. Plain JS on Pascal UI; every action is an /api call to studio_server.py.
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'class') e.className = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
const api = {
  async get(p) { const r = await fetch(p); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j; },
  async post(p, body = {}) {
    const r = await fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j;
  },
};
function toast(msg, kind = '') {
  const t = h('div', { class: `pp-toast ${kind ? 'pp-toast--' + kind : ''}` }, msg);
  $('#toasts').append(t); setTimeout(() => t.remove(), kind === 'danger' ? 9000 : 4500);
}
async function run(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast(e.message, 'danger'); throw e; }
}
const DEFAULT_TIP = 'Open a recent character or start a new one.';
document.addEventListener('mouseover', (e) => { const t = e.target.closest('[data-tip]'); $('#tip').textContent = t ? t.dataset.tip : DEFAULT_TIP; });

function frame(tabs, ...body) {
  return h('section', { class: 'pp-frame has-tab' },
    h('div', { class: 'pp-frame-tab' }, tabs.map((t, i) => h('span', { class: i ? 'is-dim' : '' }, t))), ...body);
}
function pageHead(title, sub, ...actions) {
  return h('div', { class: 'pp-page-head' },
    h('div', {}, h('h1', { class: 'pp-h1' }, title), sub ? h('p', { class: 'pp-muted', style: 'margin:4px 0 0' }, sub) : null),
    h('span', { class: 'pp-spacer' }), ...actions);
}
function field(label, input, hint) {
  return h('div', { class: 'pp-field' }, h('label', {}, label), input, hint ? h('span', { class: 'pp-hint' }, hint) : null);
}
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
}
// A character either joins the roster as a new fighter on its base's skeleton, or takes the base's place.
const fighterName = (id) => (S.fighters.find((f) => f.id === id) || { label: id }).label;
const basedOn = (p) => `${p.install === 'replace' ? 'Replaces' : 'Built on'} ${fighterName(p.base_fighter)}`;
const picUrl = (path) => `/api/project/picture?path=${encodeURIComponent(path)}&which=icon`;
function busy(title) {
  const lines = h('div', { class: 'pp-small pp-muted' }, 'Reading your disc and the model. This takes a few seconds.');
  const el = h('div', { class: 'busy' }, frame([title], h('div', { class: 'pp-progress is-indeterminate' }, h('span', {})), lines));
  document.body.append(el);
  return () => el.remove();
}

let S = null;
async function refresh() { S = await api.get('/api/studio'); $('#footer-dir').textContent = S.projects_dir; }

// Opening a project swaps the page for the editor (the server serves it at / once a project is open).
async function openProject(path) {
  const done = busy('Opening');
  try { await api.post('/api/project/open', { path }); location.href = '/'; } catch (e) { done(); toast(e.message, 'danger'); }
}

function needsDisc() {
  if (S.ready) return null;
  return h('div', { class: 'pp-notice pp-notice--warn' }, 'Set your Melee disc image first: the studio reads the base fighters from it. ',
    h('a', { href: '#settings' }, 'Open Settings'));
}

// ---- Home: recent projects
function projectCard(p) {
  const pic = h('div', { class: 'pic' });
  if (p.picture) pic.append(h('img', { src: picUrl(p.path), alt: '' }));
  else pic.append(h('span', { class: 'initial' }, (p.name || '?')[0].toUpperCase()));
  const forget = h('button', { class: 'pp-btn pp-btn--ghost pp-btn--sm', 'data-tip': 'Take it off this list. The folder is not touched.',
    onclick: async (e) => { e.stopPropagation(); await run(() => api.post('/api/project/forget', { path: p.path })); await refresh(); route(); } }, 'Remove');
  return h('div', { class: `pp-card project is-interactive ${p.missing ? 'is-missing' : ''}`, tabindex: 0, role: 'button', 'data-tip': p.path,
    onclick: () => !p.missing && openProject(p.path), onkeydown: (e) => e.key === 'Enter' && !p.missing && openProject(p.path) },
    pic,
    h('div', { class: 'body' },
      h('div', { class: 'pp-card-title' }, p.name),
      p.missing ? h('span', { class: 'pp-small pp-dim' }, 'Folder not found')
        : h('span', { class: 'pp-small pp-muted' }, `${basedOn(p)} · ${ago(p.updated)}`),
      p.autosave ? h('span', { class: 'pp-tag pp-tag--info', style: 'align-self:flex-start' }, 'Unsaved changes') : null),
    h('div', { class: 'pp-card-foot' }, h('span', { class: 'pp-spacer' }), forget));
}
// Examples open as your own copy in the projects folder; the originals stay as they came.
async function openExample(id) {
  if (!S.ready) { toast('Set your Melee disc image in Settings first.', 'danger'); location.hash = '#settings'; return; }
  const done = busy('Opening');
  try { await api.post('/api/example/open', { id }); location.href = '/'; } catch (e) { done(); toast(e.message, 'danger'); }
}
function exampleCard(e) {
  const pic = h('div', { class: 'pic' });
  if (e.picture) pic.append(h('img', { src: picUrl(e.path), alt: '' }));
  else pic.append(h('span', { class: 'initial' }, (e.name || '?')[0].toUpperCase()));
  return h('div', { class: 'pp-card project is-interactive', tabindex: 0, role: 'button',
    'data-tip': `${e.description} Opens your own copy, saved in ${S.projects_dir}.`,
    onclick: () => openExample(e.id), onkeydown: (ev) => ev.key === 'Enter' && openExample(e.id) },
    pic,
    h('div', { class: 'body' },
      h('div', { class: 'pp-card-title' }, e.name),
      h('span', { class: 'pp-small pp-muted' }, basedOn(e))));
}
function examplesFrame() {
  if (!S.examples.length) return null;
  return frame(['Examples', `${S.examples.length}`],
    h('p', { class: 'pp-small pp-muted', style: 'margin:0 0 10px' }, S.recent.length ? 'Ready-made characters to play, take apart, or start yours from. '
      : 'No projects of your own yet. Open an example to see how it is made, start from a model with New project, or ',
      h('a', { href: '#roster' }, S.recent.length ? 'Play them all' : 'play them all'), ' from the Roster.'),
    h('div', { class: 'projects' }, S.examples.map(exampleCard)));
}
function pageHome(root) {
  root.append(pageHead('Characters', 'Pick up where you left off, or start a new fighter from a model.',
    h('a', { class: 'pp-btn', href: '#open' }, 'Open…'), h('a', { class: 'pp-btn pp-btn--primary', href: '#new' }, 'New project')));
  const warn = needsDisc(); if (warn) root.append(warn);
  const ex = examplesFrame();
  if (S.recent.length) {
    root.append(frame(['Recent', `${S.recent.length}`], h('div', { class: 'projects' }, S.recent.map(projectCard))));
    if (ex) root.append(ex);
  } else if (ex) {   // a fresh install: the examples first
    root.append(ex);
  } else {
    root.append(frame(['Recent'], h('div', { class: 'pp-empty' },
      h('div', { class: 'pp-h3' }, 'No projects yet'),
      h('p', { class: 'pp-muted' }, 'Drop in a .glb, .obj, or a zipped .gltf to make your first character.'),
      h('a', { class: 'pp-btn pp-btn--primary', href: '#new' }, 'New project'))));
  }
}

// ---- New project
function pageNew(root) {
  root.append(pageHead('New project', 'A character starts from a 3D model and a Melee fighter whose moves and slot it takes.'));
  const warn = needsDisc(); if (warn) root.append(warn);
  const st = { upload: null, model: '', rotate: 0 };
  const fileInput = h('input', { type: 'file', accept: S.model_types.join(','), hidden: true });
  const dropText = h('div', { class: 'pp-small pp-muted' }, `${S.model_types.join('  ')}  ·  a .gltf goes in a .zip with its .bin and textures`);
  const dropBig = h('div', { class: 'big' }, 'Drop a model here');
  const drop = h('div', { class: 'drop', tabindex: 0, role: 'button', 'data-tip': 'Click to choose a model file, or drag one in.' }, dropBig, dropText, fileInput);
  const name = h('input', { class: 'pp-input', placeholder: 'Character name', maxlength: 40 });
  const path = h('input', { class: 'pp-input pp-mono', placeholder: 'or paste a path: C:\\Models\\hero.glb' });
  const fighter = h('select', { class: 'pp-input' }, S.fighters.map((f) => h('option', { value: f.id, selected: f.id === 'mario' }, f.label)));
  const author = h('input', { class: 'pp-input', placeholder: 'Optional' });
  const zup = h('input', { type: 'checkbox' });
  const install = h('select', { class: 'pp-input' }, h('option', { value: 'new' }, 'Add as a new fighter'), h('option', { value: 'replace' }, 'Replace the base fighter'));
  const facing = h('div', { class: 'facing' });
  const turns = [[0, 'Faces the camera (+Z)'], [90, 'Faces right'], [180, 'Faces away'], [270, 'Faces left']];
  const drawFacing = () => facing.replaceChildren(...turns.map(([deg, label]) =>
    h('button', { class: `pp-btn pp-btn--sm ${st.rotate === deg ? 'pp-btn--primary' : ''}`, type: 'button', onclick: () => { st.rotate = deg; drawFacing(); } }, label)));
  drawFacing();

  async function take(file) {
    dropBig.textContent = `Uploading ${file.name}…`; drop.classList.remove('has-file');
    try {
      const r = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file });
      const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText);
      st.upload = j.upload; path.value = '';
      if (!name.value) name.value = j.name.replace(/[_-]+/g, ' ');
      dropBig.textContent = file.name; dropText.textContent = `${(file.size / 1048576).toFixed(1)} MB · ready`; drop.classList.add('has-file');
    } catch (e) { dropBig.textContent = 'Drop a model here'; toast(e.message, 'danger'); }
  }
  drop.addEventListener('click', () => fileInput.click());
  drop.addEventListener('keydown', (e) => (e.key === 'Enter' || e.key === ' ') && fileInput.click());
  fileInput.addEventListener('change', () => fileInput.files[0] && take(fileInput.files[0]));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('is-over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('is-over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('is-over'); if (e.dataTransfer.files[0]) take(e.dataTransfer.files[0]); });
  path.addEventListener('input', () => { if (path.value) { st.upload = null; drop.classList.remove('has-file'); dropBig.textContent = 'Drop a model here'; } });

  const create = h('button', { class: 'pp-btn pp-btn--primary', onclick: async () => {
    if (!st.upload && !path.value.trim()) return toast('Choose a model first.', 'danger');
    const done = busy('Creating project');
    try {
      await api.post('/api/project/new', { upload: st.upload, model: path.value.trim(), name: name.value.trim(), base_fighter: fighter.value,
        author: author.value.trim(), rotate_y: st.rotate, z_up: zup.checked, install: install.value });
      location.href = '/';
    } catch (e) { done(); toast(e.message, 'danger'); }
  } }, 'Create project');

  root.append(frame(['Model'], drop, field('Model path', path)));
  root.append(frame(['Character'],
    h('div', { class: 'grid2' },
      field('Name', name, 'Shown on the character select screen.'),
      field('Base fighter', fighter, 'Your character uses this fighter\'s skeleton, moves and animations.'),
      field('In the game', install), field('Author', author)),
    field('Which way does the model face?', facing, 'Melee fighters face the camera. Pick the turn that makes yours do the same.'),
    h('label', { class: 'pp-switch', 'data-tip': 'Blender and some other tools export Z as up. Turn this on if the model lies on its back.' },
      zup, h('span', { class: 'pp-switch-track' }), h('span', {}, 'The model is Z-up'))));
  root.append(h('div', { class: 'toolbar' }, h('span', { class: 'pp-spacer' }), h('span', { class: 'pp-small pp-muted' },
    `Saved in ${S.projects_dir}`), create));
}

// ---- Open: a folder browser
async function pageOpen(root, folder) {
  root.append(pageHead('Open a project', 'A project is a folder with character.json and rig.json.'));
  const list = h('div', { class: 'browser' });
  const here = h('div', { class: 'path' });
  const places = h('div', { class: 'places' });
  const typed = h('input', { class: 'pp-input pp-mono', placeholder: 'Paste a folder path and press Enter' });
  const openHere = h('button', { class: 'pp-btn pp-btn--primary', disabled: true }, 'Open this project');
  let cur = null;
  async function go(f) {
    let r; try { r = await api.get(`/api/browse${f ? '?folder=' + encodeURIComponent(f) : ''}`); } catch (e) { return toast(e.message, 'danger'); }
    cur = r; here.textContent = r.folder; typed.value = r.folder;
    openHere.disabled = !r.is_project;
    places.replaceChildren(...r.places.map((p) => h('button', { class: 'pp-btn pp-btn--ghost pp-btn--sm', onclick: () => go(p) }, p)));
    const rows = [];
    if (r.parent) rows.push(h('div', { class: 'row', onclick: () => go(r.parent) }, h('b', {}, '↑'), '..'));
    for (const e of r.entries) {
      rows.push(h('div', { class: 'row', 'data-tip': e.project ? 'Double-click to open this project.' : e.path,
        onclick: () => go(e.path), ondblclick: () => e.project && openProject(e.path) },
        h('span', {}, e.project ? '◆' : '▸'), e.name, e.project ? h('span', { class: 'pp-tag pp-tag--accent' }, 'Project') : null));
    }
    if (!r.entries.length) rows.push(h('div', { class: 'pp-empty pp-small' }, 'No folders here.'));
    list.replaceChildren(...rows);
  }
  typed.addEventListener('keydown', (e) => e.key === 'Enter' && go(typed.value.trim()));
  openHere.addEventListener('click', () => cur && openProject(cur.folder));
  root.append(frame(['Folders'], places, typed, list, h('div', { class: 'toolbar' }, here, h('span', { class: 'pp-spacer' }), openHere)));
  await go(folder);
}

// ---- Roster
async function pageRoster(root) {
  root.append(pageHead('Roster', 'Several characters built together into one PascalPatch profile. One character per base fighter.'));
  let R = await api.get('/api/roster');
  const name = h('input', { class: 'pp-input', value: R.name, maxlength: 40 });
  const list = h('div', { class: 'pp-list' });
  const log = h('pre', { class: 'logview', hidden: true });
  const status = h('span', { class: 'pp-small pp-muted' });
  const count = h('span', { class: 'is-dim' }, `${R.entries.length}`);
  const draw = () => {
    count.textContent = `${R.entries.length}`;
    list.replaceChildren(...(R.entries.length ? R.entries.map((c) => h('div', { class: 'pp-list-row roster-row' },
      h('div', { class: 'thumb' }, c.picture ? h('img', { src: picUrl(c.path), alt: '' }) : (c.name || '?')[0]),
      h('div', { class: 'pp-grow' }, h('div', {}, c.name), h('div', { class: 'pp-small pp-dim' }, c.missing ? 'Folder not found' : basedOn(c))),
      h('button', { class: 'pp-btn pp-btn--ghost pp-btn--sm', onclick: () => save(R.characters.filter((p) => p !== c.path)) }, 'Remove')))
      : [h('div', { class: 'pp-empty pp-small' }, S.examples.length ? 'Nothing in the roster yet. Add the examples or your recent projects below.' : 'Nothing in the roster yet. Add characters from your recent projects below.')]));
  };
  async function save(chars) {
    R = await run(() => api.post('/api/roster', { id: R.id, name: name.value.trim() || 'My roster', characters: chars })); draw(); drawAdd();
  }
  name.addEventListener('change', () => save(R.characters));
  const add = h('div', { class: 'toolbar' });
  const examplesBtn = h('button', { class: 'pp-btn pp-btn--primary pp-btn--sm',
    'data-tip': 'Adds your own copies of the example characters, skipping any whose fighter slot the roster already uses.',
    onclick: async () => { R = await run(() => api.post('/api/roster/examples'), 'Examples added.'); await refresh(); draw(); drawAdd(); } }, '+ All examples');
  const drawAdd = () => {
    const free = S.recent.filter((p) => !p.missing && !R.characters.includes(p.path));
    const inRoster = new Set(R.entries.map((c) => c.id));
    const kids = S.examples.some((e) => !inRoster.has(e.id)) ? [examplesBtn] : [];
    kids.push(...free.map((p) => h('button', { class: 'pp-btn pp-btn--sm', onclick: () => save([...R.characters, p.path]) }, `+ ${p.name}`)));
    if (!kids.length) kids.push(h('span', { class: 'pp-small pp-dim' }, 'Every recent project is already in the roster. Open others from Open to list them here.'));
    add.replaceChildren(...kids);
  };
  async function build(launch) {
    if (!S.ready) return toast('Set your Melee disc image in Settings first.', 'danger');
    let job; try { job = await api.post('/api/roster/build', { launch }); } catch (e) { return toast(e.message, 'danger'); }
    log.hidden = false;
    while (true) {
      log.textContent = job.lines.join('\n'); log.scrollTop = log.scrollHeight;
      status.textContent = job.running ? `${job.title}…` : job.error ? 'Failed' : 'Done';
      if (!job.running) break;
      await new Promise((r) => setTimeout(r, 700));
      job = await api.get(`/api/job?id=${job.id}`);
    }
    if (job.error) toast(job.error, 'danger');
    else toast(job.result && job.result.failures.length ? `${job.result.failures.length} character(s) failed; see the log.` : 'Roster built.', job.result && job.result.failures.length ? 'danger' : 'ok');
  }
  draw(); drawAdd();
  const buildBtn = h('button', { class: 'pp-btn', 'data-tip': 'Build every character and write the PascalPatch profile.', onclick: () => build(false) }, 'Build');
  const playBtn = h('button', { class: 'pp-btn pp-btn--primary', 'data-tip': 'Build, then start Melee with this roster through PascalPatch (offline).',
    disabled: !S.pascalpatch, onclick: () => build(true) }, 'Build & play');
  root.append(h('section', { class: 'pp-frame has-tab' }, h('div', { class: 'pp-frame-tab' }, h('span', {}, 'Characters'), count), field('Roster name', name), list));
  root.append(frame(['Add'], add));
  root.append(frame(['Build'], h('div', { class: 'toolbar' }, status, h('span', { class: 'pp-spacer' }), buildBtn, playBtn),
    S.pascalpatch ? null : h('p', { class: 'pp-small pp-muted' }, 'Set the PascalPatch folders in Settings to build a profile and play.'),
    h('p', { class: 'pp-small pp-dim path' }, R.file), log));
}

// ---- Settings
function pageSettings(root) {
  root.append(pageHead('Settings', 'Remembered for next time.'));
  const s = S.settings;
  const inp = (v, ph) => h('input', { class: 'pp-input pp-mono', value: v || '', placeholder: ph || '' });
  const iso = inp(s.iso, 'C:\\Games\\Melee.iso'), dir = inp(s.projects_dir, S.projects_dir);
  const repo = inp(s.pascalpatch_repo), ppRoot = inp(s.pascalpatch_root), port = inp(s.port, 'melee_port.exe'), cwd = inp(s.port_cwd);
  const saveBtn = h('button', { class: 'pp-btn pp-btn--primary', onclick: async () => {
    S = await run(() => api.post('/api/settings', { iso: iso.value.trim(), projects_dir: dir.value.trim(), pascalpatch_repo: repo.value.trim(),
      pascalpatch_root: ppRoot.value.trim(), port: port.value.trim(), port_cwd: cwd.value.trim() }), 'Settings saved.');
    $('#footer-dir').textContent = S.projects_dir;
  } }, 'Save');
  root.append(frame(['Melee'], field('Your Melee disc image', iso, 'Your own NTSC 1.02 (GALE01) ISO. The studio only reads it.')));
  root.append(frame(['Projects'], field('New projects go in', dir, 'Leave empty for the default.')));
  root.append(frame(['PascalPatch'],
    h('div', { class: 'grid2' }, field('PascalPatch program folder', repo), field('PascalPatch data folder', ppRoot),
      field('Game (melee_port.exe)', port), field('Game working folder', cwd)),
    h('p', { class: 'pp-small pp-muted' }, 'Opening the studio from PascalPatch fills these in.')));
  root.append(h('div', { class: 'toolbar' }, h('span', { class: 'pp-spacer' }), saveBtn));
}

// ---- routing
const PAGES = { home: pageHome, new: pageNew, open: pageOpen, roster: pageRoster, settings: pageSettings };
async function route() {
  const [name, arg] = (location.hash.slice(1) || 'home').split('/');
  const page = PAGES[name] ? name : 'home';
  for (const a of document.querySelectorAll('.pp-nav-item')) a.toggleAttribute('aria-current', a.getAttribute('href') === `#${page}`);
  for (const a of document.querySelectorAll('.pp-nav-item[aria-current]')) a.setAttribute('aria-current', 'page');
  const root = $('#page'); root.replaceChildren();
  try { await PAGES[page](root, arg ? decodeURIComponent(arg) : undefined); } catch (e) { root.append(h('div', { class: 'pp-notice pp-notice--danger' }, e.message)); }
}
$('#new-top').addEventListener('click', () => { location.hash = '#new'; });
window.addEventListener('hashchange', route);
(async () => {
  await refresh();
  if (S.project) { location.href = '/'; return; }   // a project is already open in this studio
  if (!S.ready && !location.hash) { location.hash = '#settings'; return; }   // hashchange routes
  route();
})();
