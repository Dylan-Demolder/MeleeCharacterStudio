# Character authoring status

The current core supports glTF/GLB structural validation, configurable count/file budgets, deterministic package export, checksum generation and basic reference-skeleton name mapping. It does not yet retarget animation transforms, render a preview, convert assets to Melee formats, or compose a playable fighter. Those features require a validated runtime integration and are intentionally not implied by package export.


## Source-preserving edits

`ProjectEditor` provides deterministic move and attribute edits without changing the source project. `edit-move` and `set-attribute` write a new project directory through a staging directory and refuse in-place output. Edits are validated before they are committed.


The optional Tk authoring view uses the same headless `StudioController`; it edits move and attribute data but does not render a game preview or convert assets to Melee formats.


`preview-model` emits a deterministic SVG skeleton scene from validated glTF nodes. It is an authoring preview, not a Melee renderer or playable test scene.


Skeleton retargeting applies deterministic rest-pose translation, quaternion rotation, scale, and bone-length deltas. It is an authoring transform, not a game-format exporter.


Attribute calibration files carry explicit units, ranges, defaults, and provenance. The validator does not provide Nintendo calibration values; those must come from a legally sourced, documented reference.

## Editor

The authoring front end is the local web editor (`melee-character studio
<project> --iso <GALE01.iso>`; see the README for modes). Sections below that
mention the Tk or Qt workspaces describe the retired desktop editors; the
underlying Python pipeline they describe is unchanged.

## Workspace tabs (retired desktop editor)

The authoring GUI is organised around the real creation workflow: inspect the
model, inspect animation clips, choose approved moves, configure attributes,
validate, then export a new source-preserving project. A model preview and
animation timeline are deterministic authoring tools, not a claim of in-game
rendering. Move assignment records the approved move reference and validates
slot conflicts before export.

## Character Setup roster

The Character Setup tab lists the 26 Melee base fighters and lets an author
select the base-fighter/skeleton mode for the project. Clone mode is the first
supported authoring capability. Entries such as Ganondorf/Captain Falcon and
Roy/Marth expose compatible skeleton modes but do not imply that Nintendo game
assets are included or that a new character is playable yet.

The Model Viewer also exposes the selected base fighter and an animation-state
selector. “Preview selected animation” jumps to the Animation Viewer; imported
clips are marked available and required slots without a source are marked
missing. This is an author-owned import workflow, not bundled game animation
data.

## Local Melee/decomp asset sources

The repository can discover fighter source files from a user-owned ISO
extraction or local decomp checkout. Fighter files use names such as
`PlMr.dat` (Mario model/skeleton data) and `PlMrAJ.dat` (Mario animation/move
data). Use:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli discover-assets \
  /path/to/melee-decomp --fighter mario

PYTHONPATH=core/src python -m melee_character_studio.cli stage-assets \
  /path/to/melee-decomp \
  /home/$USER/.local/share/melee-character-studio/game-assets/mario \
  --fighter mario
```

Staging is local-only. It does not copy game data into a project export or
package. Character Studio currently indexes and stages these HSD/dat sources;
a dedicated HSD object/mesh/animation converter is still required before the
raw files can render as glTF in the viewer.

## Responsive viewer pipeline

Model and animation scene loading now runs through a small `ThreadPoolExecutor`
worker pool (`character-assets` threads). Tk callbacks only schedule work and
apply completed results, so validation, buffer decoding, and animation sampling
do not block the UI thread. The glTF viewer renders mesh primitive triangles
alongside the skeleton. Animation playback samples translation/rotation/scale
channels, supports play/pause, frame stepping, scrubbing, and runs scene loads
through the same worker pool.

The current renderer is a portable 2D projected preview. A native OpenGL/Vulkan
3D backend can be added later without changing the project or worker APIs.

## HSD skeleton inspection status

The Qt viewer can now load a local `PlXX.dat` source, resolve its public
`ftData...` root, and traverse the relocated `HSD_Joint` tree. For example:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli inspect-hsd-model \
  /path/to/orig/GALE01/files/PlFc.dat

PYTHONPATH=core/src python -m melee_character_studio.cli qt-gui project \
  --source /path/to/orig/GALE01/files/PlFc.dat
```

