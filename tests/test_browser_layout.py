"""Pure layout rules shared by the sequence and video browser dialogs."""

from __future__ import annotations

import pytest

from src.gui.browser_layout import (
    content_split_ok,
    frame_range_text,
    in_out_status_text,
    initial_browse_mode,
    metadata_split,
    outer_split_ok,
)


@pytest.mark.parametrize(
    ("sizes", "ok"),
    [
        (None, False),
        ([], False),
        ([360], False),
        ([360, 1000], True),
        ([1, 1], True),
        ([0, 1000], False),
        ([360, 0], False),
        ([-5, 1000], False),
        ([100, 200, 300], False),
    ],
)
def test_outer_split_ok(sizes: list[int] | None, ok: bool) -> None:
    assert outer_split_ok(sizes) is ok


@pytest.mark.parametrize(
    ("sizes", "ok"),
    [
        (None, False),
        ([800], False),
        ([800, 260], True),
        ([800, 0], True),
        ([0, 260], False),
        ([800, -1], False),
        ([800, 260, 10], False),
    ],
)
def test_content_split_ok(sizes: list[int] | None, ok: bool) -> None:
    assert content_split_ok(sizes) is ok


class TestMetadataSplit:
    def test_default_width_without_saved_sizes(self) -> None:
        assert metadata_split(1000, None, content_min=400) == [740, 260]

    def test_saved_width_is_reused(self) -> None:
        assert metadata_split(1000, [700, 300], content_min=400) == [700, 300]

    def test_saved_width_below_floor_is_ignored(self) -> None:
        assert metadata_split(1000, [881, 119], content_min=400) == [740, 260]
        assert metadata_split(1000, [880, 120], content_min=400) == [880, 120]

    def test_malformed_saved_sizes_are_ignored(self) -> None:
        assert metadata_split(1000, [300], content_min=400) == [740, 260]

    def test_content_floor_wins_over_metadata_width(self) -> None:
        # Sequence browser keeps 400 px for content, video browser 200 px.
        assert metadata_split(500, None, content_min=400) == [400, 260]
        assert metadata_split(300, None, content_min=200) == [200, 260]


class TestInOutText:
    def test_frame_range_text(self) -> None:
        assert frame_range_text((1003, 1007)) == "1003-1007"
        assert frame_range_text(None) == ""

    def test_in_out_status_text(self) -> None:
        assert in_out_status_text((1003, 1007)) == (
            "In 1003 · Out 1007 (5 frames) — Open uses this range"
        )
        assert in_out_status_text((1, 1)) == ("In 1 · Out 1 (1 frames) — Open uses this range")


class TestInitialBrowseMode:
    def test_constants_match_browser_state(self) -> None:
        from src.gui import browser_layout, browser_state

        assert browser_layout.VIEW_LIST == browser_state.VIEW_LIST
        assert browser_layout.VIEW_GRID == browser_state.VIEW_GRID

    @pytest.mark.parametrize(
        ("saved_view", "saved_last_browse", "expected"),
        [
            ("list", "grid", "list"),
            ("grid", "list", "grid"),
            # Saved Preview: back to the browse mode used before it.
            ("preview", "grid", "grid"),
            ("preview", "list", "list"),
        ],
    )
    def test_initial_browse_mode(
        self, saved_view: str, saved_last_browse: str, expected: str
    ) -> None:
        assert initial_browse_mode(saved_view, saved_last_browse) == expected
