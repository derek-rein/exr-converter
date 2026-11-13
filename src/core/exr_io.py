from __future__ import annotations

import logging

import numpy as np
import OpenImageIO as oiio

from .cinemadng import read_rgb
from .cinemadng.decode import display_size as dng_display_size
from .cinemadng.decode import read_rgb_preview
from .pixel_aspect import (
    ATTR_OPTICAL_SQUEEZE,
    ATTR_PIXEL_ASPECT,
    ATTR_SAMPLE_ASPECT,
    PixelAspect,
    is_square,
    merge_for_write,
)

log = logging.getLogger(__name__)


def _display_window(spec) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of the display window from an OIIO ImageSpec.

    Falls back to data window dimensions when full_width/full_height are unset.
    """
    if spec.full_width > 0 and spec.full_height > 0:
        return spec.full_x, spec.full_y, spec.full_width, spec.full_height
    return 0, 0, spec.width, spec.height


def apply_exr_compression_attrs(
    spec: oiio.ImageSpec,
    compression: str,
    exr_opts: dict[str, str] | None = None,
) -> None:
    """Set compression name and optional DWA / ZIP level attributes on *spec*."""
    spec.attribute("compression", compression)
    if not exr_opts:
        return
    if compression in ("dwaa", "dwab"):
        level = exr_opts.get("dwa_compression_level")
        if level is not None:
            try:
                # OIIO accepts the unprefixed name and stores it as
                # openexr:dwaCompressionLevel on the written file.
                spec.attribute("dwaCompressionLevel", float(level))
            except (TypeError, ValueError):
                pass
    elif compression in ("zip", "zips"):
        level = exr_opts.get("zip_level")
        if level is not None:
            try:
                # OIIO / OpenEXR zip compression level (1–9).
                spec.attribute("compressionlevel", int(level))
            except (TypeError, ValueError):
                pass


def read_image(path: str) -> np.ndarray:
    """Read an image (EXR, PNG, JPEG, CinemaDNG, …) and return float32 RGB (H, W, 3).

    Values are float32 in the file's native range as returned by OIIO
    (typically ~0–1 for integer 8/16-bit stills and scene-linear for EXR).
    Always extracts RGB only, even if the source has alpha or more channels.
    Crops to the display window, discarding any overscan from the data window.

    Raises
    ------
    RuntimeError
        If the file cannot be opened or pixels cannot be read.
    """
    if path.lower().endswith(".dng"):
        return read_rgb(path)
    buf = oiio.ImageBuf(path)
    if buf.has_error:
        raise RuntimeError(f"Failed to open image {path!r}: {buf.geterror()}")
    spec = buf.spec()
    dx, dy, dw, dh = _display_window(spec)
    if dw <= 0 or dh <= 0:
        raise RuntimeError(f"Invalid display window in image {path!r}")
    roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, min(spec.nchannels, 3))
    pixels = np.ascontiguousarray(buf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)
    if buf.has_error:
        raise RuntimeError(f"Failed to read pixels from image {path!r}: {buf.geterror()}")
    if pixels is None or pixels.size == 0:
        raise RuntimeError(f"Empty pixel buffer from image {path!r}")
    if pixels.ndim == 3 and pixels.shape[2] >= 3:
        return pixels[:, :, :3]
    if pixels.ndim == 3 and pixels.shape[2] == 1:
        return np.repeat(pixels, 3, axis=2)
    if pixels.ndim == 2:
        return np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
    raise RuntimeError(f"Unsupported pixel layout in image {path!r}: shape={pixels.shape}")


def _alpha_channel_index(spec) -> int:
    idx = int(getattr(spec, "alpha_channel", -1))
    if idx >= 0:
        return idx
    names = [str(n) for n in spec.channelnames]
    for cand in ("A", "a", "Alpha", "alpha"):
        if cand in names:
            return names.index(cand)
    return -1


def image_has_alpha(path: str) -> bool:
    """True when the still at *path* has an alpha channel (header only)."""
    inp = oiio.ImageInput.open(path)
    if not inp:
        return False
    try:
        return _alpha_channel_index(inp.spec()) >= 0
    finally:
        inp.close()


def read_image_alpha(path: str) -> np.ndarray | None:
    """Alpha channel (float32, H×W, display window) or ``None`` when there is none."""
    buf = oiio.ImageBuf(path)
    if buf.has_error:
        raise RuntimeError(f"Failed to open image {path!r}: {buf.geterror()}")
    spec = buf.spec()
    idx = _alpha_channel_index(spec)
    if idx < 0:
        return None
    dx, dy, dw, dh = _display_window(spec)
    roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, idx, idx + 1)
    pixels = buf.get_pixels(oiio.FLOAT, roi)
    if buf.has_error or pixels is None:
        raise RuntimeError(f"Failed to read alpha from image {path!r}: {buf.geterror()}")
    arr = np.asarray(pixels, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    return np.ascontiguousarray(arr)


def read_exr(path: str) -> np.ndarray:
    """Alias for :func:`read_image` (historical name; works for any OIIO still)."""
    return read_image(path)


def image_header_size(path: str) -> tuple[int, int]:
    """Display width and height from the header. Does not read pixels."""
    if path.lower().endswith(".dng"):
        return dng_display_size(path)
    inp = oiio.ImageInput.open(path)
    if not inp:
        raise RuntimeError(f"Failed to open image {path!r}")
    try:
        spec = inp.spec()
        width = int(spec.full_width) if spec.full_width > 0 else int(spec.width)
        height = int(spec.full_height) if spec.full_height > 0 else int(spec.height)
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Invalid image size in {path!r}")
        return width, height
    finally:
        inp.close()


def _level_size(spec) -> tuple[int, int]:
    width = int(spec.full_width) if spec.full_width > 0 else int(spec.width)
    height = int(spec.full_height) if spec.full_height > 0 else int(spec.height)
    return width, height


def _rgb_span(spec) -> tuple[int, int] | None:
    """Contiguous channel range that includes R, G, and B, or the first channels."""
    names = [str(name).split(".")[-1].lower() for name in spec.channelnames]
    index = {name: i for i, name in enumerate(names)}

    def find(*options: str) -> int | None:
        for option in options:
            if option in index:
                return index[option]
        return None

    red, green, blue = find("r", "red"), find("g", "green"), find("b", "blue")
    count = int(spec.nchannels)
    if red is None or green is None or blue is None:
        if count >= 3:
            return 0, 3
        if count == 1:
            return 0, 1
        return None
    return min(red, green, blue), max(red, green, blue) + 1


def _as_rgb(arr: np.ndarray, spec, chbegin: int) -> np.ndarray:
    pixels = np.asarray(arr, dtype=np.float32)
    if pixels.ndim == 2:
        pixels = pixels[:, :, np.newaxis]
    if pixels.ndim != 3 or pixels.size == 0:
        raise RuntimeError("Preview read returned no pixels")
    names = [str(name).split(".")[-1].lower() for name in list(spec.channelnames)[chbegin:]]
    index = {name: i for i, name in enumerate(names)}

    def find(*options: str) -> int | None:
        for option in options:
            if option in index and index[option] < pixels.shape[2]:
                return index[option]
        return None

    red, green, blue = find("r", "red"), find("g", "green"), find("b", "blue")
    if red is None or green is None or blue is None:
        if pixels.shape[2] >= 3:
            rgb = pixels[:, :, :3]
        elif pixels.shape[2] == 1:
            rgb = np.repeat(pixels, 3, axis=2)
        else:
            raise RuntimeError("Preview read has no RGB channels")
    else:
        rgb = np.stack([pixels[:, :, red], pixels[:, :, green], pixels[:, :, blue]], axis=2)
    return np.ascontiguousarray(rgb, dtype=np.float32)


def _crop_display(arr: np.ndarray, spec) -> np.ndarray:
    if spec.full_width <= 0 or spec.full_height <= 0:
        return arr
    x0 = int(spec.full_x - spec.x)
    y0 = int(spec.full_y - spec.y)
    height, width = int(arr.shape[0]), int(arr.shape[1])
    x0 = max(0, min(x0, max(0, width - 1)))
    y0 = max(0, min(y0, max(0, height - 1)))
    x1 = max(x0 + 1, min(width, x0 + int(spec.full_width)))
    y1 = max(y0 + 1, min(height, y0 + int(spec.full_height)))
    if x0 == 0 and y0 == 0 and x1 == width and y1 == height:
        return arr
    return np.ascontiguousarray(arr[y0:y1, x0:x1])


def _collect_levels(inp) -> list[tuple[int, int, int, int, int, int, int]]:
    """``(pixels, sub, mip, chbegin, chend, width, height)`` for each RGB level."""
    found: list[tuple[int, int, int, int, int, int, int]] = []
    sub = 0
    while sub < 16 and inp.seek_subimage(sub, 0):
        mip = 0
        seen: set[tuple[int, int]] = set()
        while mip < 16 and inp.seek_subimage(sub, mip):
            spec = inp.spec()
            span = _rgb_span(spec)
            width, height = _level_size(spec)
            if span is None or width <= 0 or height <= 0:
                break
            key = (width, height)
            if key in seen:
                break
            seen.add(key)
            begin, end = span
            found.append((width * height, sub, mip, begin, end, width, height))
            mip += 1
        sub += 1
    return found


def _read_level(inp, sub: int, mip: int, begin: int, end: int) -> np.ndarray:
    if not inp.seek_subimage(sub, mip):
        raise RuntimeError("Preview level is not in this file")
    spec = inp.spec()
    arr = inp.read_image(sub, mip, begin, end, oiio.FLOAT)
    rgb = _as_rgb(np.asarray(arr), spec, begin)
    return _crop_display(rgb, spec)


def read_image_preview(
    path: str,
    target_w: int,
    target_h: int,
    *,
    with_draft: bool = False,
    on_draft: object | None = None,
) -> tuple[np.ndarray | None, np.ndarray, bool]:
    """``(draft or None, RGB, reduced)`` for a browser preview.

    Uses an OpenEXR mip or ripmap, or a smaller RGB subimage, when one still
    covers *target*. Otherwise this is :func:`read_image` (same pixels, no
    extra scale). *reduced* is false in that case. CinemaDNG goes through
    :func:`read_rgb_preview`.

    *on_draft* is called with the small frame before the sharp read, so the
    UI can show it while the covering level is still loading.
    """
    if path.lower().endswith(".dng"):
        return read_rgb_preview(path, target_w, target_h, with_draft=with_draft)
    try:
        return _read_image_preview(
            path,
            target_w,
            target_h,
            with_draft=with_draft,
            on_draft=on_draft,
        )
    except Exception:
        log.debug("Reduced preview read failed for %s", path, exc_info=True)
        return None, read_image(path), False


def _read_image_preview(
    path: str,
    target_w: int,
    target_h: int,
    *,
    with_draft: bool,
    on_draft: object | None = None,
) -> tuple[np.ndarray | None, np.ndarray, bool]:
    target_w = max(1, int(target_w))
    target_h = max(1, int(target_h))
    inp = oiio.ImageInput.open(path)
    if not inp:
        raise RuntimeError(f"Failed to open image {path!r}")
    draft: np.ndarray | None = None
    try:
        levels = _collect_levels(inp)
        if not levels:
            raise RuntimeError(f"No preview level in {path!r}")
        covering = [level for level in levels if level[5] >= target_w and level[6] >= target_h]
        pool = covering if covering else [max(levels, key=lambda level: level[0])]
        chosen = min(pool, key=lambda level: level[0])
        _pixels, sub, mip, begin, end, _width, _height = chosen
        # One full RGB (or fewer) level is exactly what read_image does.
        same_as_full = len(levels) == 1 and sub == 0 and mip == 0 and begin == 0 and end <= 3
        if same_as_full:
            final_box = None
            reduced = False
        else:
            reduced = mip != 0 or sub != 0
            if with_draft and reduced:
                # Only a much smaller level. A draft that is merely one step
                # down costs enough that the sharp frame arrives no sooner
                # than a full read.
                budget = max(1, chosen[0] // 16)
                drafts = [
                    level
                    for level in levels
                    if level[0] <= budget and max(level[5], level[6]) >= 64 and level[0] < chosen[0]
                ]
                if drafts and chosen[0] >= 1280 * 720:
                    draft_level = max(drafts, key=lambda level: level[0])
                    draft = _read_level(
                        inp,
                        draft_level[1],
                        draft_level[2],
                        draft_level[3],
                        draft_level[4],
                    )
                    if draft is not None and callable(on_draft):
                        on_draft(draft)
            final_box = _read_level(inp, sub, mip, begin, end)
            if final_box.shape[1] < _width or final_box.shape[0] < _height:
                reduced = True
    finally:
        inp.close()
    if final_box is None:
        return None, read_image(path), False
    return draft, final_box, reduced


def read_exr_uint16(path: str) -> np.ndarray | None:
    """Read an image and return uint16 RGB in display window, or ``None`` on failure."""
    try:
        buf = oiio.ImageBuf(path)
        if buf.has_error:
            return None
        spec = buf.spec()
        dx, dy, dw, dh = _display_window(spec)
        roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, min(spec.nchannels, 3))
        pixels = buf.get_pixels(oiio.UINT16, roi)
        if pixels is None:
            return None
        if pixels.ndim == 3 and pixels.shape[2] >= 3:
            return np.ascontiguousarray(pixels[:, :, :3])
        if pixels.ndim == 3 and pixels.shape[2] == 1:
            return np.repeat(pixels, 3, axis=2)
        return np.ascontiguousarray(pixels)
    except Exception:
        return None


def read_exr_safe(path: str, w: int, h: int) -> np.ndarray:
    """Read an image, returning a black frame of (h, w, 3) on any error or size mismatch."""
    try:
        rgb = read_image(path)
        if rgb.shape[:2] != (h, w):
            return np.zeros((h, w, 3), dtype=np.float32)
        return rgb
    except Exception:
        return np.zeros((h, w, 3), dtype=np.float32)


def _set_float_attribute(spec: oiio.ImageSpec, key: str, value: float) -> None:
    """Store *value* as a float. A string is the wrong type for these keys."""
    spec.attribute(key, float(value))


def _apply_pixel_aspect(spec: oiio.ImageSpec, aspect: object) -> None:
    """Write OpenEXR pixel aspect as a real float, plus the named squeeze field.

    A string ``"2.0"`` is ignored and the file stays at 1.0. This OpenImageIO
    binding has no ``pixelAspectRatio`` field; ``attribute(name, float)`` is
    the call that writes the header float.
    """
    if not isinstance(aspect, PixelAspect):
        return
    exr_factor = float(aspect.exr_factor)
    _set_float_attribute(spec, ATTR_PIXEL_ASPECT, exr_factor)
    if not is_square(exr_factor):
        _set_float_attribute(spec, ATTR_OPTICAL_SQUEEZE, exr_factor)
    if aspect.kind == "broadcast" and not is_square(aspect.factor):
        _set_float_attribute(spec, ATTR_SAMPLE_ASPECT, float(aspect.factor))


def write_exr(
    path: str,
    rgb: np.ndarray,
    compression: str = "dwaa",
    src_space: str = "",
    dst_space: str = "",
    exr_opts: dict[str, str] | None = None,
    extra_attrs: dict[str, str] | None = None,
    aspect: object | None = None,
) -> None:
    """Write a float32 (H, W, 3) array as half-float EXR.

    *rgb* must already be in *dst_space* (caller runs OCIO). We do **not** ask
    OIIO to colour-convert on write.

    Colour metadata: OIIO 3.x rewrites many OCIO names (including ``ACEScg``)
    to a generic ``lin_rec709`` ``oiio:ColorSpace`` tag without changing
    pixels. Trust **``exrconverter:dstColorSpace``** for the true OCIO space
    of the samples; ``oiio:ColorSpace`` is only a coarse “scene-linear” hint.

    *extra_attrs* are optional string attributes (e.g. R3D camera metadata
    under ``exrconverter:r3d:*``). ``pixelAspectRatio`` is the exception:
    OpenImageIO drops a string ``"2.0"`` and writes 1.0, so that key (and the
    optical-squeeze field) is stored as a float. *aspect* is a
    :class:`~src.core.pixel_aspect.PixelAspect`. When it is omitted, a
    ``pixelAspectRatio`` entry in *extra_attrs* is used.

    Raises
    ------
    RuntimeError
        If the write fails (disk full, permissions, unsupported compression, …).
    """
    from .constants import APP_NAME, APP_VERSION

    h, w = rgb.shape[:2]
    spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
    apply_exr_compression_attrs(spec, compression, exr_opts)
    spec.attribute("Software", f"{APP_NAME} {APP_VERSION}")
    # Honest OCIO names — these survive round-trips (unlike oiio:ColorSpace).
    if src_space:
        spec.attribute("exrconverter:srcColorSpace", str(src_space))
    if dst_space:
        spec.attribute("exrconverter:dstColorSpace", str(dst_space))
        # Optional coarse tag for other apps; may be rewritten by OIIO.
        # Do not rely on this for our player — use exrconverter:dstColorSpace.
        try:
            spec.attribute("oiio:ColorSpace", str(dst_space))
        except Exception:
            pass
    final = merge_for_write(aspect if isinstance(aspect, PixelAspect) else None, extra_attrs)
    _apply_pixel_aspect(spec, final)
    skip = {ATTR_PIXEL_ASPECT, ATTR_OPTICAL_SQUEEZE, ATTR_SAMPLE_ASPECT}
    if extra_attrs:
        for key, val in extra_attrs.items():
            if key is None or val is None:
                continue
            k = str(key).strip()
            v = str(val).strip()
            if not k or not v or k in skip:
                continue
            try:
                spec.attribute(k, v)
            except Exception:
                pass
    buf = oiio.ImageBuf(spec)
    # set_pixels + write must not run OIIO colorconvert (pixels already OCIO'd).
    buf.set_pixels(
        oiio.ROI(0, w, 0, h, 0, 1, 0, 3),
        np.ascontiguousarray(rgb[:, :, :3], dtype=np.float32),
    )
    ok = buf.write(path)
    if not ok or buf.has_error:
        err = buf.geterror() or "unknown OIIO write error"
        raise RuntimeError(f"Failed to write EXR {path!r}: {err}")
