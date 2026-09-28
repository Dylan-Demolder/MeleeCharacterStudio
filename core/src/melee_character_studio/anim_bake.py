"""Bake HSD figatree animations exactly as the game interprets them.

This is a line-for-line port of the decomp's FObj interpreter
(``src/sysdolphin/baselib/fobj.c`` ``HSD_FObjInterpretAnim``/``FObjLoadData``
and ``spline.c`` ``splGetHelmite``). Every track is decoded once and stepped
frame by frame, so a whole clip bakes in milliseconds instead of re-reading
the archive per frame.
"""
from __future__ import annotations

import struct
from pathlib import Path

from .hsd_animation import _Reader, scan_figatree

OP_CON, OP_LIN, OP_SPL0, OP_SPL, OP_SLP, OP_KEY = 1, 2, 3, 4, 5, 6


def _hermite(fterm, time, p0, p1, d0, d1):
    t2 = time * time; f2 = fterm * fterm
    t2_t = t2 * fterm; t3_t2 = f2 * (t2 * time)
    a = 2.0 * t3_t2 * fterm; b = 3.0 * t2 * f2
    return d1 * (t3_t2 - t2_t) + d0 * (time + ((t3_t2 - t2_t) - t2_t)) + p0 * (1.0 + (a - b)) + p1 * (-a + b)


class _FObj:
    __slots__ = ("ad", "data", "flags", "op", "op_intrp", "obj_type", "frac_value", "frac_slope",
                 "nb_pack", "fterm", "time", "p0", "p1", "d0", "d1", "state", "value")

    def __init__(self, data, start_frame, obj_type, frac_value, frac_slope):
        self.data = data; self.obj_type = obj_type; self.frac_value = frac_value; self.frac_slope = frac_slope
        self.ad = 0; self.time = float(start_frame); self.op = 0; self.op_intrp = 0; self.flags = 0
        self.nb_pack = 0; self.fterm = 0; self.p0 = self.p1 = self.d0 = self.d1 = 0.0; self.state = 1; self.value = None

    def _float(self, frac):
        d = self.data; kind = frac & 0xE0
        if kind == 0x00:
            v = struct.unpack_from("<f", d, self.ad)[0]; self.ad += 4; return v
        denom = float(1 << (frac & 0x1F))
        if kind == 0x60: v = struct.unpack_from("<b", d, self.ad)[0]; self.ad += 1
        elif kind == 0x80: v = d[self.ad]; self.ad += 1
        elif kind == 0x20: v = struct.unpack_from("<h", d, self.ad)[0]; self.ad += 2
        elif kind == 0x40: v = struct.unpack_from("<H", d, self.ad)[0]; self.ad += 2
        else: return 0.0
        return v / denom

    def _pack_info(self):
        d = self.data[self.ad]; self.ad += 1
        n = ((d >> 4) & 7) + 1; shift = 3
        if d & 0x80:
            while True:
                d = self.data[self.ad]; self.ad += 1
                n += (d & 0x7F) << shift; shift += 7
                if not d & 0x80: break
        return n

    def _wait(self):
        wait = 0; shift = 0
        while True:
            d = self.data[self.ad]; self.ad += 1
            wait |= (d & 0x7F) << shift; shift += 7
            if not d & 0x80: return wait

    def _launch_key(self):
        if self.flags & 0x40:
            self.op_intrp = self.op; self.flags &= ~0x40; self.flags |= 0x80; self.p0 = self.p1

    def _load_data(self):
        if self.ad >= len(self.data):
            return 6
        self.op_intrp = self.op
        if self.nb_pack == 0:
            self.op = self.data[self.ad] & 0xF
            self.nb_pack = self._pack_info()
        self.nb_pack -= 1
        st = self.state; nxt = 3 if st == 1 else 4
        op = self.op
        if op in (OP_CON, OP_LIN):
            self.p0 = self.p1; self.p1 = self._float(self.frac_value)
            if self.op_intrp != OP_SLP: self.d0 = self.d1; self.d1 = 0.0
        elif op == OP_SPL0:
            self.p0 = self.p1; self.d0 = self.d1; self.p1 = self._float(self.frac_value); self.d1 = 0.0
        elif op == OP_SPL:
            self.p0 = self.p1; self.p1 = self._float(self.frac_value); self.d0 = self.d1; self.d1 = self._float(self.frac_slope)
        elif op == OP_SLP:
            self.d0 = self.d1; self.d1 = self._float(self.frac_slope); return st
        elif op == OP_KEY:
            self._launch_key(); self.p1 = self._float(self.frac_value); self.flags |= 0x40
        else:
            return 0
        return nxt

    def _update(self):
        op = self.op_intrp
        if op == OP_KEY:
            if self.flags & 0x80: self.value = self.p0; self.flags &= ~0x80
        elif op == OP_CON:
            self.value = self.p1 if self.time >= self.fterm else self.p0
        elif op == OP_LIN:
            if self.flags & 0x20:
                self.flags &= ~0x20
                if self.fterm: self.d0 = (self.p1 - self.p0) / self.fterm
                else: self.d0 = 0.0; self.p0 = self.p1
            self.value = self.d0 * self.time + self.p0
        elif op in (OP_SPL0, OP_SPL, OP_SLP):
            self.value = _hermite(1.0 / self.fterm, self.time, self.p0, self.p1, self.d0, self.d1) if self.fterm else self.p1

    def step(self, rate):
        """``HSD_FObjInterpretAnim``; returns the current value (None until first update)."""
        state = self.state; fterm_acc = 0.0
        if state == 0:
            return self.value
        self.time += rate
        if self.time < 0.0:
            return self.value
        for _ in range(100000):
            if state == 6:
                self.time += fterm_acc; self._launch_key(); self._update(); self.state = 6; return self.value
            if state in (1, 2):
                self.state = state; state = self._load_data(); self.state = state
            elif state == 3:
                if self.flags & 0x80: self._update()
                if self.ad >= len(self.data): state = 6
                else:
                    self.fterm = self._wait(); self.flags |= 0x20; state = 2
                self.state = state
            elif state == 4:
                if self.fterm <= self.time:
                    fterm_acc = self.fterm; self.time -= self.fterm; state = 3; self.state = 3; continue
                self._update(); self.state = 5; return self.value
            elif state == 5:
                state = 4; self.state = 4
            else:
                return self.value
        raise RuntimeError("FObj interpreter did not converge")


