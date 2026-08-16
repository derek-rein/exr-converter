from __future__ import annotations

from fractions import Fraction

import numpy as np
import OpenImageIO as oiio


def _display_window(spec) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of the display window from an OIIO ImageSpec.

    Falls back to data window dimensions when full_width/full_height are unset.
    """
    if spec.full_width > 0 and spec.full_height > 0:
        return spec.full_x, spec.full_y, spec.full_width, spec.full_height
    return 0, 0, spec.width, spec.height


def fps_to_rational(fps: float) -> tuple[int, int] | None:
    """Exact-ish (num, den) for OpenEXR / OIIO FramesPerSecond."""
    if fps is None or fps <= 0:
        return None
    if abs(fps - round(fps)) < 1e-6:
        return int(round(fps)), 1
    known = {
        23.976: (24000, 1001),
        23.98: (24000, 1001),
        29.97: (30000, 1001),
        59.94: (60000, 1001),
    }
    for key, rat in known.items():
        if abs(fps - key) < 0.01:
            return rat
    frac = Fraction(fps).limit_denominator(1001)
    if frac.numerator <= 0 or frac.denominator <= 0:
        return None
    return int(frac.numerator), int(frac.denominator)


def square_pixel_dims(width: int, height: int, pixel_aspect: float) -> tuple[int, int]:
    """Even output size that displays as square pixels (EXR → video)."""
    w, h = int(width), int(height)
    par = float(pixel_aspect) if pixel_aspect and pixel_aspect > 0 else 1.0
    if abs(par - 1.0) >= 1e-4:
        w = max(2, int(round(w * par)))
    w -= w % 2
    h -= h % 2
    return max(2, w), max(2, h)


def apply_exr_compression_attrs(
    spec: oiio.ImageSpec,
    compression: str,
    exr_opts: dict[str, str] | None = None,
) -> None:
    """Set compression name and optional DWA / ZIP level attributes on *spec*."""
    name = (compression or "zip").strip().lower()
    spec.attribute("compression", name)
    if not exr_opts:
        return
    if name in ("dwaa", "dwab"):
        level = exr_opts.get("dwa_compression_level")
        if level is not None:
            try:
                spec.attribute("dwaCompressionLevel", float(level))
            except (TypeError, ValueError):
                pass
    elif name in ("zip", "zips"):
        level = exr_opts.get("zip_level")
        if level is not None:
            try:
                n = int(level)
                if 1 <= n <= 9:
                    spec.attribute("compressionlevel", n)
            except (TypeError, ValueError):
                pass


def read_pixel_aspect(path: str) -> float:
    """PixelAspectRatio from the file header, or 1.0."""
    try:
        inp = oiio.ImageInput.open(path)
        if not inp:
            return 1.0
        try:
            val = inp.spec().getattribute("PixelAspectRatio")
        finally:
            inp.close()
        if val is None:
            return 1.0
        par = float(val)
        return par if par > 0 else 1.0
    except Exception:
        return 1.0


def _normalize_pixels(pixels: np.ndarray, want_alpha: bool) -> np.ndarray:
    if pixels.ndim == 2:
        rgb = np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
        if want_alpha:
            a = np.ones(rgb.shape[:2] + (1,), dtype=rgb.dtype)
            return np.concatenate([rgb, a], axis=2)
        return rgb
    if pixels.ndim != 3:
        raise RuntimeError(f"Unsupported pixel layout: shape={pixels.shape}")
    nch = pixels.shape[2]
    if want_alpha:
        if nch >= 4:
            return np.ascontiguousarray(pixels[:, :, :4])
        rgb = pixels[:, :, :3] if nch >= 3 else np.repeat(pixels[:, :, :1], 3, axis=2)
        a = np.ones(rgb.shape[:2] + (1,), dtype=pixels.dtype)
        return np.ascontiguousarray(np.concatenate([rgb, a], axis=2))
    if nch >= 3:
        return np.ascontiguousarray(pixels[:, :, :3])
    if nch == 1:
        return np.repeat(pixels, 3, axis=2)
    raise RuntimeError(f"Unsupported pixel layout: shape={pixels.shape}")


def read_image(path: str, *, keep_alpha: bool = False) -> np.ndarray:
    """Read an image (EXR, PNG, JPEG, … via OIIO) and return float32 RGB(A).

    Values are float32 in the file's native range as returned by OIIO
    (typically ~0–1 for integer 8/16-bit stills and scene-linear for EXR).
    Crops to the display window, discarding any overscan from the data window.

    When *keep_alpha* is true and the file has an alpha channel, returns
    ``(H, W, 4)``; otherwise always ``(H, W, 3)``.
    """
    buf = oiio.ImageBuf(path)
    if buf.has_error:
        raise RuntimeError(f"Failed to open image {path!r}: {buf.geterror()}")
    spec = buf.spec()
    dx, dy, dw, dh = _display_window(spec)
    if dw <= 0 or dh <= 0:
        raise RuntimeError(f"Invalid display window in image {path!r}")
    nch = spec.nchannels
    want_a = bool(keep_alpha and nch >= 4)
    chend = 4 if want_a else min(nch, 3)
    roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, chend)
    pixels = np.ascontiguousarray(buf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)
    if buf.has_error:
        raise RuntimeError(f"Failed to read pixels from image {path!r}: {buf.geterror()}")
    if pixels is None or pixels.size == 0:
        raise RuntimeError(f"Empty pixel buffer from image {path!r}")
    return _normalize_pixels(pixels, want_a)


def read_exr(path: str) -> np.ndarray:
    """Alias for :func:`read_image` (historical name; works for any OIIO still)."""
    return read_image(path)


def _apply_fps_attr(spec: oiio.ImageSpec, fps: float | None) -> None:
    rat = fps_to_rational(float(fps) if fps else 0.0)
    if rat is None:
        return
    spec.attribute("framesPerSecond", oiio.TypeRational, rat)


def write_exr(
    path: str,
    rgb: np.ndarray,
    compression: str = "dwaa",
    src_space: str = "",
    dst_space: str = "",
    exr_opts: dict[str, str] | None = None,
    extra_attrs: dict[str, str] | None = None,
    *,
    pixel_aspect: float = 1.0,
    fps: float | None = None,
) -> None:
    """Write a float32 (H, W, 3) or (H, W, 4) array as half-float EXR.

    *rgb* must already be in *dst_space* — the caller runs OCIO. This writer
    does not colour-convert. Always half; no float or deep output.

    OCIO space names are stored as ``exrconverter:srcColorSpace`` /
    ``exrconverter:dstColorSpace`` so later reads can pick the same config
    spaces. Pixel aspect, frames-per-second, and *extra_attrs* (e.g. R3D
    camera keys) are written as EXR attributes.
    """
    from .constants import APP_NAME, APP_VERSION

    arr = np.ascontiguousarray(rgb, dtype=np.float32)
    if arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise RuntimeError(f"write_exr expects HxWx3 or HxWx4, got {arr.shape}")
    h, w, nch = arr.shape
    spec = oiio.ImageSpec(w, h, nch, oiio.HALF)
    apply_exr_compression_attrs(spec, compression, exr_opts)
    spec.attribute("Software", f"{APP_NAME} {APP_VERSION}")
    par = float(pixel_aspect) if pixel_aspect and pixel_aspect > 0 else 1.0
    spec.attribute("PixelAspectRatio", float(par))
    _apply_fps_attr(spec, fps)
    if src_space:
        spec.attribute("exrconverter:srcColorSpace", str(src_space))
    if dst_space:
        spec.attribute("exrconverter:dstColorSpace", str(dst_space))
    if extra_attrs:
        for key, val in extra_attrs.items():
            if key is None or val is None:
                continue
            k = str(key).strip()
            v = str(val).strip()
            if not k or not v:
                continue
            spec.attribute(k, v)
    buf = oiio.ImageBuf(spec)
    buf.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, nch), arr[:, :, :nch])
    ok = buf.write(path)
    if not ok or buf.has_error:
        err = buf.geterror() or "unknown OIIO write error"
        raise RuntimeError(f"Failed to write EXR {path!r}: {err}")
