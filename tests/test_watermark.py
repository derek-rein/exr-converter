"""Unit tests for :mod:`src.render.watermark` overlay rendering."""

from __future__ import annotations

import numpy as np

from src.render.watermark import render_watermark_overlay


class TestRenderWatermark:
    def test_shape_and_dtype(self, qapp):
        out = render_watermark_overlay(64, 64, {"enabled": True, "text": "WIP"})
        assert out.shape == (64, 64, 4)
        assert out.dtype == np.uint8

    def test_disabled_is_transparent(self, qapp):
        out = render_watermark_overlay(64, 64, {"enabled": False, "text": "WIP"})
        assert out[..., 3].max() == 0

    def test_blank_text_is_transparent(self, qapp):
        out = render_watermark_overlay(64, 64, {"enabled": True, "text": "   "})
        assert out[..., 3].max() == 0

    def test_enabled_with_text_is_visible(self, qapp):
        out = render_watermark_overlay(
            256, 256, {"enabled": True, "text": "FOR REVIEW ONLY", "opacity": 80}
        )
        assert out[..., 3].max() > 0
