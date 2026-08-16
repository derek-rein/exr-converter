"""Shared convert job spec and frame-range helper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.core.convert_job import (
    ExrToVideoJob,
    VideoToExrJob,
    parse_optional_frame_range,
)


def test_parse_optional_frame_range_empty() -> None:
    assert parse_optional_frame_range("") is None
    assert parse_optional_frame_range("   ") is None


def test_parse_optional_frame_range_nuke() -> None:
    got = parse_optional_frame_range("1-4,8")
    assert got == {1, 2, 3, 4, 8}


def test_parse_optional_frame_range_invalid() -> None:
    with pytest.raises(ValueError):
        parse_optional_frame_range("not-a-range")


def test_v2e_job_run_forwards_fields(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def _fake_v2e(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

    monkeypatch.setattr("src.core.convert.run_video_to_exr", _fake_v2e)
    job = VideoToExrJob(
        video_path="/in/clip.mov",
        output_dir=tmp_path / "out",
        src_space="sRGB",
        dst_space="ACEScg",
        compression="zip",
        padding=5,
        start_frame=1001,
        output_name="plate",
    )
    job.run(ocio_cfg="cfg", log=print)
    assert seen["args"][0] == "/in/clip.mov"
    assert seen["args"][2] == "cfg"
    assert seen["kwargs"]["compression"] == "zip"
    assert seen["kwargs"]["output_name"] == "plate"


def test_e2v_job_run_forwards_codec(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def _fake_e2v(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

    monkeypatch.setattr("src.core.convert.run_exr_to_video", _fake_e2v)
    job = ExrToVideoJob(
        input_spec="/shot/beauty.####.exr",
        output_video=tmp_path / "out.mov",
        src_space="ACEScg",
        dst_space="Output - Rec.709",
        fps=24.0,
        codec_key="prores",
        video_codec="prores_ks",
        pix_fmt_out="yuv422p10le",
    )
    job.run()
    assert seen["args"][0] == "/shot/beauty.####.exr"
    assert seen["kwargs"]["codec_key"] == "prores"
    assert seen["kwargs"]["video_codec"] == "prores_ks"
