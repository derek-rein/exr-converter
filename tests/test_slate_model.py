"""Unit tests for :class:`src.services.slate_model.SlateModel`.

Covers the master-flag setters, change-signal dedup, the unified watermark
``enabled`` mirror (tab + editor share one flag), and metadata round-trips.
"""

from __future__ import annotations

import pytest

from src.services.slate_model import SlateModel


@pytest.fixture
def model(settings) -> SlateModel:
    return SlateModel(settings, mode="exr2video")


def _collect(model: SlateModel) -> list[str]:
    sections: list[str] = []
    model.changed.connect(sections.append)
    return sections


class TestFlags:
    def test_defaults_off(self, model):
        assert model.slate_enabled is False
        assert model.burnin_enabled is False
        assert model.watermark_enabled is False

    def test_set_slate_enabled_emits(self, model):
        sections = _collect(model)
        model.set_slate_enabled(True)
        assert model.slate_enabled is True
        assert sections == ["slate_enabled"]

    def test_set_flag_dedups_same_value(self, model):
        model.set_burnin_enabled(True)
        sections = _collect(model)
        model.set_burnin_enabled(True)  # no change
        assert sections == []

    def test_flag_persists_to_settings(self, settings):
        m = SlateModel(settings, mode="exr2video")
        m.set_slate_enabled(True)
        # A fresh model over the same settings sees the saved value.
        m2 = SlateModel(settings, mode="exr2video")
        assert m2.slate_enabled is True


class TestWatermarkUnifiedFlag:
    def test_watermark_active_tracks_enabled(self, model):
        assert model.watermark_active() is False
        model.set_watermark_enabled(True)
        assert model.watermark_active() is True

    def test_params_enabled_mirrors_master_flag(self, model):
        model.set_watermark_enabled(True)
        assert model.watermark_params["enabled"] is True
        model.set_watermark_enabled(False)
        assert model.watermark_params["enabled"] is False

    def test_params_enabled_mirrors_even_if_params_stale(self, model):
        # Writing params with enabled=False must not override the master flag.
        model.set_watermark_enabled(True)
        model.set_watermark_params({"enabled": False, "text": "DRAFT"})
        assert model.watermark_params["enabled"] is True
        assert model.watermark_params["text"] == "DRAFT"


class TestWatermarkParams:
    def test_param_roundtrip(self, model):
        model.set_watermark_params(
            {"text": "FOR REVIEW", "opacity": 50, "size_pct": 12.0, "angle": 45.0}
        )
        p = model.watermark_params
        assert p["text"] == "FOR REVIEW"
        assert p["opacity"] == 50
        assert p["size_pct"] == 12.0
        assert p["angle"] == 45.0

    def test_param_change_emits(self, model):
        sections = _collect(model)
        model.set_watermark_params({"text": "X"})
        assert "watermark_params" in sections


class TestBurnin:
    def test_roundtrip(self, model):
        fields = {"top_left": "ACME", "bottom_right": "1-100"}
        model.set_burnin_fields(fields)
        out = model.burnin_fields
        assert out["top_left"] == "ACME"
        assert out["bottom_right"] == "1-100"
        # Unset cells normalise to "".
        assert out["top_center"] == ""

    def test_dedup(self, model):
        model.set_burnin_fields({"top_left": "A"})
        sections = _collect(model)
        model.set_burnin_fields({"top_left": "A"})
        assert sections == []


class TestSlateMetadata:
    def test_set_fields_and_version(self, model):
        model.set_slate_fields({"show": "MOVIE", "shot": "010"}, version=7)
        f = model.slate_fields
        assert f["show"] == "MOVIE"
        assert f["shot"] == "010"
        assert model.slate_version == 7

    def test_render_shape_has_expected_keys(self, model):
        model.set_slate_fields({"show": "MOVIE"}, version=3)
        d = model.slate_data_for_render()
        for key in ("show", "version", "resolution", "frameRange", "date"):
            assert key in d
        assert d["version"] == "v0003"

    def test_dedup_on_identical_fields(self, model):
        model.set_slate_fields({"show": "MOVIE"})
        sections = _collect(model)
        model.set_slate_fields({"show": "MOVIE"})
        assert sections == []
