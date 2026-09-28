from __future__ import annotations
from dataclasses import dataclass

COMMON_ANIMATION_SLOTS = (
    "idle", "walk", "dash", "run", "jump_squat", "jump", "fall", "landing",
    "damage", "tech", "ledge_getup", "shield", "grab", "throw_forward",
    "throw_back", "attack_neutral", "attack_forward", "attack_up", "attack_down",
    "attack_air_neutral", "attack_air_forward", "attack_air_back", "attack_air_up",
    "attack_air_down", "special_neutral", "special_side", "special_up", "special_down",
    "win", "lose", "taunt",
)

@dataclass(frozen=True)
class BaseFighter:
    id: str
    name: str
    skeleton: str
    mode: str
    notes: str
    animation_slots: tuple[str, ...] = COMMON_ANIMATION_SLOTS

# Metadata only. This list contains no Nintendo models, textures, animations,
# audio, extracted game files, or copyrighted game data.
BASE_FIGHTERS = (
    ("bowser","Bowser","bowser","clone","Original author assets required."),
    ("captain-falcon","Captain Falcon","captain-falcon","clone","Original author assets required."),
    ("donkey-kong","Donkey Kong","donkey-kong","clone","Original author assets required."),
    ("dr-mario","Dr. Mario","mario","clone","Uses the Mario-compatible skeleton mode."),
    ("falco","Falco","fox","clone","Uses the Fox-compatible skeleton mode."),
    ("fox","Fox","fox","clone","Original author assets required."),
    ("ganondorf","Ganondorf","captain-falcon","clone","Uses the Captain Falcon-compatible skeleton mode."),
    (" mr-game-and-watch".strip(),"Mr. Game & Watch","game-and-watch","clone","Flat character pipeline needs dedicated validation."),
    ("ice-climbers","Ice Climbers","ice-climbers","clone","Two-part character requires paired rig support."),
    ("jigglypuff","Jigglypuff","jigglypuff","clone","Original author assets required."),
    ("kirby","Kirby","kirby","clone","Copy ability metadata is not yet supported."),
    ("link","Link","link","clone","Original author assets required."),
    ("luigi","Luigi","mario","clone","Uses the Mario-compatible skeleton mode."),
    ("mario","Mario","mario","clone","Reference base fighter for the demo project."),
    ("marth","Marth","marth","clone","Original author assets required."),
    ("mewtwo","Mewtwo","mewtwo","clone","Original author assets required."),
    ("ness","Ness","ness","clone","Original author assets required."),
    ("peach","Peach","peach","clone","Original author assets required."),
    ("pichu","Pichu","pikachu","clone","Uses the Pikachu-compatible skeleton mode."),
    ("pikachu","Pikachu","pikachu","clone","Original author assets required."),
    ("roy","Roy","marth","clone","Uses the Marth-compatible skeleton mode."),
    ("samus","Samus","samus","clone","Original author assets required."),
    ("sheik","Sheik","zelda","clone","Uses the Zelda-compatible skeleton mode."),
    ("yoshi","Yoshi","yoshi","clone","Original author assets required."),
    ("young-link","Young Link","link","clone","Uses the Link-compatible skeleton mode."),
    ("zelda","Zelda","zelda","clone","Original author assets required."),
)

ROSTER=tuple(BaseFighter(*row) for row in BASE_FIGHTERS)
ROSTER_BY_ID={x.id:x for x in ROSTER}

def list_base_fighters(): return ROSTER

def find_base_fighter(identifier):
    try: return ROSTER_BY_ID[identifier]
    except KeyError: raise ValueError(f"unknown base fighter: {identifier}")
