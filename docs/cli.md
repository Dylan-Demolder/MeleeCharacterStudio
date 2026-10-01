# Command line

Everything the studio app does can also be run from the command line. Run these from the
checkout with `PYTHONPATH=core/src`, or install with `pip install -e .` and use
`melee-character-studio` in place of `python -m melee_character_studio.cli`.

Paths like `/path/to/GALE01.iso` and `PlFc.dat` are files from your own disc. Keep them, and
everything built from them, outside the repository.

## Build characters and rosters

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli generate-model examples/glacier
PYTHONPATH=core/src python -m melee_character_studio.cli inspect-skeleton donkey-kong --iso /path/to/GALE01.iso
PYTHONPATH=core/src python -m melee_character_studio.cli build-character examples/glacier --iso /path/to/GALE01.iso --out build/glacier
PYTHONPATH=core/src python -m melee_character_studio.cli build-roster examples/roster.json --iso /path/to/GALE01.iso \
  --out build/roster --profile /path/to/pascalpatch-data/profiles/examples.json
```

- `generate-model` builds a project's `model.json` spec into an original low-poly glTF with a texture atlas. The spec is primitives attached to rig landmarks (capsules, cylinders, cones, spheres, boxes, extrusions, mirrored parts, pixel-art decals). It also writes a four-view `preview.png` and updates `rig.json` (landmarks and per-segment vertex ranges).
- `inspect-skeleton` prints the part→joint map and body segments that the importer derives from the fighter's own skeleton and `PlCo.dat`.
- `build-character` runs the full export: package, borrowed moves, fighter data, then costume.
- `build-roster` runs `build-character` for every project in a roster and writes a PascalPatch offline profile.

`character.json` may use `attribute_scales` (multipliers of the base fighter's values) as well as absolute `attributes`. See `examples/README.md` for the example characters and their testing checklist.

## Step by step: package, fighter data, costume

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli export examples/nova build/nova.melee-character
PYTHONPATH=core/src python -m melee_character_studio.cli compose-fighter-slot build/nova.melee-character /path/to/PlMs.dat build/PlMs.dat
PYTHONPATH=core/src python -m melee_character_studio.cli import-fighter-model examples/nova /path/to/PlMsNr.dat build/PlMsNr.dat
```

`compose-fighter-slot` writes attributes and retunes move hitboxes;
`import-fighter-model` rigs the project's glTF onto the base skeleton and
writes the default costume. PascalPatch installs both through a profile
`characters` entry (`fighter_file` + `costume_file`). See
`docs/authoring.md` for the pipeline and its limits.

## Compose a fighter archive

For an offline-gameplay `.melee-character` package and a user-owned base fighter archive, create a deterministic, loader-compatible HSD archive without modifying the source archive:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli compose-fighter-hsd \
  nova.melee-character /path/to/PlFc.dat build/PlNova.dat
```

The command validates package checksums, preserves the base graph, and uses `serialize_hsd_graph` to allocate a package-specific `ftData<Id>` root plus metadata symbol. It does not read or write an ISO. To produce a modified ISO, hand the output to PascalPatch: the native-first path replaces data files inside a PascalPatch profile ISO and leaves the DOL untouched (the generated-DOL route is deferred to PascalPatch's Tier C recompiler fork); user game files stay outside this repository.

## Validate and edit projects

The core CLI can validate a model or export a project:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli validate-model model.gltf
PYTHONPATH=core/src python -m melee_character_studio.cli export project character.melee-character
PYTHONPATH=core/src python -m melee_character_studio.cli validate-project project
PYTHONPATH=core/src python -m melee_character_studio.cli edit-move project edited-project jab a
PYTHONPATH=core/src python -m melee_character_studio.cli set-attribute project edited-project weight 90
PYTHONPATH=core/src python -m melee_character_studio.cli gui project
PYTHONPATH=core/src python -m melee_character_studio.cli preview-model model.gltf skeleton.svg
PYTHONPATH=core/src python -m melee_character_studio.cli validate-library approved-moves.json
PYTHONPATH=core/src python -m melee_character_studio.cli validate-calibration attributes.json
```

## Open the editor on one project

Character Studio's editor is a local web app: Python serves the project and
does every file operation, and a three.js viewport in the browser does the
rendering and interaction. No extra Python packages are needed; three.js
(MIT) is vendored under `core/src/melee_character_studio/web/vendor`.

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli studio examples/nova \
  --iso /path/to/GALE01.iso \
  --pascalpatch-repo /path/to/PascalPatch --pascalpatch-root /path/to/pascalpatch-data \
  --port /path/to/melee_port.exe --port-cwd /path/to/melee-unlocked
```

The ISO and PascalPatch paths are remembered in
`~/.melee-character-studio/config.json`; base-fighter files are read from
your disc into `~/.melee-character-studio/cache`, never into the project.

## Convert HSD files from your disc

The local source reference is the official decompilation project:

```text
https://github.com/doldecomp/melee
```

Its user-provided `orig/GALE01/files/PlXX.dat` and `PlXXAJ.dat` files can be
discovered and staged with the `discover-assets` and `stage-assets` commands.
The HSD converter decodes local fighter geometry, animation,
materials, and common texture formats. Costume archives are layered over their
shared base position streams; the exact GX TEV/lighting state remains a
separate renderer task.

Local HSD conversion is available for user-owned decomp/ISO assets:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli convert-hsd \
  /path/to/orig/GALE01/files/PlFc.dat /tmp/falco.gltf

# Include one real FigaTree action (clip 3 in this example)
PYTHONPATH=core/src python -m melee_character_studio.cli convert-hsd \
  /path/to/orig/GALE01/files/PlFc.dat /tmp/falco-walk.gltf \
  --animation-source /path/to/orig/GALE01/files/PlFcAJ.dat --clip 3
```

The converter exports decoded mesh parts, a joint skin, inverse bind matrices,
and an optional sampled HSD animation clip. HSD animation clips are also loaded
directly from the concatenated `PlFcAJ.dat` source by the editor's Animate mode.
HSD diffuse materials and common local image formats are decoded. To layer a
costume, pass `--texture-source /path/to/PlFcNr.dat` with `PlFc.dat` as the
model source. The exporter writes TEXCOORD_0, separate material primitives,
and embedded PNG images. Generated UV,
full TEV composition, and writing a playable Melee fighter back into runtime
HSD remain validation gates before game export.

## Base-fighter metadata

Character Setup includes metadata for the 26 Melee base fighters. Each entry
records a stable ID, display name, compatible skeleton mode, and authoring
notes. The roster is a schema/editor aid only: this project does not ship
Nintendo models, textures, animations, sounds, extracted archives, or game
files. Authors must provide original assets and the Studio will validate them.

## Packaging

`pyproject.toml` is dependency-free; `pip install -e .` provides the
`melee-character-studio` command, and the web editor's assets ship as package
data.
