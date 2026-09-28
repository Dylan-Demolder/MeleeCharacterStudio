# Ember (example character)

An original offline character authored with Character Studio. It carries no
Nintendo data: only metadata and attribute values. Ember occupies Fox's
fighter slot through native Tier B delivery (the vanilla DOL is untouched), so
it replaces Fox in an offline PascalPatch profile.

Ember is a heavy, floaty bruiser: 1.55x model scale, weight 118, gravity 0.09
and a 2.3 run speed.

```sh
PYTHONPATH=core/src python -m melee_character_studio.cli validate-project examples/ember
PYTHONPATH=core/src python -m melee_character_studio.cli export examples/ember build/ember.melee-character
PYTHONPATH=core/src python -m melee_character_studio.cli compose-fighter-slot build/ember.melee-character /path/to/user/PlFx.dat build/PlFx.dat
```

`PlFx.dat` comes from your own GALE01 Rev.02 disc; do not commit it or the
composed output.