The HSD PObj GX display-list and indexed vertex decoder now produces a real
mesh preview. Falco base data reports 67 joints, 25 mesh parts, and 5,212
triangles. `PlFcAJ.dat` is scanned as 222 concatenated HSD `FigaTree` clips.
The Animation Player sizes its timeline to the selected clip and loops playback
at that clip boundary, keeping frame-data overlays synchronized. The Qt
Animation Player lists those Falco actions and samples their packed
transform tracks. Envelope skinning and animated playback run in worker tasks.

Costume archives can be layered over the shared base geometry. A costume
PObj may omit its position stream because the game resolves that stream from
`PlFc.dat`; use the local base archive as the model source and the costume as
the texture source:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli qt-gui project \
  --source /path/to/PlFc.dat --texture-source /path/to/PlFcNr.dat

PYTHONPATH=core/src python -m melee_character_studio.cli convert-hsd \
  /path/to/PlFc.dat /tmp/falco-nr.gltf --texture-source /path/to/PlFcNr.dat \
  --animation-source /path/to/PlFcAJ.dat --clip 3
```

This path decodes the costume TEX0 streams, UVs, materials, local texture
images, and HSD TObj clamp/repeat/mirror wrapping while retaining the base
position data. Exported glTF contains separate
material primitives and embedded PNG images. TEV composition, texture matrices,
wrapping edge cases, and exact GX lighting are still tracked as renderer work;
the viewer does not claim those unsupported effects are game-correct. The
local validation pass currently layers the `Nr` costume archive for all 26
playable base-fighter archives; auxiliary boss archives are intentionally not
part of the playable roster.

The decomp validation pass currently covers all 26 primary fighter files:

```text
26/26 PlXX.dat meshes decoded
26/26 PlXXAJ.dat animation archives scanned
5,288 FigaTree clips indexed across the roster
```

The Qt path keeps these sources local and does not package them into the
repository or distributed character packages. Static HSD geometry and clip
indexes use a bounded worker-side cache; animation frames copy only mutable
vertex arrays before applying skinning, avoiding a full archive parse on every
frame.

Project package export now checks move `animation` bindings against imported
glTF animation names and clip frame durations. A package is rejected when a
move references a missing clip or its timing/collision window exceeds that
clip.

## Moveset timeline

The Moveset Editor includes a clickable timeline showing startup, active, and
recovery phases. Hitbox windows are shown in red and hurtbox windows in blue.
Selecting a frame on the timeline also seeks the HSD Animation Player when a
clip is loaded.

## Animation pose editing

The Animation Player supports local pose deltas at a selected clip, frame, and
joint. HSD playback and export support translation, rotation, and scale;
glTF/GLB playback also applies translation, rotation, and scale deltas. Matrix-authored
nodes are decomposed to TRS before a pose delta is applied. Pose edits are stored in `character.json` as
`animation_edits` and are applied during worker-thread HSD playback. This is
a non-destructive authoring preview. **Export HSD animation…** can bake local
translation deltas into sampled FigaTree tracks and writes a new AJ archive
while preserving its relocation and public tables. Topology and new-joint edits remain gated.

## Fixed-size HSD stream patching

A conservative `patch-hsd` command can write source-space position, normal, or
UV pairs or position/normal triples into an existing local HSD data stream without changing archive
size, relocation tables, or file layout:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli patch-hsd \
  /path/to/source.dat /tmp/patched.dat /path/to/edits.json
```

Each edit specifies `stream_offset`, `index`, `stride`, `type` (`s8`, `u8`,
`s16`, `u16`, or `f32`), optional fixed-point `frac`, and a two- or
three-component `value`. HSD
PObj decoding now retains this stream/index provenance for decoded position
vertices, which is the basis for connecting project edits to source-space
patches. The command validates bounds and relocations before writing. It is intentionally
source-space and does not yet convert topology, TEV state, animation archives,
or full runtime packages.

## Imported glTF materials

