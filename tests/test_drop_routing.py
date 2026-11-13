"""Which tab a dropped path belongs to (pure; no Qt, no filesystem)."""

from __future__ import annotations

import pytest

from src.core.drop_routing import DropTarget, drop_target
from src.core.fs_skip import VIDEO_EXTS


@pytest.mark.parametrize(
    "name", ["clip.mp4", "A001.MOV", "tape.mts", "cam.m2ts", "old.mpg", "x.dv"]
)
def test_video_files_go_to_video_to_exr(name: str) -> None:
    assert drop_target(name, is_dir=False) is DropTarget.VIDEO


def test_every_core_video_extension_is_accepted() -> None:
    refused = sorted(e for e in VIDEO_EXTS if drop_target(f"clip{e}", is_dir=False) is None)
    assert refused == []


@pytest.mark.parametrize("name", ["shot.1001.exr", "plate.0001.DPX", "still.png", "f.0001.dng"])
def test_image_frames_go_to_exr_to_video(name: str) -> None:
    assert drop_target(name, is_dir=False) is DropTarget.SEQUENCE


def test_a_folder_is_a_sequence_folder() -> None:
    assert drop_target("comp", is_dir=True) is DropTarget.SEQUENCE


@pytest.mark.parametrize("name", ["notes.txt", "._clip.mov", ".DS_Store", "clip.mov.partial"])
def test_other_files_are_refused(name: str) -> None:
    assert drop_target(name, is_dir=False) is None


def test_plugin_suffixes_extend_the_video_list() -> None:
    assert drop_target("a.ari", is_dir=False) is None or ".ari" in VIDEO_EXTS
    assert drop_target("a.xyz", is_dir=False, extra_video_exts={".xyz"}) is DropTarget.VIDEO
