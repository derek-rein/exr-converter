from __future__ import annotations

import numpy as np
import OpenImageIO as oiio


def read_exr(path: str) -> np.ndarray:
    """Read an EXR file and return float32 array (H, W, 3)."""
    buf = oiio.ImageBuf(path)
    spec = buf.spec()
    roi = oiio.ROI(0, spec.width, 0, spec.height, 0, 1, 0, 3)
    return np.ascontiguousarray(buf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)


def write_exr(path: str, rgb: np.ndarray, compression: str = "dwaa") -> None:
    """Write a float32 (H, W, 3) array as half-float EXR."""
    h, w = rgb.shape[:2]
    spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
    spec.attribute("compression", compression)
    buf = oiio.ImageBuf(spec)
    buf.set_pixels(
        oiio.ROI(0, w, 0, h, 0, 1, 0, 3),
        np.ascontiguousarray(rgb, dtype=np.float32),
    )
    buf.write(path)
