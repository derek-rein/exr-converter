"""Unit tests for frame_source factories (no real media required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.frame_source import open_ingest_source
from src.core.r3d import R3DUnavailableError


def test_open_ingest_source_r3d_missing_raises(tmp_path: Path) -> None:
    from src.core.r3d import native as native_mod

    prev = (
        native_mod._init_attempted,
        native_mod._init_ok,
        native_mod._init_error,
        native_mod._lib,
    )
    native_mod._init_attempted = True
    native_mod._init_ok = False
    native_mod._init_error = "test: bridge missing"
    native_mod._lib = None
    try:
        fake = tmp_path / "clip.R3D"
        fake.write_bytes(b"not real")
        with pytest.raises(R3DUnavailableError):
            open_ingest_source(fake, scale=1.0)
    finally:
        (
            native_mod._init_attempted,
            native_mod._init_ok,
            native_mod._init_error,
            native_mod._lib,
        ) = prev


def test_scaled_dims_even() -> None:
    from src.core.frame_source import scaled_dims

    assert scaled_dims(100, 50, 1.0) == (100, 50)
    w, h = scaled_dims(101, 51, 0.5)
    assert w % 2 == 0
    assert h % 2 == 0


def test_video_ingest_reads_sar_and_no_alpha(tmp_path: Path) -> None:
    from src.core.frame_source import VideoIngestSource
    from tests.support.integration import write_synthetic_video

    vid = tmp_path / "sar.mov"
    write_synthetic_video(vid, width=80, height=60, frames=1, sample_aspect=(4, 3))
    src = VideoIngestSource(str(vid))
    try:
        assert abs(src.info.pixel_aspect - 4 / 3) < 1e-3
        assert src.info.has_alpha is False
    finally:
        src.close()


def test_video_ingest_detects_prores_alpha(tmp_path: Path) -> None:
    from src.core.frame_source import VideoIngestSource
    from tests.support.integration import write_synthetic_alpha_video

    vid = tmp_path / "a.mov"
    write_synthetic_alpha_video(vid, frames=1)
    src = VideoIngestSource(str(vid))
    try:
        assert src.info.has_alpha is True
        idx, rgb, _attrs = next(src.iter_frames(None))
        assert idx == 1
        assert rgb.shape[2] == 4
        assert 0.4 < float(rgb[..., 3].mean()) < 0.6
    finally:
        src.close()