Author-owned glTF and GLB files are loaded through the same worker path as HSD
sources. The generic importer now reads `TEXCOORD_0`, base-color factors, and
PNG images from data URIs, external files, or image buffer views. The Qt viewer
samples those UVs in its portable renderer. Unsupported image encodings remain
flat-material previews instead of being silently treated as valid textures.

## Mesh edits and move assignments

For local HSD sources, the Qt Model Viewer also provides **Export HSD vertex
patch…**, which writes fixed-size source-space position edits in a worker task.
Topology, UV, normal, and TEV edits remain glTF/authoring outputs until their
runtime encoders are implemented.

The Qt Model Viewer includes non-destructive mesh transform and individual
vertex editors for scale, translation, per-vertex coordinates, U/V
coordinates, and normals. It also supports mesh-part duplication, deletion,
and triangle-winding reversal. A normal-overlay toggle draws sampled vertex
normal vectors in both the Model Viewer and Animation Player for inspection.
The portable renderer also applies a lightweight normal-based Lambert shading
pass; this is useful feedback but is not a replacement for the game GX TEV and
lighting pipeline. Vertex, UV, normal, and topology operations are
stored as authoring metadata in the project and emitted when exporting glTF.
The Model Viewer also provides a single **Reset mesh edits** action that clears
these edits and reloads the untouched source. The edited scene can be exported with **Export glTF…**;
export runs on the worker pool. The Moveset Editor can assign a local HSD
`FigaTree` clip to each authored move, edit startup/active/recovery frame
counts, validate non-negative timing values, edit bone-attached hitboxes and
hurtboxes with active frame windows, display both collision types over the
animation player, and save the project directory without blocking the UI. Hitbox data is authoring
metadata only; runtime HSD collision export remains a separate validation
stage.

The HSD preview now reads local `HSD_Material` diffuse/ambient/specular
colors when available and uses back-to-front projected triangle sorting in the
portable Qt fallback renderer. This is not a replacement for GX depth, lighting, or full TEV state. Costume
TEX0/UV layers are now sampled in the portable renderer when a shared base
source is supplied; generated UV and complete TEV composition remain explicit
unsupported layers.

Local HSD image descriptors are now decoded for the common GameCube formats
`I4`, `I8`, `IA4`, `IA8`, `RGB565`, `RGB5A3`, `RGBA8`, and `CMPR`. Converted
glTF files embed decoded PNG texture data, TEXCOORD_0 data, and separate
base-color materials when the source HSD object references an image. Palette-
indexed and full TEV material networks remain separate follow-up work.

The normal glTF preview path now evaluates `JOINTS_0`/`WEIGHTS_0` skinning and
inverse-bind matrices for imported animated assets. This keeps converted HSD
models usable after they are reloaded as ordinary glTF rather than requiring
the raw HSD source every session.

### HSD skin/material status

The animation skin path uses the real `HSD_Joint.mtx` envelope matrices and the
decomp-compatible `HSD_MtxSRT` row ordering. The same envelope pipeline is
used for the static HSD model scene and animated frames. This replaces the
former inferred-Euler inverse-bind approximation.

`PlFc.dat` does contain an 8×8 local HSD image descriptor, but its PObj vertex
descriptors are `[PNMTXIDX, TEX0MTXIDX, TEX1MTXIDX, POS, NRM]` and contain no
`TEX0` UV stream. The viewer therefore does not apply that image as a body texture by itself.
It reports that UV/TEV mapping is pending rather than displaying an incorrect
texture. When a costume archive supplies shared geometry positions and TEX0
UVs, those costume images are applied instead; the base image is retained for
the later generated-UV and TEV implementation.

For Falco, the base `PlFc.dat` model references an 8×8 CMPR image used by the
HSD material setup. Costume archives such as local `PlFcNr.dat` contain the
larger 16–256px CMPR image descriptors and `TEX0` vertex streams. Combining
those material/vertex descriptors with the base geometry is the next texture
milestone; the current viewer intentionally does not pretend the base 8×8
image is Falco’s body texture.

The HSD skin implementation also now applies the per-PObj model-node matrix
used by `_HSD_mkEnvelopeModelNodeMtx`. Without that matrix, parts below a
skeleton root can be translated or rotated even when the bone inverse matrix
is correct.

