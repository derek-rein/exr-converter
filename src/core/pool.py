"""Multiprocessing worker functions for parallel OCIO + EXR I/O.

Each worker process lazily initializes its own OCIO CPUProcessor on first use
since OCIO.Config objects cannot be pickled across process boundaries.
"""

from __future__ import annotations

import os

import numpy as np
import OpenImageIO as oiio
import PyOpenColorIO as OCIO

# Workers cache up to two CPUProcessors: one for src→working (OCIO load
# stage) and one for working→display (OCIO display stage).  Each is keyed
# on (config_source, config_path, src, dst) so they only rebuild when the
# args change.
_worker_cpus: dict[tuple[str, str, str, str], OCIO.CPUProcessor] = {}


def _ensure_cpu(
    config_source: str, config_path: str, src_space: str, dst_space: str
) -> OCIO.CPUProcessor:
    """Return a cached CPUProcessor, rebuilding only when args change."""
    key = (config_source, config_path, src_space, dst_space)
    cached = _worker_cpus.get(key)
    if cached is not None:
        return cached

    if config_path and os.path.isfile(config_path):
        cfg = OCIO.Config.CreateFromFile(config_path)
    else:
        cfg = OCIO.Config.CreateFromBuiltinConfig(config_source)

    proc = cfg.getProcessor(src_space, dst_space).getDefaultCPUProcessor()
    _worker_cpus[key] = proc
    return proc


def _alpha_over_rgb(bg_rgb: np.ndarray, fg_rgba: np.ndarray) -> np.ndarray:
    """Composite *fg_rgba* over *bg_rgb* (both float32, working space)."""
    a = fg_rgba[..., 3:4]
    fg = fg_rgba[..., :3]
    return fg * a + bg_rgb * (1.0 - a)


def _read_exr_rgb(path: str) -> np.ndarray | None:
    """Read an EXR and return (H, W, 3) float32, or None on failure.

    Crops to the display window, discarding any overscan from the data window.
    """
    try:
        buf = oiio.ImageBuf(path)
        if buf.has_error:
            return None
        spec = buf.spec()
        if spec.full_width > 0 and spec.full_height > 0:
            dx, dy = spec.full_x, spec.full_y
            dw, dh = spec.full_width, spec.full_height
        else:
            dx, dy = 0, 0
            dw, dh = spec.width, spec.height
        ch = min(spec.nchannels, 3)
        roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, ch)
        pixels = np.ascontiguousarray(buf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)
        if pixels.ndim == 3 and pixels.shape[2] >= 3:
            return pixels[:, :, :3]
        if pixels.ndim == 3 and pixels.shape[2] == 1:
            return np.repeat(pixels, 3, axis=2)
        return pixels
    except Exception:
        return None


def process_frame_v2e(
    idx: int,
    rgb: np.ndarray,
    out_path: str,
    compression: str,
    config_source: str,
    config_path: str,
    src_space: str,
    dst_space: str,
) -> int:
    """OCIO transform + write one EXR frame. Returns frame index."""
    from .constants import APP_NAME, APP_VERSION

    cpu = _ensure_cpu(config_source, config_path, src_space, dst_space)
    h, w = rgb.shape[:2]
    buf = np.ascontiguousarray(rgb[:, :, :3], dtype=np.float32)
    desc = OCIO.PackedImageDesc(buf, w, h, 3)
    cpu.apply(desc)

    spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
    spec.attribute("compression", compression)
    spec.attribute("Software", f"{APP_NAME} {APP_VERSION}")
    if dst_space:
        spec.attribute("oiio:ColorSpace", dst_space)
    if src_space:
        spec.attribute("exrconverter:srcColorSpace", src_space)
    if dst_space:
        spec.attribute("exrconverter:dstColorSpace", dst_space)
    out = oiio.ImageBuf(spec)
    out.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, 3), buf)
    out.write(out_path)
    return idx


def process_frame_e2v(
    idx: int,
    path: str,
    config_source: str,
    config_path: str,
    src_space: str,
    working_space: str,
    dst_space: str,
    overlay_working: np.ndarray | None = None,
) -> tuple[int, np.ndarray]:
    """Read one EXR, run the full working-space comp pipeline, return (idx, rgb_u16).

    Pipeline:

    1. read EXR (in *src_space*)
    2. OCIO src→working (scene-linear)
    3. composite *overlay_working* (alpha-over) if provided — ``overlay_working``
       is a float32 RGBA buffer **already linearised into the working space**
    4. OCIO working→display
    5. quantise to uint16

    Corrupt/unreadable frames produce a black frame.
    """
    cpu_to_working = _ensure_cpu(config_source, config_path, src_space, working_space)
    cpu_to_display = _ensure_cpu(config_source, config_path, working_space, dst_space)

    rgb = _read_exr_rgb(path)
    if rgb is None:
        return idx, np.zeros((2, 2, 3), dtype=np.uint16)

    h, w = rgb.shape[:2]
    desc = OCIO.PackedImageDesc(rgb, w, h, 3)
    cpu_to_working.apply(desc)

    if overlay_working is not None and overlay_working.shape[:2] == (h, w):
        rgb = _alpha_over_rgb(rgb, overlay_working)
        rgb = np.ascontiguousarray(rgb, dtype=np.float32)

    desc2 = OCIO.PackedImageDesc(rgb, w, h, 3)
    cpu_to_display.apply(desc2)

    rgb_u16 = np.clip(rgb * 65535.0, 0.0, 65535.0).astype(np.uint16)
    return idx, rgb_u16
