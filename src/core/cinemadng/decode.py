"""Decode one CinemaDNG frame to scene-linear RGB."""

from __future__ import annotations

import numpy as np

from .color import FrameMeta, develop, exposure_scale, oriented_size, output_colorspace
from .native import decode_mosaic, probe_meta


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
    return attrs


def display_size(path: str) -> tuple[int, int]:
    return oriented_size(probe_meta(path))
