from __future__ import annotations

import math
from pathlib import Path

from .hsd_patch import patch_hsd_streams, position_edits_from_hsd_scene
from .hsd_codegen import decode_gx_display_list, encode_gx_display_list, serialize_hsd_graph


class HsdWritebackError(ValueError):
    pass


def _finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise HsdWritebackError("UV coordinates must be finite")
    return value


def generate_uv(vertices, projection="planar", axis="z"):
    points = [tuple(_finite(component) for component in point[:3]) for point in vertices]
    if any(len(point) != 3 for point in points):
        raise HsdWritebackError("vertices must contain three components")
    if not points:
        return []
    lo = tuple(min(point[index] for point in points) for index in range(3))
    hi = tuple(max(point[index] for point in points) for index in range(3))
    span = tuple(hi[index] - lo[index] for index in range(3))
    if projection == "planar":
        axes = {"x": (1, 2), "y": (0, 2), "z": (0, 1)}
        if axis not in axes:
            raise HsdWritebackError("planar axis must be x, y, or z")
        first, second = axes[axis]
        return [((point[first] - lo[first]) / span[first] if span[first] else 0.0,
                 (point[second] - lo[second]) / span[second] if span[second] else 0.0) for point in points]
    if projection == "cylindrical":
        axes = {"x": (1, 2), "y": (0, 2), "z": (0, 1)}
        if axis not in axes:
            raise HsdWritebackError("cylindrical axis must be x, y, or z")
        around, height = axes[axis]
        vertical = {"x": 0, "y": 1, "z": 2}[axis]
        return [((math.atan2(point[height] - (lo[height] + hi[height]) / 2.0,
                              point[around] - (lo[around] + hi[around]) / 2.0) / (2.0 * math.pi) + 0.5),
                 (point[vertical] - lo[vertical]) / span[vertical] if span[vertical] else 0.0)
                for point in points]
    if projection == "spherical":
        center = tuple((lo[index] + hi[index]) / 2.0 for index in range(3))
        result = []
        for point in points:
            x, y, z = (point[index] - center[index] for index in range(3))
            radius = math.sqrt(x * x + y * y + z * z)
            if radius == 0.0:
                result.append((0.5, 0.5))
            else:
                result.append((math.atan2(y, x) / (2.0 * math.pi) + 0.5,
                               0.5 - math.asin(max(-1.0, min(1.0, z / radius))) / math.pi))
        return result
    raise HsdWritebackError(f"unsupported UV projection: {projection}")


def _color(value):
    if isinstance(value, (int, float)):
        value = (value,) * 4
    if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
        raise HsdWritebackError("TEV colors must contain three or four components")
    values = tuple(float(component) for component in value)
    return values if len(values) == 4 else values + (1.0,)


def _source(name, sources, previous):
    if name == "previous":
        return previous
    if name not in sources:
        raise HsdWritebackError(f"missing TEV source: {name}")
    return _color(sources[name])


def compose_tev(stages, sources=None):
    sources = dict(sources or {})
    previous = _color(sources.pop("previous", (0.0, 0.0, 0.0, 1.0)))
    for stage in stages:
        if not isinstance(stage, dict):
            raise HsdWritebackError("TEV stages must be objects")
        operation = stage.get("operation", "replace")
        a = _source(stage.get("a", "texture"), sources, previous)
        if operation == "replace":
            result = a
        elif operation == "add":
            b = _source(stage.get("b", "vertex"), sources, previous)
            result = tuple(a[i] + b[i] for i in range(4))
        elif operation == "subtract":
            b = _source(stage.get("b", "vertex"), sources, previous)
            result = tuple(a[i] - b[i] for i in range(4))
        elif operation == "multiply":
            b = _source(stage.get("b", "vertex"), sources, previous)
            result = tuple(a[i] * b[i] for i in range(4))
        elif operation in ("lerp", "mix"):
            b = _source(stage.get("b", "vertex"), sources, previous)
            c = _source(stage.get("c", "raster"), sources, previous)
            result = tuple(a[i] * (1.0 - c[i]) + b[i] * c[i] for i in range(4))
        else:
            raise HsdWritebackError(f"unsupported TEV operation: {operation}")
        previous = tuple(max(0.0, min(1.0, value)) for value in result)
    return previous


def write_hsd_scene(source, output, scene, *, vertex_edits=None, uv_edits=None, normal_edits=None):
    if not isinstance(scene, dict) or scene.get("hsd") is not True:
        raise HsdWritebackError("scene is not an HSD scene")
    if scene.get("topology_ops"):
        raise HsdWritebackError("topology changes cannot be written without rebuilding GX display lists")
    if scene.get("archive_edits"):
        raise HsdWritebackError("archive/object graph edits require a relocating HSD writer")
    edits = position_edits_from_hsd_scene(scene, vertex_edits or {}, uv_edits or {}, normal_edits or {})
    try:
        return patch_hsd_streams(Path(source), Path(output), edits)
    except (KeyError, TypeError, ValueError) as exc:
        raise HsdWritebackError(str(exc)) from exc
