"""Multiprocessing worker functions for parallel OCIO + EXR I/O.

Each worker process lazily initializes its own OCIO CPUProcessor on first use
since OCIO.Config objects cannot be pickled across process boundaries.
"""

from __future__ import annotations

import numpy as np
import PyOpenColorIO as OCIO

from .exr_io import read_image, write_exr

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

    # Prefer the shared loader so version-mismatch errors include the fix hint
    # (oiio-python can rewire PyOpenColorIO onto a 2.4 dylib).
    from .ocio_utils import load_config_from_source_info

    cfg = load_config_from_source_info(config_source, config_path)

    proc = cfg.getProcessor(src_space, dst_space).getDefaultCPUProcessor()
    _worker_cpus[key] = proc
    return proc


def _alpha_over_rgb(bg_rgb: np.ndarray, fg_rgba: np.ndarray) -> np.ndarray:
    """Composite *fg_rgba* over *bg_rgb* (both float32, working space)."""
    a = fg_rgba[..., 3:4]
    fg = fg_rgba[..., :3]
    return fg * a + bg_rgb * (1.0 - a)


def process_frame_v2e(
    idx: int,
    rgb: np.ndarray,
    out_path: str,
    compression: str,
    config_source: str,
    config_path: str,
    src_space: str,
    dst_space: str,
    exr_opts: dict[str, str] | None = None,
    extra_attrs: dict[str, str] | None = None,
    pixel_aspect: float = 1.0,
    fps: float | None = None,
) -> int:
    """OCIO transform + write one EXR frame. Returns frame index.

    RGB is transformed; alpha (if present) is copied unchanged.
    """
    cpu = _ensure_cpu(config_source, config_path, src_space, dst_space)
    arr = np.ascontiguousarray(rgb, dtype=np.float32)
    rgb3 = np.ascontiguousarray(arr[:, :, :3])
    h, w = rgb3.shape[:2]
    cpu.apply(OCIO.PackedImageDesc(rgb3, w, h, 3))
    if arr.shape[2] >= 4:
        arr[:, :, :3] = rgb3
        out = arr[:, :, :4]
    else:
        out = rgb3
    write_exr(
        out_path,
        out,
        compression=compression,
        src_space=src_space,
        dst_space=dst_space,
        exr_opts=exr_opts,
        extra_attrs=extra_attrs,
        pixel_aspect=pixel_aspect,
        fps=fps,
    )
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
    keep_alpha: bool = False,
) -> tuple[int, np.ndarray]:
    """Read one still (EXR/PNG/JPG/…), run working-space comp, return (idx, rgb_u16).

    Pipeline:

    1. read image (in *src_space*)
    2. OCIO src→working (scene-linear)
    3. composite *overlay_working* (alpha-over) if provided — ``overlay_working``
       is a float32 RGBA buffer **already linearised into the working space**
    4. OCIO working→display
    5. quantise to uint16

    Raises
    ------
    RuntimeError
        If the frame cannot be read.
    """
    cpu_to_working = _ensure_cpu(config_source, config_path, src_space, working_space)
    cpu_to_display = _ensure_cpu(config_source, config_path, working_space, dst_space)

    rgb = read_image(path, keep_alpha=keep_alpha)
    h, w = rgb.shape[:2]
    rgb3 = np.ascontiguousarray(rgb[:, :, :3], dtype=np.float32)
    cpu_to_working.apply(OCIO.PackedImageDesc(rgb3, w, h, 3))

    if overlay_working is not None and overlay_working.shape[:2] == (h, w):
        rgb3 = _alpha_over_rgb(rgb3, overlay_working)
        rgb3 = np.ascontiguousarray(rgb3, dtype=np.float32)

    cpu_to_display.apply(OCIO.PackedImageDesc(rgb3, w, h, 3))
    if keep_alpha and rgb.shape[2] >= 4:
        out = np.concatenate([rgb3, rgb[:, :, 3:4]], axis=2)
    else:
        out = rgb3
    rgb_u16 = np.clip(out * 65535.0, 0.0, 65535.0).astype(np.uint16)
    return idx, rgb_u16