def bake_clip(raw, clip, frame_count=None):
    """Return ``[frame][node] -> {obj_type: value}`` for every integer frame of ``clip``."""
    reader = _Reader(raw, clip.archive_offset)
    tracks = []; index = 0
    for node, count in enumerate(clip.nodes):
        for _ in range(count):
            t = clip.tracks[index]; index += 1
            start = t.start_frame - 0x10000 if t.start_frame >= 0x8000 else t.start_frame
            data = bytes(reader.data[t.ad_offset:t.ad_offset + t.length])
            tracks.append((node, _FObj(data, start, t.obj_type, t.frac_value, t.frac_slope)))
    frames = int(frame_count if frame_count is not None else max(1, round(clip.frames)))
    out = []; current = [dict() for _ in clip.nodes]
    for f in range(frames):
        for node, fobj in tracks:
            value = fobj.step(0.0 if f == 0 else 1.0)
            if value is not None:
                current[node][fobj.obj_type] = value
        out.append([dict(c) for c in current])
    return out


class AnimationLibrary:
    """Figatree clips of one ``PlXxAJ.dat`` with cached bakes."""

    def __init__(self, path):
        self.path = Path(path); self.raw = self.path.read_bytes(); self.clips = scan_figatree(self.path); self._cache = {}

    def names(self):
        return [c.name for c in self.clips]

    def bake(self, name):
        if name not in self._cache:
            clip = next(c for c in self.clips if c.name == name)
            self._cache[name] = bake_clip(self.raw, clip)
        return self._cache[name]
