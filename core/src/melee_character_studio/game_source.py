"""Read base-fighter files from the user's own GALE01 disc image.

Files are copied into a per-user cache outside any repository; nothing from
the disc is packaged with Character Studio.
"""
from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

FIGHTER_CODES = {
    "bowser": "Kp", "captain-falcon": "Ca", "donkey-kong": "Dk", "dr-mario": "Dr",
    "falco": "Fc", "fox": "Fx", "ganondorf": "Gn", "mr-game-and-watch": "Gw",
    "ice-climbers": "Pp", "jigglypuff": "Pr", "kirby": "Kb", "link": "Lk",
    "luigi": "Lg", "mario": "Mr", "marth": "Ms", "mewtwo": "Mt", "ness": "Ns",
    "peach": "Pe", "pichu": "Pc", "pikachu": "Pk", "roy": "Fe", "samus": "Ss",
    "sheik": "Sk", "yoshi": "Ys", "young-link": "Cl", "zelda": "Zd",
}
_EXPECTED_GAME_ID = b"GALE01"


def cache_root():
    return Path(os.environ.get("MELEE_STUDIO_HOME", Path.home() / ".melee-character-studio")) / "cache"


def _fst(iso):
    with open(iso, "rb") as f:
        head = f.read(0x440)
        if head[:6] != _EXPECTED_GAME_ID:
            raise ValueError("not a GALE01 (Melee NTSC) disc image")
        fst_off, fst_size = struct.unpack_from(">II", head, 0x424)
        f.seek(fst_off); fst = f.read(fst_size)
    count = struct.unpack_from(">I", fst, 8)[0]; strings = count * 12
    files = {}
    for i in range(1, count):
        flags_name, off, size = struct.unpack_from(">III", fst, i * 12)
        if flags_name >> 24:
            continue  # directory
        name_off = flags_name & 0xFFFFFF; end = fst.index(b"\0", strings + name_off)
        files[fst[strings + name_off:end].decode("ascii")] = (off, size)
    return files


def extract_base_files(iso, base_fighter):
    """Return {'data', 'costume', 'animations', 'common'} paths for ``base_fighter``.

    ``common`` is ``PlCo.dat``, whose parts table maps body parts to each
    fighter's joints (used to derive skeleton maps for any base fighter).
    """
    code = FIGHTER_CODES[base_fighter]
    iso = Path(iso).expanduser().resolve(); st = iso.stat()
    key = hashlib.sha1(f"{iso}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    out = cache_root() / key; out.mkdir(parents=True, exist_ok=True)
    wanted = {"data": f"Pl{code}.dat", "costume": f"Pl{code}Nr.dat", "animations": f"Pl{code}AJ.dat", "common": "PlCo.dat"}
    result = {}; table = None
    for role, name in wanted.items():
        target = out / name
        if not target.is_file():
            table = table or _fst(iso)
            if name not in table:
                raise FileNotFoundError(f"{name} not on disc")
            off, size = table[name]
            with open(iso, "rb") as f:
                f.seek(off); data = f.read(size)
            tmp = target.with_suffix(".tmp"); tmp.write_bytes(data); tmp.replace(target)
        result[role] = target
    return result
