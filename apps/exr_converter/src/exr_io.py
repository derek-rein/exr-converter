from __future__ import annotations

import numpy as np
import OpenImageIO as oiio


def read_exr(path: str) -> np.ndarray:
    """Read an EXR file and return float32 array (H, W, 3).

    Always extracts RGB only, even if the source has alpha or more channels.
    Returns a black frame if the file is corrupt or unreadable.
    """
    try:
        buf = oiio.ImageBuf(path)
        if buf.has_error:
            raise RuntimeError(buf.geterror())
        spec = buf.spec()
        roi = oiio.ROI(0, spec.width, 0, spec.height, 0, 1, 0, min(spec.nchannels, 3))
        pixels = np.ascontiguousarray(buf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)
        if pixels.ndim == 3 and pixels.shape[2] >= 3:
            return pixels[:, :, :3]
        if pixels.ndim == 3 and pixels.shape[2] == 1:
            return np.repeat(pixels, 3, axis=2)
        return pixels
    except Exception:
        try:
            inp = oiio.ImageInput.open(path)
            if inp:
                s = inp.spec()
                inp.close()
                return np.zeros((s.height, s.width, 3), dtype=np.float32)
        except Exception:
            pass
        return np.zeros((1080, 1920, 3), dtype=np.float32)


def read_exr_safe(path: str, w: int, h: int) -> np.ndarray:
    """Read an EXR, returning a black frame of (h, w, 3) on any error."""
    try:
        rgb = read_exr(path)
        if rgb.shape[:2] != (h, w):
            return rgb
        return rgb
    except Exception:
        return np.zeros((h, w, 3), dtype=np.float32)


def write_exr(
    path: str,
    rgb: np.ndarray,
    compression: str = "dwaa",
    src_space: str = "",
    dst_space: str = "",
) -> None:
    """Write a float32 (H, W, 3) array as half-float EXR."""
    from .constants import APP_NAME, APP_VERSION

    h, w = rgb.shape[:2]
    spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
    spec.attribute("compression", compression)
    spec.attribute("Software", f"{APP_NAME} {APP_VERSION}")
    if dst_space:
        spec.attribute("oiio:ColorSpace", dst_space)
    if src_space:
        spec.attribute("exrconverter:srcColorSpace", src_space)
    if dst_space:
        spec.attribute("exrconverter:dstColorSpace", dst_space)
    buf = oiio.ImageBuf(spec)
    buf.set_pixels(
        oiio.ROI(0, w, 0, h, 0, 1, 0, 3),
        np.ascontiguousarray(rgb[:, :, :3], dtype=np.float32),
    )
    buf.write(path)
