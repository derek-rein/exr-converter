"""Bake the app's default OCIO display view for the macOS Quick Look extension.

The player shows scene-linear plates with a ``DisplayViewTransform`` from the
config's ``scene_linear`` role through ``default_display_view``, at exposure 0
and gamma 1. On the bundled ACES Studio config that is ACEScg →
``sRGB - Display`` / ``ACES 2.0 - SDR 100 nits (Rec.709)``, which matches the
player path ACEScg → ACES2065-1 → the same display view.

The extension cannot link OpenColorIO. This module samples that processor
through a log2 shaper into a 3D LUT (``CIColorCube`` order: red fastest, then
green, then blue, RGBA float). The appex applies the same shaper, then the cube.

The cube is clipped to 0–1 because Quick Look draws an 8-bit preview. Negatives
fall on the first lattice entry (scene value ``2^min_exp``). Exposure and gamma
sliders are not baked; the preview matches the viewer's default.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import PyOpenColorIO as OCIO

from .ocio_utils import (
    default_display_view,
    get_bundled_aces_studio_path,
    get_working_space,
    make_display_processor,
)

LUT_FILENAME = "view.lut"
LUT_VERSION = 1
# Analytic log2 shaper. 2^-10 … 2^6 covers about 0.001 to 64.
LUT_MIN_EXP = -10.0
LUT_MAX_EXP = 6.0
LUT_CUBE_SIZE = 33
# Bit 0: lattice is the analytic log2 shaper, not a separate 1D table.
LUT_FLAG_LOG2_SHAPER = 1

# magic, version, cube, flags, reserved, min, max, src_len, display_len, view_len, reserved
_HEADER = struct.Struct("<4sHHHHffHHHH")
assert _HEADER.size == 28


@dataclass(frozen=True)
class ViewLUT:
    """Parsed ``view.lut`` cube. ``samples`` is RGBA float, red fastest."""

    cube_size: int
    min_exp: float
    max_exp: float
    source: str
    display: str
    view: str
    samples: np.ndarray


def shaper(value: float, *, min_exp: float = LUT_MIN_EXP, max_exp: float = LUT_MAX_EXP) -> float:
    """Map a scene-linear channel into the cube's 0–1 index domain."""
    if not value > 0.0:
        return 0.0
    span = max_exp - min_exp
    if span <= 0.0:
        return 0.0
    floor = float(np.exp2(min_exp))
    encoded = (float(np.log2(max(value, floor))) - min_exp) / span
    if encoded < 0.0:
        return 0.0
    if encoded > 1.0:
        return 1.0
    return encoded


