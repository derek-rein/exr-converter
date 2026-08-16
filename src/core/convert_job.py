"""Typed convert jobs shared by the CLI and the GUI.

The pixel pipeline stays in :mod:`convert`. This module is the job spec both
front-ends fill.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from .constants import (
    DEFAULT_EXR_COMPRESSION,
    DEFAULT_FRAME_PADDING,
    DEFAULT_START_FRAME,
)
from .framerange import parse_frame_range

ConvertMode = Literal["video2exr", "exr2video"]

ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]


@dataclass
class VideoToExrJob:
    """Video / R3D ingest → EXR sequence (no slate / overlays)."""

    video_path: str
    output_dir: Path
    src_space: str
    dst_space: str
    config_source: str = ""
    config_path: str = ""
    compression: str = DEFAULT_EXR_COMPRESSION
    scale: float = 1.0
    padding: int = DEFAULT_FRAME_PADDING
    start_frame: int = DEFAULT_START_FRAME
    frame_set: set[int] | None = None
    exr_opts: dict[str, str] | None = None
    deinterlace: str = "auto"
    output_name: str = ""
    workers: int = 0

    @property
    def mode(self) -> ConvertMode:
        return "video2exr"

    def run(
        self,
        *,
        ocio_cfg: object | None = None,
        progress: ProgressCallback | None = None,
        log: LogCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        from .convert import run_video_to_exr

        run_video_to_exr(
            self.video_path,
            Path(self.output_dir),
            ocio_cfg,
            self.src_space,
            self.dst_space,
            progress=progress,
            cancel_check=cancel_check,
            log=log,
            compression=self.compression,
            workers=self.workers,
            config_source=self.config_source,
            config_path=self.config_path,
            scale=self.scale,
            padding=self.padding,
            start_frame=self.start_frame,
            frame_set=self.frame_set,
            exr_opts=self.exr_opts,
            deinterlace=self.deinterlace,
            output_name=self.output_name,
        )


@dataclass
class ExrToVideoJob:
    """Image sequence → video (optional slate / burn-in / watermark arrays)."""

    input_spec: str
    output_video: Path
    src_space: str
    dst_space: str
    fps: float
    config_source: str = ""
    config_path: str = ""
    scale: float = 1.0
    video_codec: str = "libx264"
    pix_fmt_out: str = "yuv420p"
    codec_key: str = "h264"
    frame_set: set[int] | None = None
    codec_opts: dict[str, str] | None = None
    workers: int = 0
    slate_frame: np.ndarray | None = None
    burnin_overlay: np.ndarray | None = None
    slate_overlay: np.ndarray | None = None
    overlay_provider: Callable[[int | None], np.ndarray | None] | None = None

    @property
    def mode(self) -> ConvertMode:
        return "exr2video"

    def run(
        self,
        *,
        ocio_cfg: object | None = None,
        progress: ProgressCallback | None = None,
        log: LogCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        from .convert import run_exr_to_video

        run_exr_to_video(
            self.input_spec,
            Path(self.output_video),
            ocio_cfg,
            self.src_space,
            self.dst_space,
            self.fps,
            progress=progress,
            cancel_check=cancel_check,
            log=log,
            video_codec=self.video_codec,
            pix_fmt_out=self.pix_fmt_out,
            workers=self.workers,
            config_source=self.config_source,
            config_path=self.config_path,
            scale=self.scale,
            codec_key=self.codec_key,
            frame_set=self.frame_set,
            slate_frame=self.slate_frame,
            burnin_overlay=self.burnin_overlay,
            slate_overlay=self.slate_overlay,
            overlay_provider=self.overlay_provider,
            codec_opts=self.codec_opts,
        )


ConvertJob = VideoToExrJob | ExrToVideoJob


def parse_optional_frame_range(spec: str) -> set[int] | None:
    """Parse a Nuke-style range. Empty → ``None`` (all frames). Invalid → ValueError."""
    text = (spec or "").strip()
    if not text:
        return None
    try:
        frames = parse_frame_range(text)
    except Exception as exc:
        raise ValueError(f"invalid frame range: {text}") from exc
    return set(frames) if frames else None
