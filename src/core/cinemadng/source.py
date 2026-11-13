"""Video-to-EXR ingest for a CinemaDNG frame sequence."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import numpy as np

from ..frame_source import MediaInfo, resize_rgb_f32, scaled_dims
from ..pixel_aspect import aspect_from_default_scale
from .color import LINEAR_P3_D60
from .decode import read_rgb_preview
from .input import frame_paths, probe_sequence, read_sequence_frame
from .native import probe_meta

ProgressCancel = Callable[[], bool]


class CinemaDNGPreviewDecoder:
    """Random-access preview of a CinemaDNG sequence. Indices are 1-based."""

    def __init__(
        self,
        path: str,
        *,
        fps: float = 0.0,
        target_size: tuple[int, int] | None = None,
    ) -> None:
        self.path = path
        width, height, probed_fps, count, _colorspace = probe_sequence(path)
        self.fps = float(fps) if fps and fps > 0 else probed_fps
        self._width = width
        self._height = height
        self._count = count
        self._target = target_size
        self._last: tuple[int, np.ndarray] | None = None

    def close(self) -> None:
        return

    @property
    def decode_size(self) -> tuple[int, int]:
        return self._width, self._height

    def get_frame(self, idx_1based: int) -> np.ndarray | None:
        if idx_1based < 1 or idx_1based > self._count:
            return None
        if self._last is not None and self._last[0] == idx_1based:
            return self._last[1]
        if self._target is None:
            rgb, _attrs = read_sequence_frame(self.path, idx_1based)
            self._last = (idx_1based, rgb)
            return rgb
        try:
            _draft, rgb, _reduced = read_rgb_preview(
                frame_paths(self.path)[idx_1based - 1],
                self._target[0],
                self._target[1],
                with_draft=False,
            )
        except Exception:
            return None
        self._last = (idx_1based, rgb)
        return rgb


class CinemaDNGIngestSource:
    """Sequential CinemaDNG frames for video2exr."""

    def __init__(self, path: str, *, scale: float = 1.0) -> None:
        self._path = path
        self._scale = float(scale)
        width, height, fps, count, colorspace = probe_sequence(path)
        self._colorspace = colorspace
        self._header_meta = probe_meta(frame_paths(path)[0])
        aspect = aspect_from_default_scale(
            self._header_meta.default_scale[0],
            self._header_meta.default_scale[1],
            present=self._header_meta.has_default_scale,
        )
        self.pixel_aspect = aspect
        self._info = MediaInfo(
            path=path,
            width=width,
            height=height,
            fps=fps,
            frame_count=count,
            kind="cinemadng",
            colorspace_hint=colorspace,
            pixel_aspect=aspect.factor,
            pixel_aspect_kind=aspect.kind,
        )
        self._out_w, self._out_h = scaled_dims(width, height, self._scale)

    @property
    def info(self) -> MediaInfo:
        return self._info

    @property
    def output_size(self) -> tuple[int, int]:
        return self._out_w, self._out_h

    def close(self) -> None:
        return

    def __enter__(self) -> CinemaDNGIngestSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def log_header(self, log_fn: Callable[[str], None] | None) -> None:
        if log_fn is None:
            return
        meta = self._header_meta
        camera = " ".join(part for part in (meta.make, meta.model) if part).strip()
        if camera:
            log_fn(f"CinemaDNG camera: {camera}")
        log_fn(f"CinemaDNG source color space: {self._colorspace}")
        if self._colorspace == LINEAR_P3_D60:
            log_fn("DJI exposure: BaselineExposure plus 1.4 stops is already in the pixels")
        label = self.pixel_aspect.label()
        if label:
            log_fn(f"CinemaDNG pixel aspect: {label}")

    def iter_frames(
        self,
        frame_set: set[int] | None = None,
        *,
        cancel_check: ProgressCancel | None = None,
    ) -> Iterator[tuple[int, np.ndarray, dict[str, str]]]:
        total = self._info.frame_count
        indexes = range(1, total + 1)
        if frame_set is not None:
            indexes = [idx for idx in indexes if idx in frame_set]
        do_resize = self._scale < 1.0
        for idx in indexes:
            if cancel_check and cancel_check():
                return
            rgb, attrs = read_sequence_frame(self._path, idx)
            if do_resize:
                rgb = resize_rgb_f32(rgb, self._out_w, self._out_h)
            yield idx, rgb, attrs
