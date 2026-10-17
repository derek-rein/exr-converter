"""Bilinear resize and the TrustMark Q content region.

The encoder runs at 256x256. Its residual is scaled back to the frame with
the same half-pixel bilinear filter PyTorch uses when ``align_corners`` is
false, then added to the full-resolution image.
"""

from __future__ import annotations

import numpy as np

MODEL_SIZE = 256
ASPECT_LIMIT = 2.0


def content_region(height: int, width: int) -> tuple[int, int, int, int]:
    """Return ``(top, left, height, width)`` of the area TrustMark Q marks.

    Frames wider or taller than 2:1 use a center square, matching TrustMark.
    Everything else is marked across the whole frame.
    """
    if height <= 0 or width <= 0:
        raise ValueError("Cannot watermark a frame with no area.")
    aspect = (width / height) if width > height else (height / width)
    if aspect <= ASPECT_LIMIT:
        return 0, 0, height, width
    side = min(height, width)
    if height > width:
        return (height - side) // 2, 0, side, side
    return 0, (width - side) // 2, side, side


def resize_bilinear(image: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """Resize ``HxWxC`` float image with half-pixel bilinear sampling."""
    if image.ndim != 3:
        raise ValueError("Expected an HxWxC image.")
    in_h, in_w = int(image.shape[0]), int(image.shape[1])
    if in_h == out_h and in_w == out_w:
        return image
    if in_h < 1 or in_w < 1 or out_h < 1 or out_w < 1:
        raise ValueError("Cannot resize an empty image.")

    y = (np.arange(out_h, dtype=np.float32) + 0.5) * (in_h / out_h) - 0.5
    x = (np.arange(out_w, dtype=np.float32) + 0.5) * (in_w / out_w) - 0.5
    y0 = np.floor(y).astype(np.int32)
    x0 = np.floor(x).astype(np.int32)
    wy = (y - y0).astype(np.float32)[:, None, None]
    wx = (x - x0).astype(np.float32)[None, :, None]
    y0 = np.clip(y0, 0, in_h - 1)
    x0 = np.clip(x0, 0, in_w - 1)
    y1 = np.clip(y0 + 1, 0, in_h - 1)
    x1 = np.clip(x0 + 1, 0, in_w - 1)

    top = image[y0][:, x0] * (1.0 - wx) + image[y0][:, x1] * wx
    bot = image[y1][:, x0] * (1.0 - wx) + image[y1][:, x1] * wx
    return top * (1.0 - wy) + bot * wy


def rgb_u8_to_model(rgb: np.ndarray, size: int = MODEL_SIZE) -> np.ndarray:
    """uint8 RGB to a contiguous ``(1, 3, size, size)`` tensor in ``[-1, 1]``."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("Expected HxWx3 RGB.")
    small = resize_bilinear(rgb.astype(np.float32) / 255.0, size, size)
    nchw = np.transpose(small, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(nchw * 2.0 - 1.0, dtype=np.float32)
