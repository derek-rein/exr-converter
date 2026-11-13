"""Short facts shown on the source card (pure)."""

from __future__ import annotations

from src.core.source_summary import format_duration, format_fps, sequence_details, video_details


def test_video_details_in_reading_order() -> None:
    assert video_details(1920, 1080, 240, 24.0) == [
        "1920 × 1080",
        "240 frames",
        "24 fps",
        "0:10",
    ]


def test_video_details_skip_what_is_unknown() -> None:
    assert video_details(0, 0, 0, 0.0) == []
    assert video_details(3840, 2160, 0, 0.0) == ["3840 × 2160"]
    assert video_details(0, 0, 1, 25.0) == ["1 frame", "25 fps"]


def test_fractional_rates_keep_their_decimals() -> None:
    assert format_fps(23.976) == "23.976 fps"
    assert format_fps(29.97) == "29.97 fps"
    assert format_fps(24.0) == "24 fps"
    assert format_fps(59.94006) == "59.94 fps"


def test_duration_formats() -> None:
    assert format_duration(0) == ""
    assert format_duration(4.2) == "0:04"
    assert format_duration(65) == "1:05"
    assert format_duration(3725) == "1:02:05"


def test_sequence_details() -> None:
    assert sequence_details(12, 1001, 1012, ".exr") == ["EXR", "12 frames", "1001–1012"]
    assert sequence_details(1, 7, 7, ".DPX") == ["DPX", "1 frame", "frame 7"]


def test_sequence_with_gaps_says_so() -> None:
    assert sequence_details(8, 1001, 1012, ".exr") == [
        "EXR",
        "8 frames",
        "1001–1012",
        "4 missing",
    ]


def test_empty_sequence() -> None:
    assert sequence_details(0, 0, 0, ".exr") == ["EXR"]
