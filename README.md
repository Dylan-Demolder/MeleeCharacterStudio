# Melee Character Studio

Make your own Super Smash Bros. Melee fighters from 3D models, and play them offline in
[Melee Unlocked](https://github.com/hero88go/melee-unlocked) through
[PascalPatch](https://github.com/Dylan-Demolder/PascalPatch).

Bring a `.glb`, `.obj` or zipped `.gltf` and pick the Melee fighter whose skeleton and animations
it builds on. The studio fits the model to that skeleton, then lets you retune moves, borrow
specials from other fighters and change stats, with frame data and KO percents that update as
you go. **Build & play** puts the character in the game.

Six original example characters come with it, ready to play or take apart: Glacier (Bowser),
Sir Nova (Marth), Bolt-9 (Samus), Umbra (Mewtwo), Cinder (Ganondorf) and Chungus
(Jigglypuff). See [`examples/`](examples/README.md).

## Get it

Character Studio comes with PascalPatch. Download PascalPatch from its
[releases](https://github.com/Dylan-Demolder/PascalPatch/releases/latest), unzip it, run
`pascalpatch.cmd`, make a profile with your Melee disc, then press **Character Studio**. You
need nothing else installed.

1. On the start screen, open an example, or press **New project** and drop in a model.
2. To play the examples, open **Roster**, press **+ All examples**, then **Build & play**.

The studio reads the base fighters from your own Melee disc (NTSC 1.02) and writes what it builds
to PascalPatch's folders. This repository contains no Nintendo game data. Custom characters are
offline only: they would desync against other players online, so PascalPatch's online-safe
profiles refuse them.

## The app

It opens in its own window: pywebview if installed, otherwise Edge or Chrome in app mode.

- **Start screen:**
  - recent projects, and the examples. Opening an example opens your own copy in the projects
    folder; the original stays as it came;
  - **New project** from a model: drop in a `.glb`, an `.obj`, or a `.zip` of a `.gltf` with its files. Pick a name, the base fighter whose skeleton and moves it uses, and which way the model faces. The joints are placed automatically as a first guess;
  - **Open**, to browse to any project folder;
  - **Roster**, to put characters together, then Build, or Build & play through PascalPatch (offline);
  - **Settings**: your disc image, where projects go, and PascalPatch.
- **Editor:**
  - the modes: Fit, Body parts, Sculpt, Animate, Moves, Stats, Build;
  - **Test in game** (in Build): builds, then starts the game straight in a match with the character, against the fighter and on the stage you pick, with no menus (PascalPatch's Quick Match plugin). Player 2 can be a Training Lab dummy or a CPU;
  - **File** menu: New, Open, Recent, Save (Ctrl+S), Save as (Ctrl+Shift+S; copies the project to a new folder with its own id), Roster, Close;
  - edits are autosaved to `<project>/.studio/autosave.json` a few seconds after each change. Reopening a project with newer autosaved edits offers to restore them.

A project is a folder: `character.json`, `moveset.json`, `rig.json`, `model/model.gltf` and its textures. New projects go in `Documents/Character Studio` unless you pick another folder in Settings. Settings and the recent list are kept in `~/.melee-character-studio/config.json`. The roster is `roster.json` in the projects folder.

## From source

With Python 3.10 or newer and no other packages:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli app
```

Pass `--no-window` to serve at <http://127.0.0.1:8766> instead. In PascalPatch's Settings, set
the Character Studio folder to this checkout to use it from PascalPatch's button. Tests:

```sh
PYTHONPATH=core/src python -m unittest discover -s core/tests -v
```

## License

Character Studio is free software under the GNU General Public License, version 2 or (at your
option) any later version: see [LICENSE](LICENSE). The example characters in `examples/` are
CC0-1.0. three.js (MIT) is vendored under `core/src/melee_character_studio/web/vendor`.

## Command line

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

## Package-to-fighter HSD composition

For an offline-gameplay `.melee-character` package and a user-owned base fighter archive, create a deterministic, loader-compatible HSD archive without modifying the source archive:

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli compose-fighter-hsd \
  nova.melee-character /path/to/PlFc.dat build/PlNova.dat
```

The command validates package checksums, preserves the base graph, and uses `serialize_hsd_graph` to allocate a package-specific `ftData<Id>` root plus metadata symbol. It does not read or write an ISO. To produce a modified ISO, hand the output to PascalPatch: the native-first path replaces data files inside a PascalPatch profile ISO and leaves the DOL untouched (the generated-DOL route is deferred to PascalPatch's Tier C recompiler fork); user game files stay outside this repository.

## Importing a model as a playable character

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

## Model kit, roster builds and skeleton inspection

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

## 3D editor

Character Studio's editor is a local web app: Python serves the project and
does every file operation, and a three.js viewport in the browser does the
rendering and interaction. No extra Python packages are needed; three.js
(MIT) is vendored under `core/src/melee_character_studio/web/vendor`.

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli studio examples/nova   --iso /path/to/GALE01.iso   --pascalpatch-repo /path/to/PascalPatch --pascalpatch-root /path/to/pascalpatch-data   --port /path/to/melee_port.exe --port-cwd /path/to/melee-unlocked
```

The ISO and PascalPatch paths are remembered in
`~/.melee-character-studio/config.json`; base-fighter files are read from
your disc into `~/.melee-character-studio/cache`, never into the project.

| Mode | What it does |
|---|---|
| Fit | Drag the base skeleton's joints onto your model in its own pose (gizmo, X-ray, snap to limb centre, per-part size) |
| Body parts | Paint which bone each area follows; fixes baggy clothes, hair and capes that the automatic nearest-bone pass gets wrong |
| Sculpt | Grab / smooth / inflate / deflate brushes with X symmetry, stored as bind-space offsets on top of the fit |
| Animate | Play the base fighter's real animations on the rig (baked with a port of the game's FObj interpreter), with per-part weight heat maps |
| Moves | Pick each special (B) from any fighter via dropdowns (run by PascalPatch's move-graft plugin), borrow normal attacks, and retune hitboxes with a before/after table. Every move shows its frame data (startup, active frames, when you can act, landing lag and auto-cancel windows) an on-shield estimate, and the percent it KOs Fox, Marth, Peach and Bowser from the centre of Final Destination, all following your tuning |
| Stats | Grouped attribute sliders (movement, air, body, landing lag) against the base fighter's values, each with a strip showing where it sits among all 26 fighters, and a Survival table: the percent Melee's signature kill moves KO your character at, against the whole cast |
| Build | A Check first: problems that would stop a build, stats or moves beyond anything in Melee (faster than every jab, heavier than Bowser, safer on shield than any smash, KOs earlier than any forward smash, outlives Bowser), and every normal attack ranked against the cast. Then build fighter data + animations + costume, build a PascalPatch offline profile, launch melee-unlocked with Slippi disabled |

Every choice is a dropdown, and in narrow windows the panel stacks under the viewport and the modes become a dropdown. Keys 1–7 switch modes. Undo/redo (Ctrl+Z / Ctrl+Y) covers every edit; Ctrl+S saves `character.json`,
`moveset.json` and `rig.json`. The earlier Tk and Qt desktop editors were
retired; `gui`/`qt-gui` now point here.

## Base-fighter roster

Character Setup includes metadata for the 26 Melee base fighters. Each entry
records a stable ID, display name, compatible skeleton mode, and authoring
notes. The roster is a schema/editor aid only: this project does not ship
Nintendo models, textures, animations, sounds, extracted archives, or game
files. Authors must provide original assets and the Studio will validate them.

## Melee decomp integration

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

## Packaging

`pyproject.toml` is dependency-free; `pip install -e .` provides the
`melee-character-studio` command, and the web editor's assets ship as package
data.
