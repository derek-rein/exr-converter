"""Optional invisible TrustMark watermarks (ONNX Runtime, MIT models)."""

from __future__ import annotations

from .detect import WatermarkRead, decode_rgb, detect_video
from .embed import FrameWatermark
from .payload import (
    DEFAULT_STRENGTH,
    MAX_TEXT_CHARS,
    normalize_strength,
    normalize_watermark_text,
)

__all__ = [
    "DEFAULT_STRENGTH",
    "MAX_TEXT_CHARS",
    "FrameWatermark",
    "WatermarkRead",
    "decode_rgb",
    "detect_video",
    "normalize_strength",
    "normalize_watermark_text",
]
