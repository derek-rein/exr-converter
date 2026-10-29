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

import json
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
# Every SDR view of the sRGB and Display P3 displays, for the preview's view menu.
LUT_BUNDLE_FILENAME = "views.lut"
LUT_BUNDLE_VERSION = 1
# Input color spaces the preview can detect from EXR metadata.
INPUTS_FILENAME = "inputs.json"
INPUTS_VERSION = 1
INPUT_CURVE_SIZE = 1024
INPUT_CURVE_MIN = -0.125
INPUT_CURVE_MAX = 1.25
# Core Graphics color space the preview tags its 8-bit output with.
CG_SRGB = "sRGB"
CG_DISPLAY_P3 = "displayP3"
_NO_INDEX = 0xFFFF
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
    return _bake_one(config, source, display, view, cube_size, min_exp, max_exp)


def _bake_one(
    config: OCIO.Config,
    source: str,
    display: str,
    view: str,
    cube_size: int,
    min_exp: float,
    max_exp: float,
) -> bytes:
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


def _bundled_config() -> OCIO.Config:
    path = get_bundled_aces_studio_path()
    if path is None:
        raise RuntimeError("bundled ACES Studio OCIO config was not found")
    return OCIO.Config.CreateFromFile(str(path))


def bake_bundled_view_lut() -> bytes:
    """LUT for the ACES Studio config shipped with the app."""
    return bake_view_lut(_bundled_config())


def bake_bundled_view_lut_bundle() -> bytes:
    """``views.lut`` for the ACES Studio config shipped with the app."""
    return bake_view_lut_bundle(_bundled_config())


def bake_bundled_input_table() -> str:
    """``inputs.json`` for the ACES Studio config shipped with the app."""
    return bake_input_table(_bundled_config())


@dataclass(frozen=True)
class ViewBundleEntry:
    """One cube of ``views.lut`` and the CG color space its output is in."""

    cg_space: str
    lut: ViewLUT


@dataclass(frozen=True)
class ViewBundle:
    entries: list[ViewBundleEntry]
    p3_default: int | None
    srgb_default: int | None


def _cg_space_for_display(config: OCIO.Config, display: str) -> str:
    """``displayP3`` or ``sRGB`` for an SDR display Core Graphics can tag, else ``""``."""
    interop = ""
    try:
        cs = config.getColorSpace(display)
        if cs is not None and hasattr(cs, "getInteropID"):
            interop = str(cs.getInteropID() or "").lower()
    except Exception:
        interop = ""
    if interop:
        if interop == "srgb_p3d65_display":
            return CG_DISPLAY_P3
        if interop == "srgb_rec709_display":
            return CG_SRGB
        return ""
    name = display.lower()
    if any(tag in name for tag in ("hdr", "pq", "hlg", "2084", "dci")):
        return ""
    if "p3" in name and "display" in name:
        return CG_DISPLAY_P3
    if "srgb" in name:
        return CG_SRGB
    return ""


def _is_sdr_view(config: OCIO.Config, display: str, view: str) -> bool:
    return "hdr" not in view.lower()


def bake_view_lut_bundle(
    config: OCIO.Config,
    *,
    cube_size: int = LUT_CUBE_SIZE,
    min_exp: float = LUT_MIN_EXP,
    max_exp: float = LUT_MAX_EXP,
    max_entries: int = 16,
) -> bytes:
    """Every SDR view of the config's sRGB and Display P3 displays.

    The preview picks Display P3 on a P3 screen and sRGB otherwise, each at its
    display's default view. A config with neither display gets its default
    display's views, tagged sRGB.
    """
    source = get_working_space(config)
    displays: list[tuple[str, str]] = []
    try:
        names = [str(d) for d in config.getDisplays()]
    except Exception:
        names = []
    for kind in (CG_DISPLAY_P3, CG_SRGB):
        for name in names:
            if _cg_space_for_display(config, name) == kind:
                displays.append((name, kind))
                break
    if not displays:
        display, _view = default_display_view(config, color_space=source)
        if not display:
            display, _view = default_display_view(config)
        if not display:
            raise ValueError("OCIO config has no displays")
        displays.append((display, CG_SRGB))

    blobs: list[tuple[str, bytes]] = []
    p3_default: int | None = None
    srgb_default: int | None = None
    for display, kind in displays:
        try:
            default_view = str(config.getDefaultView(display, source) or "")
        except Exception:
            default_view = ""
        if not default_view:
            default_view = str(config.getDefaultView(display) or "")
        views = [str(v) for v in config.getViews(display)]
        for view in views:
            if len(blobs) >= max_entries:
                break
            if not _is_sdr_view(config, display, view):
                continue
            try:
                blob = _bake_one(config, source, display, view, cube_size, min_exp, max_exp)
            except Exception:
                continue
            if view == default_view:
                if kind == CG_DISPLAY_P3 and p3_default is None:
                    p3_default = len(blobs)
                if kind == CG_SRGB and srgb_default is None:
                    srgb_default = len(blobs)
            blobs.append((kind, blob))
    if not blobs:
        raise ValueError("no OCIO view could be baked")
    if p3_default is None and srgb_default is None:
        srgb_default = 0

    out = bytearray(
        struct.pack(
            "<4sHHHH",
            b"EXQB",
            LUT_BUNDLE_VERSION,
            len(blobs),
            _NO_INDEX if p3_default is None else p3_default,
            _NO_INDEX if srgb_default is None else srgb_default,
        )
    )
    for kind, blob in blobs:
        tag = kind.encode("utf-8")
        out += struct.pack("<H", len(tag)) + tag
        out += struct.pack("<I", len(blob)) + blob
    return bytes(out)


