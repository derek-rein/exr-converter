"""Unit tests for pure helpers in :mod:`src.core.convert`."""

from __future__ import annotations

import pytest

from src.core.convert import _frame_num_from_path, _scaled_dims


class TestScaledDims:
    def test_full_scale_is_passthrough(self):
        assert _scaled_dims(1920, 1080, 1.0) == (1920, 1080)

    def test_scale_above_one_is_passthrough(self):
        # Upscaling isn't supported — dims are returned unchanged.
        assert _scaled_dims(1920, 1080, 2.0) == (1920, 1080)

    def test_half_scale(self):
        assert _scaled_dims(1920, 1080, 0.5) == (960, 540)

    def test_dims_forced_even(self):
        # 1919 * 0.5 = 959.5 -> round 960 (already even); use odd source.
        w, h = _scaled_dims(1001, 1001, 0.5)
        assert w % 2 == 0 and h % 2 == 0

    def test_minimum_two_pixels(self):
        w, h = _scaled_dims(2, 2, 0.01)
        assert w >= 2 and h >= 2
        assert w % 2 == 0 and h % 2 == 0


class TestFrameNumFromPath:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/tmp/beauty.0001.exr", 1),
            ("/tmp/beauty.1001.exr", 1001),
            ("render_00042.exr", 42),
            ("shot.v002.0100.exr", 100),
            ("/a/b/c/plate.999999.exr", 999999),
        ],
    )
    def test_extracts_trailing_number(self, path, expected):
        assert _frame_num_from_path(path) == expected

    def test_no_number_returns_none(self):
        assert _frame_num_from_path("/tmp/slate.exr") is None

    def test_leading_zeros_preserved_as_int(self):
        assert _frame_num_from_path("x.0007.exr") == 7
