# Example characters

Six original fighters made with Character Studio, each built on a vanilla
fighter's skeleton, animations and moves. In the game they are new fighters on
the character select screen; no original fighter is replaced. They come with the studio: its start screen lists them under
**Examples**, and the Roster page can add them all at once, ready for **Build &
play**. Every one has an original low-poly model built by the model kit
(`model.json` → `model/model.gltf`), a move set that retunes the base fighter's
hitboxes, and stat changes. Nothing in this folder comes from the game disc; the
base fighters' files are read from your own ISO at build time and written only to
your output folder. The project files are CC0-1.0: copy them, change them, share
what you make.

| Character | Built on (file) | Idea | Highlights |
|---|---|---|---|
| Glacier | Bowser (`PlKp`) | Ice golem | Ice element on specials and smashes; heavier than Bowser |
| Sir Nova | Marth (`PlMs`) | Star knight | Electric starlight blade; floatier jumps |
| Bolt-9 | Samus (`PlSs`) | Storm robot | Electric up special, smashes and kicks; heavier, less floaty; cannon on the right arm |
| Umbra | Mewtwo (`PlMt`) | Shadow ninja | Dark element strikes; faster on the ground; teleport recovery kept |
| Cinder | Ganondorf (`PlGn`) | Fire-demon warlord | Fire on every special, forward smash and forward air |
| Chungus | Jigglypuff (`PlPr`) | Round purple bat | Jigglypuff's air game; Fox's Firefox and Illusion and Luigi's Cyclone as specials (move-graft); wings flap with the arm animations |

To make your own from one of them, open it and use **File → Save as**, or copy
the folder, pick an unused base fighter in `rig.json`/`moveset.json` (`base_fighter`), edit
`model.json`, and list it in `roster.json`. A roster has one character per base fighter.

Each project's `character.json` has a `description` and `features` list; the
full move list is in `moveset.json`. `model/preview.png` shows front, left,
back and three-quarter views, with the rig landmarks marked in the second row.

`ember/` is older and smaller: a `.melee-character` package example (stats and
moves only, no model) for the command-line tools.

## Project layout

```text
<name>/
  model.json      model-kit spec: palette, landmarks, primitives, pixel decals
  model/          generated: model.gltf + .bin + .png atlas, preview.png
  character.json  identity, description, features, attributes / attribute_scales
  moveset.json    moves: base action (or glob), damage/size/knockback/element edits, borrows
  rig.json        base fighter, model path, landmarks, per-segment vertex ranges
```

`attribute_scales` multiplies the base fighter's own values (`weight: 1.05`
= 5 % heavier than Bowser), so the characters stay relative to whatever
the disc says. `attributes` sets absolute values; a key can't be in both.

## Building

Run all commands from the `MeleeCharacterStudio` checkout with
`PYTHONPATH=core/src` (or install it with `pip install -e .` and use
`melee-character-studio`).

```sh
# 1. (Optional) regenerate models after editing model.json
python -m melee_character_studio.cli generate-model examples/glacier

# 2. Check how a slot's skeleton is mapped (reads your ISO, prints parts/segments)
python -m melee_character_studio.cli inspect-skeleton jigglypuff --iso /path/to/GALE01.iso

# 3. Build every character plus a PascalPatch profile
python -m melee_character_studio.cli build-roster examples/roster.json \
  --iso /path/to/GALE01.iso --out ~/melee-roster-build \
  --profile /path/to/pascalpatch-data/profiles/examples.json

#    ...or just some of them
python -m melee_character_studio.cli build-roster examples/roster.json \
  --iso /path/to/GALE01.iso --out ~/melee-roster-build --only glacier --only cinder

#    ...or one project on its own
python -m melee_character_studio.cli build-character examples/glacier \
  --iso /path/to/GALE01.iso --out ~/melee-roster-build/glacier
```

Each character gets `<out>/<name>/`, which holds:

