"""Read a :class:`ConvertTab` into a :class:`~src.core.convert_job.ConvertJob`.

Lives in ``gui`` so :mod:`core.convert_job` stays free of widgets.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..core.convert_job import ConvertJob, ConvertMode, ExrToVideoJob, VideoToExrJob

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

    from .widgets import ConvertTab


def job_from_convert_tab(
    tab: ConvertTab,
    *,
    mode: ConvertMode,
    config_source: str,
    config_path: str,
    frame_set: set[int] | None,
    slate_frame: np.ndarray | None = None,
    burnin_overlay: np.ndarray | None = None,
    slate_overlay: np.ndarray | None = None,
    overlay_provider: Callable[[int | None], np.ndarray | None] | None = None,
) -> ConvertJob:
    """Collect the active tab's validated fields into a convert job."""
    if mode == "video2exr":
        out_name = tab.get_output_sequence_name()
        pattern_pad = tab.get_output_sequence_padding()
        pad = int(pattern_pad) if pattern_pad is not None else tab.get_padding()
        return VideoToExrJob(
            video_path=tab.get_input_path(),
            output_dir=Path(tab.get_output_path()),
            src_space=tab.src_btn.current_space(),
            dst_space=tab.dst_btn.current_space(),
            config_source=config_source,
            config_path=config_path,
            compression=tab.get_compression(),
            scale=tab.get_scale(),
            padding=pad,
            start_frame=tab.get_start_frame(),
            frame_set=frame_set,
            exr_opts=tab.get_exr_opts() or None,
            output_name=out_name,
        )

    codec_key, codec, pix = tab.get_video_codec_info()
    return ExrToVideoJob(
        input_spec=tab.get_input_path(),
        output_video=Path(tab.get_output_path()),
        src_space=tab.src_btn.current_space(),
        dst_space=tab.dst_btn.current_space(),
        fps=tab.get_fps(),
        config_source=config_source,
        config_path=config_path,
        scale=tab.get_scale(),
        video_codec=codec,
        pix_fmt_out=pix,
        codec_key=codec_key,
        frame_set=frame_set,
        codec_opts=tab.get_codec_opts() or None,
        slate_frame=slate_frame,
        burnin_overlay=burnin_overlay,
        slate_overlay=slate_overlay,
        overlay_provider=overlay_provider,
    )