def bake_view_lut(
    config: OCIO.Config,
    *,
    cube_size: int = LUT_CUBE_SIZE,
    min_exp: float = LUT_MIN_EXP,
    max_exp: float = LUT_MAX_EXP,
) -> bytes:
    """Sample ``scene_linear`` → the config's default display view."""
    if cube_size < 2 or cube_size > 65:
        raise ValueError(f"cube size {cube_size} is outside 2…65")
    if not max_exp > min_exp:
        raise ValueError("LUT shaper max_exp must be greater than min_exp")
    source = get_working_space(config)
    display, view = default_display_view(config, color_space=source)
    if not display or not view:
        display, view = default_display_view(config)
    if not display or not view:
        raise ValueError("OCIO config has no default display/view")
    processor = make_display_processor(config, source, display, view, exposure=0.0, gamma=1.0)
    lattice = _lattice(cube_size, min_exp, max_exp)
    blue, green, red = np.meshgrid(lattice, lattice, lattice, indexing="ij")
    rgb = np.ascontiguousarray(np.stack([red, green, blue], axis=-1).reshape(-1, 3))
    processor.applyRGB(rgb)
    np.nan_to_num(rgb, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
    np.clip(rgb, 0.0, 1.0, out=rgb)
    alpha = np.ones((rgb.shape[0], 1), dtype=np.float32)
    rgba = np.concatenate([rgb, alpha], axis=1).astype(np.float32, copy=False)
    return _encode(rgba, cube_size, min_exp, max_exp, source, display, view)


def bake_bundled_view_lut() -> bytes:
    """LUT for the ACES Studio config shipped with the app."""
    path = get_bundled_aces_studio_path()
    if path is None:
        raise RuntimeError("bundled ACES Studio OCIO config was not found")
    config = OCIO.Config.CreateFromFile(str(path))
    return bake_view_lut(config)


def parse_view_lut(data: bytes) -> ViewLUT | None:
    """Return the cube, or ``None`` when the bytes are not a version-1 LUT."""
    if len(data) < _HEADER.size or data[:4] != b"EXQL":
        return None
    (
        _magic,
        version,
        cube_size,
        flags,
        _reserved,
        min_exp,
        max_exp,
        src_len,
        display_len,
        view_len,
        _reserved_names,
    ) = _HEADER.unpack_from(data)
    if version != LUT_VERSION or not (2 <= cube_size <= 65):
        return None
    if flags & LUT_FLAG_LOG2_SHAPER == 0 or not max_exp > min_exp:
        return None
    name_end = _HEADER.size + src_len + display_len + view_len
    count = cube_size * cube_size * cube_size * 4
    cube_end = name_end + count * 4
    if len(data) < cube_end:
        return None
    try:
        source = data[_HEADER.size : _HEADER.size + src_len].decode("utf-8")
        display_start = _HEADER.size + src_len
        display = data[display_start : display_start + display_len].decode("utf-8")
        view = data[display_start + display_len : name_end].decode("utf-8")
    except UnicodeDecodeError:
        return None
    samples = np.frombuffer(data, dtype="<f4", count=count, offset=name_end).reshape(-1, 4)
    if samples.shape != (cube_size**3, 4):
        return None
    return ViewLUT(
        cube_size=cube_size,
        min_exp=float(min_exp),
        max_exp=float(max_exp),
        source=source,
        display=display,
        view=view,
        samples=np.array(samples, dtype=np.float32, copy=True),
    )


def sample_view_lut(lut: ViewLUT, rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Trilinear sample. The appex's CPU fallback uses the same lattice."""
    encoded = (
        shaper(rgb[0], min_exp=lut.min_exp, max_exp=lut.max_exp),
        shaper(rgb[1], min_exp=lut.min_exp, max_exp=lut.max_exp),
        shaper(rgb[2], min_exp=lut.min_exp, max_exp=lut.max_exp),
    )
    return _trilinear(lut, encoded)


def write_view_lut(data: bytes, destinations: list[Path]) -> list[Path]:
    """Atomic replace. A destination that cannot be written is skipped."""
    written: list[Path] = []
    for path in destinations:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError:
            continue
        written.append(path)
    return written


def _lattice(cube_size: int, min_exp: float, max_exp: float) -> np.ndarray:
    step = np.arange(cube_size, dtype=np.float32) / np.float32(cube_size - 1)
    return np.exp2(np.float32(min_exp) + step * np.float32(max_exp - min_exp))


def _encode(
    rgba: np.ndarray,
    cube_size: int,
    min_exp: float,
    max_exp: float,
    source: str,
    display: str,
    view: str,
) -> bytes:
    names = (source.encode("utf-8"), display.encode("utf-8"), view.encode("utf-8"))
    if any(len(name) > 65535 for name in names):
        raise ValueError("OCIO color space name is too long for the LUT header")
    header = _HEADER.pack(
        b"EXQL",
        LUT_VERSION,
        cube_size,
        LUT_FLAG_LOG2_SHAPER,
        0,
        float(min_exp),
        float(max_exp),
        len(names[0]),
        len(names[1]),
        len(names[2]),
        0,
    )
    cube = np.ascontiguousarray(rgba, dtype="<f4").tobytes()
    return header + names[0] + names[1] + names[2] + cube


def _trilinear(lut: ViewLUT, encoded: tuple[float, float, float]) -> tuple[float, float, float]:
    size = lut.cube_size
    last = size - 1
    corners = []
    weights = []
    for channel in encoded:
        scaled = min(max(channel, 0.0), 1.0) * last
        low = min(int(scaled), last)
        high = min(low + 1, last)
        frac = scaled - low
        corners.append((low, high))
        weights.append(frac)
    (r0, r1), (g0, g1), (b0, b1) = corners
    fr, fg, fb = weights

    def at(red: int, green: int, blue: int) -> np.ndarray:
        index = (blue * size + green) * size + red
        return lut.samples[index, :3]

    c00 = at(r0, g0, b0) * (1.0 - fr) + at(r1, g0, b0) * fr
    c01 = at(r0, g0, b1) * (1.0 - fr) + at(r1, g0, b1) * fr
    c10 = at(r0, g1, b0) * (1.0 - fr) + at(r1, g1, b0) * fr
    c11 = at(r0, g1, b1) * (1.0 - fr) + at(r1, g1, b1) * fr
    c0 = c00 * (1.0 - fg) + c10 * fg
    c1 = c01 * (1.0 - fg) + c11 * fg
    out = c0 * (1.0 - fb) + c1 * fb
    return (float(out[0]), float(out[1]), float(out[2]))
