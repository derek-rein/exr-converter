"""Decide which convert tab a dropped path belongs to.

Pure: takes a file name and whether it is a folder, touches nothing on disk.
The GUI drag-enter and drop handlers both ask here, so what the window
accepts on hover is exactly what it handles on drop.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from pathlib import PurePath

from .constants import is_image_sequence_ext
from .fs_skip import VIDEO_EXTS
from .video import is_ignored_media_filename


class DropTarget(Enum):
    VIDEO = "video"  # Video → EXR input
    SEQUENCE = "sequence"  # EXR → Video input (a frame, or a folder of frames)


def drop_target(
    name: str, *, is_dir: bool, extra_video_exts: Iterable[str] = ()
) -> DropTarget | None:
    """Tab for a dropped *name*, or ``None`` when the window should refuse it.

    *extra_video_exts* adds decoder-plugin suffixes to the built-in video list.
    """
    if is_dir:
        return DropTarget.SEQUENCE
    if is_ignored_media_filename(name):
        return None
    suffix = PurePath(name).suffix.lower()
    if suffix in VIDEO_EXTS or suffix in {e.lower() for e in extra_video_exts}:
        return DropTarget.VIDEO
    if is_image_sequence_ext(suffix):
        return DropTarget.SEQUENCE
    return None
