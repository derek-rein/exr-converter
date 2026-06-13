"""Unit tests for Nuke-style frame range parsing/formatting."""

from __future__ import annotations

import pytest

from src.core.framerange import format_frame_range, parse_frame_range


class TestParse:
    def test_simple_range(self):
        assert parse_frame_range("1-10") == list(range(1, 11))

    def test_stepped_range(self):
        assert parse_frame_range("1-10x2") == [1, 3, 5, 7, 9]

    def test_single_frame(self):
        assert parse_frame_range("42") == [42]

    def test_comma_list(self):
        assert parse_frame_range("1,5,9") == [1, 5, 9]

    def test_empty_returns_empty(self):
        assert parse_frame_range("") == []
        assert parse_frame_range("   ") == []

    def test_result_is_sorted(self):
        assert parse_frame_range("10-1") == list(range(1, 11))


class TestFormat:
    def test_empty(self):
        assert format_frame_range([]) == ""

    def test_contiguous_compacts(self):
        assert format_frame_range([1, 2, 3, 4, 5]) == "1-5"

    def test_dedup_and_sort(self):
        assert (
            format_frame_range([5, 1, 3, 1]) == "1-5x2"
            or format_frame_range([5, 1, 3, 1]) == "1,3,5"
        )

    @pytest.mark.parametrize("frames", [[1, 2, 3], [10], [1, 3, 5, 7]])
    def test_roundtrip(self, frames):
        assert parse_frame_range(format_frame_range(frames)) == frames