## Native-first playability evidence (retarget 2026-09-24)

Playability evidence now targets the melee-unlocked native port (primary)
with Dolphin as a secondary cross-check; PascalPatch tracks the work as N-series
tasks in its `docs/plan-tracker.md`. This section restates the gates; it
promotes none of them.

- Gates 1–3 (typed decode of `ftLoadCommonData`/`ftData`, attribute mapping,
  moveset → action-state conversion) are format work and unchanged by the
  retarget.
- Gate 4 (composition + loader hook): deliver composed fighter files via a
  PascalPatch profile ISO with the DOL untouched, and the loader/table hook as a
  profile Gecko list baked at recompile, so upstream's vanilla-DOL invariant
  holds. Hook, attribute-table and callback validation markers are expected
  as `OSReport` output in `melee_port.log`; that channel must be confirmed
  first (PascalPatch's N2 spike) before any marker counts as evidence.
- Gate 5 (character select + offline match): drive boot → CSS → match with
  the port's input-automation scripts instead of `.dtm` movies; require the
  same log markers as before (symbol resolution, attribute-table validation,
  callback/action-table validation, character-select completion, match start)
  plus a clean `validate_native.py` checkpoint run. GDB-stub fighter reads
  remain available only as a secondary cross-check.
- No gate has been passed on the native runtime. `game_integration: false`
  stands until all five gates have direct tests and native evidence.

### Slot replacement (`compose-fighter-slot`)

A vanilla DOL finds fighters by their existing `ftData<Base>` symbol, so the
Tier B route keeps that root and rewrites the data it points at.
`compose-fighter-slot` writes the package's attributes into the base
fighter's `ftCo_DatAttrs` table (`ftData+0x0`; offsets from decomp
`src/melee/ft/types.h`, confirmed against real `PlFc.dat`/`PlFx.dat` values).
The archive layout, relocations and symbols stay byte-identical, and a
`<file>.slot.json` report records the base/output hashes that PascalPatch checks
before overlaying the file. `examples/ember` was observed on the native port
in an offline VS match (2026-09-24) in Fox's slot. It replaces Fox; it does not
add a roster slot, and model/animation conversion is not part of this route.

## Importing a model and retuning moves (Tier B, observed 2026-09-24)

`import-fighter-model <project> <base PlXxNr.dat> <out>` turns a static glTF
into the base fighter's default costume. It was first proved on Captain
Falcon with a model that is not distributed here; every project in
`examples/` goes through the same route.

1. `rig.json` names the model, a Y rotation that makes it face +Z, the base
   fighter, and ~20 joint landmarks measured on the model in its own pose.
2. `retarget_mesh` maps each body segment onto the matching base bone
   (`BASE_SEGMENTS`): limbs and torso stretch along the bone so both ends
   land on base joints; head, hands, feet and clavicles keep the model's
   proportions. The chain of swing rotations un-poses the mesh into the base
   T-pose, and nearest-segment weights are smoothed over the welded mesh
   (parent/child blending only), limited to 3 influences and quantized to
   tenths.
3. `write_costume` emits float position/normal/UV streams, envelope PObjs of
   at most 10 envelopes, and a 256x256 CMPR texture with a copy of the
   template DObj's material. The new PObj chain is hosted by DObjs 0 (high
   poly) and 34 (low poly) of Captain Falcon so every index in the fighter's
   visibility tables keeps its meaning; all other original PObjs point at an
   empty display list.
4. HSD rule that matters (decomp `pobj.c` `SetupEnvelopeModelMtx`): when the
   owning joint has no model-node matrix, a single weight-1 envelope uses the
   joint matrix *without* its inverse bind, so those vertices are written in
   that joint's bind-local space. Writing them in model space produces
   spikes in game.

Moves are retuned in `compose-fighter-slot`: a move with `actions` lists base
action names (`SpecialLw`, `AttackLw4`, ...) and `tuning` (`damage` or
`damage_scale`, `angle`, `knockback_growth`, `base_knockback`, ...).
`fighter_moves.py` walks the action scripts with the decomp's command
lengths and edits only hitbox fields in place, so timings, bones and every
pointer stay valid. Zero-damage detection boxes are never touched.

