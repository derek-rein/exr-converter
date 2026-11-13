"""Pure rules shared by the Video → Proxy and Transcode tabs."""

from __future__ import annotations

import pytest

from src.gui.batch_rules import (
    LOCATION_BESIDE,
    LOCATION_CUSTOM,
    custom_output_dir,
    video_file_filter,
    watermark_payload,
)


@pytest.mark.parametrize(
    ("location", "text", "expected"),
    [
        (LOCATION_BESIDE, "/out", None),
        (LOCATION_BESIDE, "", None),
        (LOCATION_CUSTOM, "/out", "/out"),
        (LOCATION_CUSTOM, "  /out  ", "/out"),
        (LOCATION_CUSTOM, "", None),
        (LOCATION_CUSTOM, "   ", None),
        ("", "/out", None),
    ],
)
def test_custom_output_dir(location: str, text: str, expected: str | None) -> None:
    assert custom_output_dir(location, text) == expected


def test_location_values_match_saved_settings() -> None:
    # QSettings stores these strings; changing them would lose users' choice.
    assert (LOCATION_BESIDE, LOCATION_CUSTOM) == ("beside", "custom")


@pytest.mark.parametrize(
    ("enabled", "text", "expected"),
    [
        (False, "ID42", ""),
        (False, "", ""),
        (True, "ID42", "ID42"),
        (True, "  ID42 ", "ID42"),
        (True, "", ""),
        (True, "   ", ""),
    ],
)
def test_watermark_payload(enabled: bool, text: str, expected: str) -> None:
    assert watermark_payload(enabled, text) == expected


def test_video_file_filter_sorts_suffixes() -> None:
    assert video_file_filter({".mov", ".mp4", ".avi"}) == (
        "Video (*.avi *.mov *.mp4);;All files (*)"
    )


def test_video_file_filter_without_suffixes() -> None:
    assert video_file_filter([]) == "Video ();;All files (*)"