- `<id>.melee-character`
- `PlXx.dat`, the stats and hitbox edits
- `PlXxNr.dat`, the imported model as the default costume
- `PlXxAJ.dat`, only when moves are borrowed (Chungus)

Each `.dat` has its report next to it. `<out>/roster-report.json` lists the steps, warnings and failures for every character. If one character fails, the rest still build.

Then in PascalPatch:

```sh
pascalpatch --root /path/to/pascalpatch-data profile validate examples
pascalpatch --root /path/to/pascalpatch-data build examples
pascalpatch --root /path/to/pascalpatch-data launch examples --runtime native --allow-unsafe \
  --port /path/to/melee_port.exe --port-cwd /path/to/melee-unlocked
```

The profile is `mode: offline`, so launching needs `--allow-unsafe`. Custom characters won't work on Slippi or netplay, and netplay-safe profiles refuse them.

## Testing checklist

All of them have been built from a GALE01 v1.02 disc and played in offline VS
matches on melee-unlocked (`--no-slippi`). That pass found and fixed:

- **Part numbering.** The decomp's `Fighter_Part` enum is one entry short
  after `RFootJ`, so every derived arm was shifted by a joint. Fox failed
  outright with "no pelvis". `base_skeleton.PART_NAMES` now matches the disc,
  which was checked against bind positions for all 26 kinds.
- **Ganondorf's head.** His parts table leaves NeckN/HeadN unmapped, so the
  head is now found from the bind pose.
- **Jigglypuff and Samus.** Jigglypuff's legs start at the centre of the body,
  so the torso side axis is taken from the knees. Parts a skeleton lacks
  (Jigglypuff's hands, Samus's right hand) fold into their parent for
  `segment_modes`, painted overrides and ranges.
- **Texture animations.** Eyes and mouths address TObjs by their index across
  all DObjs. DK's and Mewtwo's host DObj had two TObjs and the costume wrote
  one, which crashed the game with `can't find fighter texture anim!`. The host
  now keeps its TObj count; the extra TObjs point at a black image.
- **Air specials.** Moves whose script is only a `goto` into the ground script
  count as tuned by the ground entry, so they don't warn.

The warnings still left in `roster-report.json` are specials whose hitboxes
come from fighter code or items: Fox's Illusion, Bowser's Fire Breath and
Mewtwo's Confusion.

To re-check after changes:

1. Run `inspect-skeleton <slot> --iso …` for each slot and check that the parts include pelvis, chest, head, both arms and both legs. Jigglypuff is the one to watch, because Chungus needs both thighs and a chest.
2. Run `build-roster` and read the warnings in `roster-report.json`:
   - `move … skipped` means an action name or glob didn't match that fighter. Fix the `action` in `moveset.json`. `inspect-skeleton` and the studio's Moves tab list the real names.
   - Costume notes flag vertices with no nearby bone.
3. Open a project in the studio (`… cli studio examples/<name> --iso …`) and play animations in Animate mode:
   - The sword (Nova) and the cannon (Bolt-9) should follow the hand. If one points the wrong way, fix it with a joint rotation in Fit mode.
4. In game:
   - Pick each character and check that its model, not the vanilla costume, appears.
   - In training mode, check the damage numbers against the edits in `moveset.json`.
   - Check that elemental hits freeze (Glacier), burn (Cinder) and shock (Nova, Bolt-9).
   - Check that Chungus's borrowed specials play.

## Limits

- **Default costume only.** The other colour slots keep the vanilla look.
- **Vanilla gear is hidden.** All of the base fighter's own meshes are hidden, so the vanilla sword, cannon and so on disappear and the modelled versions replace them.
- **Projectiles can't be tuned.** Arrows, missiles, blasters and TNT are items, so those moves are described but keep vanilla behaviour.
- **No new sounds or effects.** Characters are new fighters, but they use their base fighter's sounds and effects.
- **Only offline use is supported.**
