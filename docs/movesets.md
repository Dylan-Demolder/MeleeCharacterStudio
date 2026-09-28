# Move sets

Move-set validation requires unique move names and input/state slots. References are checked against the approved move library. Missing transitions and duplicate bindings are blocking diagnostics. Custom scripting is not enabled by this core.


The core editor supports adding moves with a slot, optional approved reference, and transitions. Duplicate slots, missing references, and unknown transition targets remain errors.


The versioned approved-move-library contract requires GALE01-1.02 targeting, provenance, slots, and non-negative startup/active/recovery frame counts. The repository does not ship Nintendo move data; users provide legally sourced library content.

## Runtime conversion target (retarget 2026-09-24)

Converted moveset data ships as PascalPatch profile content: composed files in
the profile ISO plus table/callback hooks in the profile's baked Gecko list,
run on the native-first melee-unlocked runtime (see PascalPatch's
`docs/architecture.md`). Moveset-changing content stays offline-only — it
would desync Slippi netplay against Dolphin players — and packages keep
reporting `game_integration: false` until the PAS-28 gates have native
evidence. The library contract above is unchanged.

## Frame data

The Moves panel reads each action's script the way the engine runs it
(`fighter_moves.FighterData.timeline`, mirroring `ftAction_80073240`) and shows:

- **Startup**: the first frame a damaging hitbox is out.
- **Active**: every frame range with a hitbox out (multi-hit moves list each hit).
- **Can act**: the IASA frame from the script's "allow interrupt" command, or the
  frame the animation ends.
- **Landing lag** for aerials, halved (rounded down) when L-cancelled, and the
  auto-cancel windows: the frames where the script's landing-lag flag is off.
- **On shield**: shield stun minus the frames the attacker is still stuck after
  its first hit (for aerials: minus landing lag, hitting just before landing).
  Shield stun is `floor(0.45 * damage + 2)` frames after hitlag for a hard
  shield: `ftCo_80092F2C` with PlCo's constants. It follows your damage tuning.

Frames are 1-based and assume the normal animation rate. The numbers agree with
known community data for Fox and Marth, and the shield formula was checked in
game: Dr. Mario's jab is -10 and an unL-cancelled nair -40 on shield, matching
PascalPatch's frame-data plugin. Staling, light shield and moves whose
animation rate is changed in code (some specials) are not modelled.

## Kill power and survival

Each move also shows when it KOs: the percent its hardest-hitting hitbox KOs Fox,
Marth, Peach and Bowser standing at the centre of Final Destination, with no DI,
and when it starts to tumble Fox. While you tune a move, the untuned percents
stay beside the new ones. The Stats panel turns this around: a **Survival** table
gives the percent Marth's forward smash, Fox's up smash, Captain Falcon's knee
and Sheik's forward air KO your character at, next to the base fighter and the
whole cast, so weight, gravity and fall speed changes show what they cost.

`knockback.py` (mirrored in `web/knockback.js`) computes these the way the game
does:

- knockback from `ftColl_80079EA8` with PlCo's constants:
  `0.01 * growth * (1.4 * (200 / (weight + 100)) * (0.1 * p + 0.05 * damage * p) + 18) + base`,
  where `p` is the percent after the hit (weight-set knockback uses `p = 10`);
- the 361 angle is 0 degrees below 32 knockback on the ground, 44 above, 45 in
  the air (`ftCo_Damage_CalcAngle`); hitstun is `0.4 * knockback`, and tumble
  starts at 32 frames of it;
- the launch flies as `Fighter_procUpdate` moves a fighter: knockback velocity
  starts at `0.03 * knockback` and loses 0.051 a frame along its direction in the
  air (the target's traction on the ground), gravity pulls the fighter's own
  speed down to its fall speed, and position gains both.

A target is KO'd if it crosses a blast zone while still in hitstun or still
carried by knockback. The simulation reproduces a launch recorded frame by frame
in game (Dr. Mario's forward smash on Luigi at 80%) to within 0.001 units, KO
frame included. DI, SDI, crouch cancelling and staling are not modelled.