Native evidence: a PascalPatch profile carrying the composed `PlCa.dat` and
`PlCaNr.dat` boots on the Slippi-free melee-unlocked build. The Falcon slot
renders as the imported model driven by Falcon's animations, and the same scripted
inputs make down-B (Dragon's Rage) deal 18% and KO Ness from 0% where the
vanilla Falcon Kick dealt 12% and did not. The character replaces Captain Falcon
(no new roster slot), keeps Falcon's animations, CSS portrait and name, and
`game_integration` stays `false` for the unchanged gates.

## Web editor internals (2026-09-24)

- `studio_server.py` (stdlib HTTP, 127.0.0.1 only) exposes `/api/state`,
  `/api/retarget`, `/api/animation`, `/api/moves`, `/api/save`,
  `/api/export`, `/api/launch`; the browser never touches game files.
- `anim_bake.py` ports `HSD_FObjInterpretAnim`/`splGetHelmite` from the
  decomp, so Animate mode uses the game's own interpolation. A clip bakes in
  ~6 ms; the previous per-frame sampler took ~1.5 s per frame and read track
  start frames as unsigned.
- `rig.json` edits the editor writes: `landmarks`, `segment_overrides`
  (painted vertex -> body part), `segment_scale` (part -> factor),
  `vertex_offsets` (vertex -> bind-space delta), `texture_size` (default 512).
- The CMPR encoder uses principal-axis endpoints plus a least-squares refit
  (44 dB PSNR on a 512x512 character texture).

## Any-slot imports, model kit and roster builds (not yet disc-validated)

These extend the Captain Falcon route to every slot. The unit tests use synthetic archives only. Real-disc validation is the checklist in `examples/README.md`.

- **Derived skeleton map.** `base_skeleton.py` reads the fighter's joint tree together with `PlCo.dat`:
  - `pData[4]`, the per-fighter `ftPartsTable`
  - `pData[5]`, the virtual parts

  `auto_segments` uses these to build the segment table that `BASE_SEGMENTS` hard-codes for Captain Falcon. Missing toe and hand ends are synthesised. Each owner walks up to the nearest joint that has an envelope matrix.

  In `rig.json`, `root_symbol`, `host_dobjs` and `template_dobj` accept `"auto"`. `inspect-skeleton` prints the derived map and compares it with `BASE_SEGMENTS` where a hard-coded table exists.
- **Retarget options.**
  - `segment_modes` sets each part to `axial` (stretch along the bone, the default for limbs) or `uniform`.
  - `segment_ranges` assigns vertices to parts from the model kit, which skips nearest-bone guessing.
  - `rigid: "<part>"` with `scale_factor` binds the whole mesh to one owner (a vehicle or a creature with no limbs).
- **Move tuning.**
  - `actions` entries may be globs (`Attack*4*`). A glob that matches no action becomes a skip in the slot report.
  - `tuning` gains `size_scale`, `knockback_scale` and a named `element` (`fire`, `electric`, `ice`, `dark`, `sleep`, ...).
- **Relative stats.** `character.json` `attribute_scales` multiplies the base value, and integer attributes are rounded. A key can't be both scaled and set absolutely.
- **Model kit (`model_kit.py`).** `model.json` describes an original model:
  - landmarks and a palette
  - primitives anchored to landmarks: capsule, cylinder, cone, sphere, box (centre/size or from/to), extrude
  - `mirror` parts
  - pixel-art decals on box faces

  `generate-model` writes the glTF and atlas. It also writes `model_preview.py`'s four-view PNG and the matching `rig.json` fields. The kit's axes are Y up and facing +Z, with the character's right on -X.
- **Roster builds (`roster.py`).** `build-roster` runs `build_character` for every project in `roster.json`:
  1. package
  2. `PlXxAJ.dat` with borrowed moves
  3. `PlXx.dat`
  4. `PlXxNr.dat`

  It rejects two projects on one slot, keeps building after a failure, and writes `roster-report.json` plus a PascalPatch `mode: offline` profile. The studio's Export button uses the same `build_character`.
