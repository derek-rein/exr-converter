"""Decode one CinemaDNG frame to scene-linear RGB."""

from __future__ import annotations

import logging

import numpy as np
import OpenImageIO as oiio

from .color import (
    FrameMeta,
    develop,
    develop_half,
    exposure_scale,
    half_covers,
    oriented_size,
    output_colorspace,
)
from .native import decode_mosaic, probe_meta

log = logging.getLogger(__name__)


def probe_frame(path: str) -> FrameMeta:
    return probe_meta(path)


def read_frame(path: str) -> tuple[np.ndarray, FrameMeta, str]:
    """Return developed RGB, the header, and the OCIO colorspace name."""
    import numpy as np

    mosaic, meta = decode_mosaic(path)
    rgb, colorspace = develop(mosaic, meta)
    return np.ascontiguousarray(rgb, dtype=np.float32), meta, colorspace


def read_rgb(path: str) -> np.ndarray:
    """Developed float32 RGB for image-sequence readers."""
    rgb, _meta, _colorspace = read_frame(path)
    return rgb


def _embedded_rgb(path: str, raw_w: int, raw_h: int) -> np.ndarray | None:
    """A 3-channel subimage smaller than the raw mosaic, or None.

    The Bayer plane is one channel, so it is skipped. This only reads a
    preview IFD. It does not develop the mosaic.
    """
    raw_pixels = max(1, int(raw_w) * int(raw_h))
    inp = oiio.ImageInput.open(path)
    if not inp:
        return None
    try:
        found: list[tuple[int, int]] = []
        sub = 0
        while sub < 8 and inp.seek_subimage(sub, 0):
            spec = inp.spec()
            width = int(spec.width)
            height = int(spec.height)
            pixels = width * height
            if width > 0 and height > 0 and int(spec.nchannels) >= 3 and pixels < raw_pixels:
                found.append((pixels, sub))
            sub += 1
        if not found:
            return None
        _pixels, subimage = max(found)
        arr = np.asarray(inp.read_image(subimage, 0, 0, 3, oiio.FLOAT), dtype=np.float32)
        if arr.ndim == 2:
            arr = np.repeat(arr[:, :, np.newaxis], 3, axis=2)
        if arr.ndim != 3 or arr.shape[2] < 3 or arr.size == 0:
            return None
        return np.ascontiguousarray(arr[:, :, :3])
    except Exception:
        log.debug("CinemaDNG embedded preview was not readable", exc_info=True)
        return None
    finally:
        inp.close()


def read_rgb_preview(
    path: str,
    target_w: int,
    target_h: int,
    *,
    with_draft: bool = False,
) -> tuple[np.ndarray | None, np.ndarray, bool]:
    """``(draft or None, RGB, reduced)`` for a browser preview.

    An embedded preview is used when it covers the target (no mosaic decode).
    Otherwise a half-size debayer is used when it still covers, and the full
    demosaic only when the pane is larger than half. *reduced* is false for
    that full demosaic, which is the same work as :func:`read_rgb`.
    """
    draft: np.ndarray | None = None
    meta = probe_meta(path)
    embedded = _embedded_rgb(path, int(meta.width), int(meta.height))
    if embedded is not None:
        height, width = int(embedded.shape[0]), int(embedded.shape[1])
        if width >= int(target_w) and height >= int(target_h):
            return None, embedded, True
        if with_draft and max(width, height) >= 64:
            draft = embedded
    mosaic, meta = decode_mosaic(path)
    if half_covers(meta, target_w, target_h):
        rgb, _colorspace = develop_half(mosaic, meta)
        return draft, rgb, True
    rgb, _colorspace = develop(mosaic, meta)
    return draft, rgb, False


def colorspace_of_file(path: str) -> str:
    return output_colorspace(probe_meta(path))


def frame_exr_attrs(meta: FrameMeta, colorspace: str) -> dict[str, str]:
    """Extra EXR attributes for a developed CinemaDNG frame."""
    attrs: dict[str, str] = {}
    if meta.make:
        attrs["exrconverter:cinemadng:make"] = meta.make
    if meta.model:
        attrs["exrconverter:cinemadng:model"] = meta.model
    if meta.unique_model:
        attrs["exrconverter:cinemadng:unique_camera_model"] = meta.unique_model
    if colorspace:
        attrs["exrconverter:cinemadng:colorspace"] = colorspace
    attrs["exrconverter:cinemadng:baseline_exposure"] = f"{meta.baseline_exposure:.6g}"
    attrs["exrconverter:cinemadng:exposure_scale"] = f"{exposure_scale(meta):.6g}"
    attrs["exrconverter:cinemadng:compression"] = (
        "uncompressed" if meta.compression == 1 else "lossless-jpeg"
    )
    if meta.has_default_scale:
        h, v = meta.default_scale
        attrs["exrconverter:cinemadng:default_scale"] = f"{h:.6g} {v:.6g}"
    return attrs


def display_size(path: str) -> tuple[int, int]:
    return oriented_size(probe_meta(path))
