"""Dependency-free software rasterizer for model-kit previews.

``render_views`` draws a generated mesh from the front, the character's left
side, the back and a three-quarter angle into one PNG so a model can be
checked without a 3D viewer. Textures are sampled nearest-neighbour from the
kit's atlas and lit with a single directional light.
"""
from __future__ import annotations

import math
from pathlib import Path

from .hsd_texture import rgba_png

VIEWS = (("front", 0.0), ("left", 90.0), ("back", 180.0), ("three-quarter", -35.0))
BACKGROUND = (38, 40, 48, 255)


def _rotate_y(p, degrees):
    a = math.radians(degrees); c, s = math.cos(a), math.sin(a)
    return (c * p[0] + s * p[2], p[1], -s * p[0] + c * p[2])


def _sample(atlas, uv, size):
    x = min(size - 1, max(0, int(uv[0] * size))); y = min(size - 1, max(0, int(uv[1] * size)))
    return atlas.pixels[y * size + x]


def render(positions, normals, uvs, triangles, atlas, *, yaw=0.0, width=240, height=320, bounds=None, atlas_size=512):
    """Orthographic render looking down -Z after rotating the model by ``yaw``.

    Returns a flat list of RGBA tuples. Yaw 0 shows the model's front (+Z);
    yaw 90 turns its left side (+X) towards the camera.
    """
    pts = [_rotate_y(p, -yaw) for p in positions]
    nrm = [_rotate_y(n, -yaw) for n in normals]
    if bounds is None:
        lo = [min(p[k] for p in positions) for k in range(3)]; hi = [max(p[k] for p in positions) for k in range(3)]
        radius = max(math.hypot(max(abs(lo[0]), abs(hi[0])), max(abs(lo[2]), abs(hi[2]))), 1e-6)
        bounds = (lo[1], hi[1], radius)
    y0, y1, radius = bounds
    scale = min((width - 16) / (2 * radius), (height - 16) / max(y1 - y0, 1e-6))
    cx = width / 2; base = height - 8 - (height - 16 - (y1 - y0) * scale) / 2
    screen = [(cx + p[0] * scale, base - (p[1] - y0) * scale, p[2]) for p in pts]
    colour = [BACKGROUND] * (width * height); depth = [-1e30] * (width * height)
    light = (0.35, 0.6, 0.72); ln = math.sqrt(sum(c * c for c in light)); light = tuple(c / ln for c in light)
    for a, b, c in triangles:
        (ax, ay, az), (bx, by, bz), (qx, qy, qz) = screen[a], screen[b], screen[c]
        area = (bx - ax) * (qy - ay) - (by - ay) * (qx - ax)
        if area >= 0:  # screen Y points down, so front faces have negative area
            continue
        minx = max(0, int(min(ax, bx, qx))); maxx = min(width - 1, int(max(ax, bx, qx)) + 1)
        miny = max(0, int(min(ay, by, qy))); maxy = min(height - 1, int(max(ay, by, qy)) + 1)
        n = tuple(nrm[a][k] + nrm[b][k] + nrm[c][k] for k in range(3)); nl = math.sqrt(sum(x * x for x in n)) or 1.0
        shade = 0.45 + 0.55 * max(0.0, sum(n[k] * light[k] for k in range(3)) / nl)
        ua, ub, uc = uvs[a], uvs[b], uvs[c]
        inv = 1.0 / area
        for y in range(miny, maxy + 1):
            py = y + 0.5
            for x in range(minx, maxx + 1):
                px = x + 0.5
                w0 = ((bx - px) * (qy - py) - (by - py) * (qx - px)) * inv
                w1 = ((qx - px) * (ay - py) - (qy - py) * (ax - px)) * inv
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                z = w0 * az + w1 * bz + w2 * qz; i = y * width + x
                if z <= depth[i]:
                    continue
                depth[i] = z
                uv = (w0 * ua[0] + w1 * ub[0] + w2 * uc[0], w0 * ua[1] + w1 * ub[1] + w2 * uc[1])
                r, g, bl, _ = _sample(atlas, uv, atlas_size)
                colour[i] = (int(r * shade), int(g * shade), int(bl * shade), 255)
    return colour


def _landmark_marks(image, width, height, landmarks, yaw, bounds):
    y0, y1, radius = bounds
    scale = min((width - 16) / (2 * radius), (height - 16) / max(y1 - y0, 1e-6))
    cx = width / 2; base = height - 8 - (height - 16 - (y1 - y0) * scale) / 2
    for name, p in landmarks.items():
        q = _rotate_y(p, -yaw)
        sx, sy = int(cx + q[0] * scale), int(base - (q[1] - y0) * scale)
        tint = (255, 60, 60, 255) if name.startswith("r_") else (60, 160, 255, 255) if name.startswith("l_") else (255, 230, 60, 255)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if 0 <= sx + dx < width and 0 <= sy + dy < height and (dx == 0 or dy == 0):
                    image[(sy + dy) * width + sx + dx] = tint


def render_views(mb, atlas, path, *, landmarks=None, width=240, height=320):
    """Write a PNG strip of front/left/back/three-quarter views; returns the path.

    Landmarks are overlaid as small crosses (red = right, blue = left) on a
    second row so the rig can be checked against the silhouette.
    """
    lo = [min(p[k] for p in mb.positions) for k in range(3)]; hi = [max(p[k] for p in mb.positions) for k in range(3)]
    radius = max(math.hypot(max(abs(lo[0]), abs(hi[0])), max(abs(lo[2]), abs(hi[2]))), 1e-6)
    bounds = (lo[1], hi[1], radius)
    rows = 2 if landmarks else 1
    total_w = width * len(VIEWS); total_h = height * rows
    canvas = [BACKGROUND] * (total_w * total_h)
    for column, (_, yaw) in enumerate(VIEWS):
        image = render(mb.positions, mb.normals, mb.uvs, mb.triangles, atlas, yaw=yaw, width=width, height=height, bounds=bounds)
        copies = [image]
        if landmarks:
            marked = [(p[0] // 2 + 60, p[1] // 2 + 60, p[2] // 2 + 60, 255) if p != BACKGROUND else p for p in image]
            _landmark_marks(marked, width, height, landmarks, yaw, bounds)
            copies.append(marked)
        for row, img in enumerate(copies):
            for y in range(height):
                start = (row * height + y) * total_w + column * width
                canvas[start:start + width] = img[y * width:(y + 1) * width]
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rgba_png({"width": total_w, "height": total_h, "rgba": bytes(c for p in canvas for c in p)}))
    return path
