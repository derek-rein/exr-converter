"""Resolve a CinemaDNG frame or a folder of frames to a sequence."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..sequence import find_exr_sequence
from .color import oriented_size, output_colorspace
from .decode import frame_exr_attrs, read_frame
from .native import probe_meta


def is_cinemadng_file(path: str) -> bool:
    return Path(path).suffix.lower() == ".dng"


def frame_paths(path: str) -> list[str]:
    """Ordered frames for a .dng file or a directory whose sequence is CinemaDNG.

    Raises RuntimeError when the path is not a CinemaDNG sequence.
    """
    paths, _name = find_exr_sequence(path)
    if not paths or not is_cinemadng_file(paths[0]):
        raise RuntimeError(f"Not a CinemaDNG sequence: {path}")
    return paths


def is_cinemadng_input(path: str) -> bool:
    """True for a .dng file or a directory whose preferred sequence is .dng."""
    candidate = Path(path)
    if candidate.is_file():
        return is_cinemadng_file(path)
    if not candidate.is_dir():
        return False
    try:
        frame_paths(path)
    except Exception:
        return False
    return True


def probe_sequence(path: str) -> tuple[int, int, float, int, str]:
    """Return width, height, fps, frame count, and OCIO colorspace."""
    paths = frame_paths(path)
    meta = probe_meta(paths[0])
    width, height = oriented_size(meta)
    fps = float(meta.frame_rate) if meta.frame_rate and meta.frame_rate > 0 else 24.0
    return width, height, fps, len(paths), output_colorspace(meta)


def read_sequence_frame(path: str, index_1based: int) -> tuple[np.ndarray, dict[str, str]]:
    """Develop one 1-based frame of the sequence at *path*."""
    paths = frame_paths(path)
    if index_1based < 1 or index_1based > len(paths):
        raise RuntimeError(f"CinemaDNG frame {index_1based} is outside 1..{len(paths)}")
    rgb, meta, colorspace = read_frame(paths[index_1based - 1])
    attrs = frame_exr_attrs(meta, colorspace)
    attrs["exrconverter:cinemadng:source_frame"] = str(index_1based)
    return rgb, attrs
