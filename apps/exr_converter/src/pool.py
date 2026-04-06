"""Multiprocessing worker functions for parallel OCIO + EXR I/O.

Each worker process lazily initializes its own OCIO CPUProcessor on first use
since OCIO.Config objects cannot be pickled across process boundaries.
"""

from __future__ import annotations

import os

import numpy as np
import OpenImageIO as oiio
import PyOpenColorIO as OCIO

_worker_cpu: OCIO.CPUProcessor | None = None
_worker_key: tuple[str, str, str, str] = ("", "", "", "")


def _ensure_cpu(
    config_source: str, config_path: str, src_space: str, dst_space: str
) -> OCIO.CPUProcessor:
    """Return a cached CPUProcessor, rebuilding only when args change."""
    global _worker_cpu, _worker_key
    key = (config_source, config_path, src_space, dst_space)
    if _worker_cpu is not None and _worker_key == key:
        return _worker_cpu

    if config_path and os.path.isfile(config_path):
        cfg = OCIO.Config.CreateFromFile(config_path)
    else:
        cfg = OCIO.Config.CreateFromBuiltinConfig(config_source)

    _worker_cpu = cfg.getProcessor(src_space, dst_space).getDefaultCPUProcessor()
    _worker_key = key
    return _worker_cpu


def _read_exr_rgb(path: str) -> np.ndarray | None:
    """Read an EXR and return (H, W, 3) float32, or None on failure."""
    try:
        buf = oiio.ImageBuf(path)
        if buf.has_error:
            return None
        spec = buf.spec()
        ch = min(spec.nchannels, 3)
        roi = oiio.ROI(0, spec.width, 0, spec.height, 0, 1, 0, ch)
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
    dst_space: str,
) -> tuple[int, np.ndarray]:
    """Read one EXR, apply OCIO, quantise to uint16. Returns (idx, rgb_u16).

    Corrupt/unreadable frames produce a black frame.
    """
    cpu = _ensure_cpu(config_source, config_path, src_space, dst_space)

    rgb = _read_exr_rgb(path)
    if rgb is None:
        return idx, np.zeros((2, 2, 3), dtype=np.uint16)

    h, w = rgb.shape[:2]
    desc = OCIO.PackedImageDesc(rgb, w, h, 3)
    cpu.apply(desc)

    rgb_u16 = np.clip(rgb * 65535.0, 0.0, 65535.0).astype(np.uint16)
    return idx, rgb_u16
