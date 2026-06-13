"""Unit tests for :mod:`src.render.burnin` (field derivation + overlay rendering)."""

from __future__ import annotations

import numpy as np

from src.render.burnin import burnin_fields_from_slate, render_burnin_overlay


class TestFieldsFromSlate:
    def test_maps_expected_cells(self):
        slate = {
            "vendor": "ACME",
            "show": "MOVIE",
            "sequence": "SQ01",
            "shot": "010",
            "frameRange": "1-100",
        }
        fields = burnin_fields_from_slate(slate, input_path="/x/plate.0001.exr")
        assert fields["top_left"] == "ACME"
        assert fields["top_center"] == "MOVIE"
        assert fields["bottom_right"] == "1-100"
        assert set(fields) == {
            "top_left",
            "top_center",
            "top_right",
            "bottom_left",
            "bottom_center",
            "bottom_right",
        }

    def test_top_right_is_iso_date(self):
        import datetime

        fields = burnin_fields_from_slate({"show": "M"})
        assert fields["top_right"] == datetime.date.today().isoformat()

    def test_handles_missing_keys(self):
        fields = burnin_fields_from_slate({})
        assert all(k in fields for k in ("top_left", "bottom_right"))


class TestRenderOverlay:
    def test_shape_and_dtype(self, qapp):
        out = render_burnin_overlay(64, 32, {"top_left": "HI"})
        assert out.shape == (32, 64, 4)
        assert out.dtype == np.uint8

    def test_empty_fields_fully_transparent(self, qapp):
        out = render_burnin_overlay(64, 32, {})
        assert out[..., 3].max() == 0

    def test_text_produces_opaque_pixels(self, qapp):
        out = render_burnin_overlay(256, 128, {"top_left": "RENDER"})
        assert out[..., 3].max() > 0
