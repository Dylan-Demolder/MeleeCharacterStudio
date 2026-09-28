from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .hsd_archive import (
    HsdArchiveError,
    build_hsd_archive,
    extract_hsd_tables,
    validate_hsd_archive,
)

GX_QUADS = 0x80
GX_TRIANGLES = 0x90
GX_TRIANGLESTRIP = 0x98
GX_TRIANGLEFAN = 0xA0
GX_LINES = 0xA8
GX_LINESTRIP = 0xB0
GX_POINTS = 0xB8
GX_DIRECT = 1
GX_INDEX8 = 2
GX_INDEX16 = 3
GX_VA_NULL = 0xFF

_COMPONENT_WIDTH = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4}
_PRIMITIVES = {GX_QUADS, GX_TRIANGLES, GX_TRIANGLESTRIP, GX_TRIANGLEFAN, GX_LINES, GX_LINESTRIP, GX_POINTS}


class GxCodegenError(ValueError):
    pass


@dataclass(frozen=True)
class GxDescriptor:
    attr: int
    type: int
    count: int
    comp: int
    frac: int = 0
    stride: int = 0
    vertex: int | None = None


@dataclass(frozen=True)
class GxCommand:
    kind: int
    vertices: tuple[dict[int, object], ...]


def _descriptor(value):
    if isinstance(value, GxDescriptor):
        result = value
    elif isinstance(value, dict):
        result = GxDescriptor(
            int(value["attr"]), int(value["type"]), int(value["count"]),
            int(value["comp"]), int(value.get("frac", 0)),
            int(value.get("stride", 0)), value.get("vertex"),
        )
    else:
        raise GxCodegenError("GX descriptors must be objects")
    if result.type not in (GX_DIRECT, GX_INDEX8, GX_INDEX16):
        raise GxCodegenError("unsupported GX descriptor type")
    if result.comp not in _COMPONENT_WIDTH:
        raise GxCodegenError("unsupported GX component type")
    if not 0 <= result.frac <= 31 or not 0 <= result.count <= 1:
        raise GxCodegenError("invalid GX descriptor fields")
    if result.type != GX_DIRECT and result.vertex is None:
        raise GxCodegenError("indexed GX descriptors require a vertex stream")
    return result


def _arity(descriptor):
    if descriptor.attr <= 8:
        return 1
    if descriptor.attr in (11, 12):
        return 4 if descriptor.count == 1 else 3
    return 2 if descriptor.attr >= 13 else 3


def _pack_component(value, comp, frac):
    value = float(value)
    if comp == 4:
        return struct.pack(">f", value)
    scaled = int(round(value * (1 << frac)))
    limits = {0: (0, 255), 1: (-128, 127), 2: (0, 65535), 3: (-32768, 32767)}
    low, high = limits[comp]
    if not low <= scaled <= high:
        raise GxCodegenError("GX component is outside its encoded range")
    if comp == 0:
        return bytes((scaled,))
    if comp == 1:
        return struct.pack(">b", scaled)
    if comp == 2:
        return struct.pack(">H", scaled)
    return struct.pack(">h", scaled)


def _unpack_component(data, offset, comp, frac):
    if comp == 0:
        value = data[offset]
    elif comp == 1:
        value = struct.unpack_from(">b", data, offset)[0]
    elif comp == 2:
        value = struct.unpack_from(">H", data, offset)[0]
    elif comp == 3:
        value = struct.unpack_from(">h", data, offset)[0]
    else:
        return struct.unpack_from(">f", data, offset)[0]
    return value / float(1 << frac)


def _value(vertex, descriptor):
    try:
        value = vertex[descriptor.attr]
    except (KeyError, TypeError):
        raise GxCodegenError(f"vertex is missing GX attribute {descriptor.attr}")
    if descriptor.attr <= 8:
        return (value,)
    if not hasattr(value, "__iter__") or isinstance(value, (str, bytes)):
        raise GxCodegenError("vector GX attributes require a sequence")
    values = tuple(value)
    if len(values) != _arity(descriptor):
        raise GxCodegenError("GX attribute has the wrong component count")
    return values


