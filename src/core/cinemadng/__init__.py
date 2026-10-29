"""CinemaDNG frame sequences (uncompressed and lossless JPEG Bayer)."""

from __future__ import annotations

from .color import ACES2065_1, LINEAR_P3_D60
from .decode import colorspace_of_file, read_rgb
from .input import is_cinemadng_file, is_cinemadng_input, probe_sequence

__all__ = [
    "ACES2065_1",
    "LINEAR_P3_D60",
    "colorspace_of_file",
    "is_cinemadng_file",
    "is_cinemadng_input",
    "probe_sequence",
    "read_rgb",
]
