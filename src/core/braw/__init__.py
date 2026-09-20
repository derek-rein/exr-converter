"""Optional Blackmagic RAW decode via the local BRAW SDK bridge.

Public API is stable: ``from src.core.braw import BRAWClip, is_available, …``.
"""

from __future__ import annotations

from pathlib import Path

from .clip import (
    BRAWClip,
    BRAWClipInfo,
    BRAWError,
    BRAWUnavailableError,
    braw_exr_attributes,
    probe_braw,
)
from .constants import (
    BMD_REDISTRIBUTABLE_NOTICE,
    BRAW_SRC_COLORSPACE_CANDIDATES,
    BRAW_SUFFIXES,
    CLIP_META_KEYS,
    DECODE_EIGHTH,
    DECODE_FULL,
    DECODE_HALF,
    DECODE_MODE_SCALE,
    DECODE_PREVIEW,
    DECODE_QUARTER,
    DECODE_THUMBNAIL,
    decode_mode_for_scale,
    scale_for_decode_mode,
)
from .native import decoder_kind, is_available, sdk_version, unavailable_reason
from .paths import bridge_candidates, bridge_names

_bridge_candidates = bridge_candidates
_bridge_names = bridge_names


def is_braw_path(path: str | Path) -> bool:
    """True if *path* looks like a Blackmagic RAW file by extension.

    Rejects macOS AppleDouble sidecars (``._clip.braw``).
    """
    p = Path(path)
    if p.name.startswith("._"):
        return False
    return p.suffix.lower() in BRAW_SUFFIXES


def braw_src_colorspace_candidates(path: str | Path = "") -> list[str]:
    """OCIO source-space candidates for Linear ACES AP0 decode."""
    _ = path
    return list(BRAW_SRC_COLORSPACE_CANDIDATES)


__all__ = [
    "BMD_REDISTRIBUTABLE_NOTICE",
    "BRAWClip",
    "BRAWClipInfo",
    "BRAWError",
    "BRAWUnavailableError",
    "BRAW_SRC_COLORSPACE_CANDIDATES",
    "BRAW_SUFFIXES",
    "CLIP_META_KEYS",
    "DECODE_EIGHTH",
    "DECODE_FULL",
    "DECODE_HALF",
    "DECODE_MODE_SCALE",
    "DECODE_PREVIEW",
    "DECODE_QUARTER",
    "DECODE_THUMBNAIL",
    "braw_exr_attributes",
    "braw_src_colorspace_candidates",
    "decode_mode_for_scale",
    "decoder_kind",
    "is_available",
    "is_braw_path",
    "probe_braw",
    "scale_for_decode_mode",
    "sdk_version",
    "unavailable_reason",
]
