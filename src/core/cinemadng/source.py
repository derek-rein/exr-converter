"""Video-to-EXR ingest for a CinemaDNG frame sequence."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import numpy as np

from ..frame_source import MediaInfo, resize_rgb_f32, scaled_dims
from .color import LINEAR_P3_D60
from .input import frame_paths, probe_sequence, read_sequence_frame
from .native import probe_meta

ProgressCancel = Callable[[], bool]


class CinemaDNGPreviewDecoder:
    """Random-access preview of a CinemaDNG sequence. Indices are 1-based."""

    def __init__(self, path: str, *, fps: float = 0.0) -> None:
        self.path = path
        width, height, probed_fps, count, _colorspace = probe_sequence(path)
        self.fps = float(fps) if fps and fps > 0 else probed_fps
        self._width = width
        self._height = height
        self._count = count

    def close(self) -> None:
        return

    @property
    def decode_size(self) -> tuple[int, int]:
        return self._width, self._height

    def get_frame(self, idx_1based: int) -> np.ndarray | None:
        if idx_1based < 1 or idx_1based > self._count:
            return None
        rgb, _attrs = read_sequence_frame(self.path, idx_1based)
        return rgb


class CinemaDNGIngestSource:
    """Sequential CinemaDNG frames for video2exr."""

    def __init__(self, path: str, *, scale: float = 1.0) -> None:
        self._path = path
        self._scale = float(scale)
        width, height, fps, count, colorspace = probe_sequence(path)
        self._colorspace = colorspace
        self._info = MediaInfo(
            path=path,
            width=width,
            height=height,
            fps=fps,
            frame_count=count,
            kind="cinemadng",
            colorspace_hint=colorspace,
        )
        self._out_w, self._out_h = scaled_dims(width, height, self._scale)
        self._header_meta = probe_meta(frame_paths(path)[0])

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
