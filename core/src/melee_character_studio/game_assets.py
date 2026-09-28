from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re

# Melee's fighter data files use PlXX*.dat names. This table is only a
# filename map; it does not contain game data.
FIGHTER_CODES = {
    "bowser":"Bo", "captain-falcon":"Ca", "donkey-kong":"Dk", "dr-mario":"Dr",
    "falco":"Fc", "fox":"Fx", "ganondorf":"Gn", "mr-game-and-watch":"Gw",
    "ice-climbers":"Nn", "jigglypuff":"Pr", "kirby":"Kb", "link":"Lk",
    "luigi":"Lg", "mario":"Mr", "marth":"Ms", "mewtwo":"Mt", "ness":"Ns",
    "peach":"Pe", "pichu":"Pp", "pikachu":"Pk", "roy":"Fe", "samus":"Ss",
    "sheik":"Sk", "yoshi":"Ys", "young-link":"Cl", "zelda":"Zd",
}
@dataclass(frozen=True)
class FighterAsset:
    fighter_id: str
    path: Path
    kind: str
    size: int

def locate_files_root(root: str|Path) -> Path:
    p=Path(root).expanduser().resolve()
    candidates=(p, p/"files", p/"orig"/"GALE01"/"files")
    for candidate in candidates:
        if candidate.is_dir() and (candidate/"PlCo.dat").exists(): return candidate
    raise FileNotFoundError(f"could not find a Melee files directory below {p}")

def discover_fighter_assets(root: str|Path, fighter_id: str|None=None) -> tuple[FighterAsset,...]:
    files=locate_files_root(root); wanted={fighter_id} if fighter_id else set(FIGHTER_CODES)
    result=[]
    for ident in sorted(wanted):
        code=FIGHTER_CODES.get(ident)
        if not code: continue
        for path in sorted(files.glob(f"Pl{code}*.dat")):
            suffix=path.stem[len("Pl"+code):]
            kind="model-and-skeleton" if suffix in ("", "Nr") else ("animation-and-moves" if suffix=="AJ" else "variant")
            result.append(FighterAsset(ident,path,kind,path.stat().st_size))
    return tuple(result)


def stage_fighter_assets(root: str|Path, output: str|Path, fighter_id: str|None=None) -> tuple[Path, ...]:
    """Copy local source dat files into an explicitly local staging directory.

    This is intentionally not part of project export or package creation.
    """
    import shutil
    destination=Path(output).expanduser().resolve()
    assets=discover_fighter_assets(root,fighter_id)
    if not assets: raise FileNotFoundError("no fighter assets found")
    destination.mkdir(parents=True,exist_ok=True)
    copied=[]
    for asset in assets:
        target=destination/asset.path.name
        shutil.copy2(asset.path,target); copied.append(target)
    return tuple(copied)