def encode_gx_display_list(commands, descriptors, *, block_count=None):
    descriptors = tuple(_descriptor(item) for item in descriptors)
    if not descriptors:
        raise GxCodegenError("at least one GX descriptor is required")
    output = bytearray()
    for command in commands:
        if isinstance(command, GxCommand):
            kind, vertices = command.kind, command.vertices
        else:
            kind, vertices = int(command["kind"]), command["vertices"]
        if kind not in _PRIMITIVES:
            raise GxCodegenError("unsupported GX primitive")
        vertices = tuple(vertices)
        if len(vertices) > 0xFFFF:
            raise GxCodegenError("GX command has too many vertices")
        output.extend(bytes((kind,)) + struct.pack(">H", len(vertices)))
        for vertex in vertices:
            for descriptor in descriptors:
                values = _value(vertex, descriptor)
                if descriptor.type != GX_DIRECT:
                    raise GxCodegenError("indexed encoding requires explicit stream allocation")
                if descriptor.attr <= 8:
                    number = int(values[0])
                    if not 0 <= number <= 255:
                        raise GxCodegenError("GX matrix index is outside its range")
                    output.append(number)
                else:
                    for value in values:
                        output.extend(_pack_component(value, descriptor.comp, descriptor.frac))
    output.append(0)
    if block_count is None:
        block_count = (len(output) + 31) // 32
    if block_count < 1 or len(output) > block_count * 32:
        raise GxCodegenError("GX display list does not fit its block count")
    output.extend(b"\0" * (block_count * 32 - len(output)))
    return bytes(output)


def decode_gx_display_list(data, descriptors, *, block_count=None):
    descriptors = tuple(_descriptor(item) for item in descriptors)
    raw = memoryview(data)
    end = len(raw) if block_count is None else min(len(raw), block_count * 32)
    offset = 0
    result = []
    while offset + 3 <= end:
        kind = raw[offset]
        if kind == 0:
            break
        if kind not in _PRIMITIVES:
            raise GxCodegenError("unsupported GX primitive")
        count = struct.unpack_from(">H", raw, offset + 1)[0]
        offset += 3
        vertices = []
        for _ in range(count):
            vertex = {}
            for descriptor in descriptors:
                if descriptor.type != GX_DIRECT:
                    raise GxCodegenError("indexed decoding requires a vertex stream")
                if descriptor.attr <= 8:
                    if offset >= end:
                        raise GxCodegenError("truncated GX display list")
                    vertex[descriptor.attr] = int(raw[offset])
                    offset += 1
                else:
                    width = _COMPONENT_WIDTH[descriptor.comp]
                    size = width * _arity(descriptor)
                    if offset + size > end:
                        raise GxCodegenError("truncated GX display list")
                    vertex[descriptor.attr] = tuple(
                        _unpack_component(raw, offset + i * width, descriptor.comp, descriptor.frac)
                        for i in range(_arity(descriptor))
                    )
                    offset += size
            vertices.append(vertex)
        result.append(GxCommand(kind, tuple(vertices)))
    return tuple(result)


def _allocate_objects(objects, alignment):
    cursor = 0
    mapping = {}
    for old_offset, payload in sorted(objects.items()):
        cursor = (cursor + alignment - 1) // alignment * alignment
        mapping[int(old_offset)] = cursor
        cursor += len(payload)
    return mapping, cursor


def serialize_hsd_graph(source, objects=None, *, public_symbols=None, external_symbols=None, relocations=None, alignment=4, output=None):
    raw = Path(source).read_bytes() if isinstance(source, (str, Path)) else bytes(source)
    info = validate_hsd_archive(raw)
    old_data = raw[0x20:0x20 + info.data_size]
    old_reloc, old_publics, old_externs = extract_hsd_tables(raw)
    relocation_offsets = tuple(old_reloc) + tuple(int(offset) for offset in (relocations or ()))
    if alignment < 1 or alignment & (alignment - 1):
        raise HsdArchiveError("alignment must be a positive power of two")
    identity = objects is None
    if objects is None:
        objects = {0: old_data}
    objects = {int(offset): bytes(payload) for offset, payload in objects.items()}
    if any(offset < 0 for offset in objects):
        raise HsdArchiveError("object offsets must be non-negative")
    mapping, size = _allocate_objects(objects, alignment)
    block = bytearray(size)
    for old_offset, payload in objects.items():
        block[mapping[old_offset]:mapping[old_offset] + len(payload)] = payload
    def remap_offset(offset):
        if identity and 0 <= offset < len(old_data):
            return offset
        for old_offset, payload in objects.items():
            if old_offset <= offset < old_offset + len(payload):
                return mapping[old_offset] + offset - old_offset
        raise HsdArchiveError(f"offset {offset} was not allocated")
    for old_relocation in relocation_offsets:
        field = remap_offset(old_relocation)
        target = struct.unpack_from(">I", block, field)[0]
        if target:
            struct.pack_into(">I", block, field, remap_offset(target))
    public_map = {int(offset): name for offset, name in old_publics}
    public_map.update({int(offset): str(name) for name, offset in (public_symbols or {}).items()})
    external_map = {int(offset): name for offset, name in old_externs}
    external_map.update({int(offset): str(name) for name, offset in (external_symbols or {}).items()})
    def remap(entries):
        return [(remap_offset(offset), name) for offset, name in entries.items()]
    result = build_hsd_archive(block, relocations=tuple(remap_offset(x) for x in relocation_offsets), publics=remap(public_map), externs=remap(external_map), version=info.version)
    if output is not None:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result)
        return destination
    return result