def parse_view_lut_bundle(data: bytes) -> ViewBundle | None:
    """Return the cubes of ``views.lut``, or ``None`` when the bytes are not a bundle."""
    if len(data) < 12 or data[:4] != b"EXQB":
        return None
    _magic, version, count, p3_default, srgb_default = struct.unpack_from("<4sHHHH", data)
    if version != LUT_BUNDLE_VERSION or count == 0:
        return None
    offset = 12
    entries: list[ViewBundleEntry] = []
    for _ in range(count):
        if offset + 2 > len(data):
            return None
        (tag_len,) = struct.unpack_from("<H", data, offset)
        offset += 2
        tag = data[offset : offset + tag_len].decode("utf-8", errors="replace")
        offset += tag_len
        if offset + 4 > len(data):
            return None
        (blob_len,) = struct.unpack_from("<I", data, offset)
        offset += 4
        lut = parse_view_lut(data[offset : offset + blob_len])
        if lut is None:
            return None
        offset += blob_len
        entries.append(ViewBundleEntry(cg_space=tag, lut=lut))

    def index(value: int) -> int | None:
        return value if value != _NO_INDEX and value < count else None

    return ViewBundle(
        entries=entries, p3_default=index(p3_default), srgb_default=index(srgb_default)
    )


def bake_input_table(config: OCIO.Config) -> str:
    """JSON of scene color spaces the preview can turn into ``scene_linear``.

    Each space is fitted as a per-channel curve followed by a 3×3 matrix, which
    is how log and linear camera spaces are built. A space that does not fit
    within tolerance (or a data space) is left out. Linear spaces get no curve.
    """
    target = get_working_space(config)
    spaces: list[dict[str, object]] = []
    try:
        names = [
            str(n)
            for n in config.getColorSpaceNames(
                OCIO.SEARCH_REFERENCE_SPACE_SCENE, OCIO.COLORSPACE_ACTIVE
            )
        ]
    except Exception:
        names = []
    for name in names:
        try:
            cs = config.getColorSpace(name)
            if cs is None or cs.isData():
                continue
            fitted = _fit_input(config, name, target)
        except Exception:
            continue
        if fitted is None:
            continue
        matrix, curve = fitted
        aliases = [str(a) for a in cs.getAliases()]
        interop = ""
        if hasattr(cs, "getInteropID"):
            interop = str(cs.getInteropID() or "")
        entry: dict[str, object] = {
            "name": name,
            "aliases": aliases,
            "interop": interop,
            "matrix": [round(float(v), 7) for v in matrix.reshape(-1)],
        }
        if curve is not None:
            entry["curve"] = [round(float(v), 6) for v in curve]
        spaces.append(entry)
    payload = {
        "version": INPUTS_VERSION,
        "target": target,
        "curve_min": INPUT_CURVE_MIN,
        "curve_max": INPUT_CURVE_MAX,
        "spaces": spaces,
    }
    return json.dumps(payload, separators=(",", ":"))


def _fit_input(
    config: OCIO.Config, source: str, target: str
) -> tuple[np.ndarray, np.ndarray | None] | None:
    processor = config.getProcessor(source, target).getDefaultCPUProcessor()

    def run(rgb: np.ndarray) -> np.ndarray:
        out = np.ascontiguousarray(rgb, dtype=np.float32).copy()
        processor.applyRGB(out)
        return out.astype(np.float64)

    codes = np.linspace(INPUT_CURVE_MIN, INPUT_CURVE_MAX, INPUT_CURVE_SIZE)
    grays = run(np.repeat(codes[:, None], 3, axis=1))
    curve = grays.mean(axis=1)
    if not np.all(np.isfinite(curve)):
        return None

    rng = np.random.default_rng(7)
    probes = rng.uniform(0.0, 1.0, size=(64, 3))
    expected = run(probes)
    shaped = np.interp(probes, codes, curve)
    matrix, *_ = np.linalg.lstsq(shaped, expected, rcond=None)
    matrix = matrix.T
    predicted = shaped @ matrix.T
    scale = np.maximum(np.abs(expected), 0.05)
    if (
        not np.all(np.isfinite(predicted))
        or float(np.max(np.abs(predicted - expected) / scale)) > 0.01
    ):
        return None

    linear = np.allclose(curve, codes * (curve[-1] / codes[-1]), rtol=1e-4, atol=1e-6)
    if linear:
        return matrix * (curve[-1] / codes[-1]), None
    return matrix, curve


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
