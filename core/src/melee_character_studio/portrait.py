"""Character portraits: the photo shown on a character select screen door, and the grid icon.

The editor keeps the author's photo as ``portrait.png`` in the project (already
cropped to the door's shape in the browser). A build turns it into the door
image as the game reads it: 136x188 RGB5A3 in 4x4 tiles, big-endian. PascalPatch
stages that file for its extra-fighters plugin, which swaps it in for the
door's own portrait while the character is picked; nothing on the disc changes.

The grid icon (``icon.png``, 64x56) is the same idea for the character's button in the
select screen grid; the editor renders it from the model in the game's icon style
(frame, striped backdrop, name bar), and so, by default, the door portrait too.

The stock icon (``stock.png``, 24x24) is the little head shown once per stock above the
player's damage in a match; the editor renders it from the model's head.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .hsd_texture import decode_png_rgba

WIDTH, HEIGHT = 136, 188
PORTRAIT_FILE = "portrait.png"
ICON_WIDTH, ICON_HEIGHT = 64, 56
ICON_FILE = "icon.png"
STOCK_WIDTH, STOCK_HEIGHT = 24, 24
STOCK_FILE = "stock.png"


def fit_cover(image, width=WIDTH, height=HEIGHT):
    """Scale and centre-crop an RGBA image to ``width`` x ``height`` (box-filtered)."""
    sw, sh, src = image["width"], image["height"], image["rgba"]
    scale = max(width / sw, height / sh)
    cw, ch = width / scale, height / scale            # the source rectangle that is kept
    x0, y0 = (sw - cw) / 2, (sh - ch) / 2
    out = bytearray(width * height * 4)
    for y in range(height):
        ya, yb = y0 + y * ch / height, y0 + (y + 1) * ch / height
        rows = range(int(ya), max(int(ya) + 1, min(sh, int(-(-yb // 1)))))
        for x in range(width):
            xa, xb = x0 + x * cw / width, x0 + (x + 1) * cw / width
            cols = range(int(xa), max(int(xa) + 1, min(sw, int(-(-xb // 1)))))
            acc = [0, 0, 0, 0]; n = 0
            for sy in rows:
                base = sy * sw
                for sx in cols:
                    i = (base + sx) * 4
                    for c in range(4): acc[c] += src[i + c]
                    n += 1
            o = (y * width + x) * 4
            out[o:o + 4] = bytes(round(a / n) for a in acc)
    return {"width": width, "height": height, "rgba": bytes(out)}


def encode_rgb5a3(rgba, width, height):
    """GX RGB5A3: opaque pixels as 1RRRRRGGGGGBBBBB, others as 0AAARRRRGGGGBBBB, in 4x4 tiles."""
    if width % 4 or height % 4:
        raise ValueError("RGB5A3 images are made of 4x4 tiles")
    out = bytearray()
    for ty in range(0, height, 4):
        for tx in range(0, width, 4):
            for y in range(ty, ty + 4):
                for x in range(tx, tx + 4):
                    r, g, b, a = rgba[(y * width + x) * 4:(y * width + x) * 4 + 4]
                    if a >= 0xE0:
                        v = 0x8000 | (r >> 3) << 10 | (g >> 3) << 5 | b >> 3
                    else:
                        v = (a >> 5) << 12 | (r >> 4) << 8 | (g >> 4) << 4 | b >> 4
                    out += v.to_bytes(2, "big")
    return bytes(out)


def portrait_image(png_bytes, width=WIDTH, height=HEIGHT):
    """The door image (136x188 RGBA), or another size, for a picture of any size."""
    image = decode_png_rgba(png_bytes)
    if (image["width"], image["height"]) != (width, height):
        image = fit_cover(image, width, height)
    return image


def _build_image(project, out_dir, character_id, key, suffix, width, height):
    project = Path(project)
    character = json.loads((project / "character.json").read_text(encoding="utf-8"))
    name = character.get(key)
    out = Path(out_dir) / f"{character_id}{suffix}"
    if not name or not (project / name).is_file():
        for stale in (out, out.with_name(out.name + ".json")):
            if stale.exists():
                stale.unlink()
        return None
    source = project / name
    image = portrait_image(source.read_bytes(), width, height)
    data = encode_rgb5a3(image["rgba"], width, height)
    out.write_bytes(data)
    report = {"character": character_id, "source": source.name, "width": width, "height": height, "format": "RGB5A3",
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "output_sha256": hashlib.sha256(data).hexdigest()}
    out.with_name(out.name + ".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return out


def build_portrait(project, out_dir, character_id):
    """Write ``<id>.portrait`` (+ its report) from the project's picture; None when it has none."""
    return _build_image(project, out_dir, character_id, "portrait", ".portrait", WIDTH, HEIGHT)


def build_icon(project, out_dir, character_id):
    """Write ``<id>.icon`` (64x56 RGB5A3, + its report) from the project's icon; None when it has none."""
    return _build_image(project, out_dir, character_id, "icon", ".icon", ICON_WIDTH, ICON_HEIGHT)


def build_stock(project, out_dir, character_id):
    """Write ``<id>.stock`` (24x24 RGB5A3, + its report) from the project's stock icon; None when it has none."""
    return _build_image(project, out_dir, character_id, "stock", ".stock", STOCK_WIDTH, STOCK_HEIGHT)
