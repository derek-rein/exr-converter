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
    cpu = _ensure_cpu(config_source, config_path, src_space, dst_space)
    h, w = rgb.shape[:2]
    buf = np.ascontiguousarray(rgb, dtype=np.float32)
    desc = OCIO.PackedImageDesc(buf, w, h, 3)
    cpu.apply(desc)

    spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
    spec.attribute("compression", compression)
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
    """Read one EXR, apply OCIO, quantise to uint16. Returns (idx, rgb_u16)."""
    cpu = _ensure_cpu(config_source, config_path, src_space, dst_space)

    ibuf = oiio.ImageBuf(path)
    spec = ibuf.spec()
    roi = oiio.ROI(0, spec.width, 0, spec.height, 0, 1, 0, 3)
    rgb = np.ascontiguousarray(ibuf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)

    h, w = rgb.shape[:2]
    desc = OCIO.PackedImageDesc(rgb, w, h, 3)
    cpu.apply(desc)

    rgb_u16 = np.clip(rgb * 65535.0, 0.0, 65535.0).astype(np.uint16)
    return idx, rgb_u16
