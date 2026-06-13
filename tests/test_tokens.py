"""Unit tests for :mod:`src.render.tokens` (burn-in / watermark variables)."""

from __future__ import annotations

import datetime

from src.render.tokens import (
    any_per_frame_token,
    build_values,
    has_per_frame_token,
    substitute,
)

_SLATE = {
    "show": "PROJ",
    "sequence": "sq010",
    "shot": "0040",
    "version": "v0003",
    "take": "02",
    "artist": "jdoe",
    "vendor": "Studio X",
    "scope": "comp",
    "shotTypes": "2d comp",
    "submitFor": "WIP",
    "fps": "24",
    "resolution": "1920x1080",
    "frameRange": "1001-1100",
}


def test_substitute_basic_and_case_insensitive():
    values = build_values(_SLATE)
    assert substitute("<shot>", values) == "0040"
    assert substitute("<SHOT>", values) == "0040"
    assert substitute("<Shot>", values) == "0040"


def test_substitute_aliases():
    values = build_values(_SLATE)
    assert substitute("<seq>", values) == "sq010"
    assert substitute("<res>", values) == "1920x1080"
    assert substitute("<range>", values) == "1001-1100"


def test_substitute_compound_template():
    values = build_values(_SLATE, frame=1017, frame_pad=4)
    assert substitute("<shot> <version> - <frame>", values) == "0040 v0003 - 1017"


def test_unknown_token_left_literal():
    values = build_values(_SLATE)
    assert substitute("<notarealtoken>", values) == "<notarealtoken>"


def test_empty_value_collapses():
    values = build_values({})  # no metadata
    assert substitute("[<shot>]", values) == "[]"


def test_has_per_frame_token():
    assert has_per_frame_token("frame <frame>") is True
    assert has_per_frame_token("<f>") is True  # alias
    assert has_per_frame_token("<shot> <version>") is False
    assert has_per_frame_token("") is False
    assert has_per_frame_token(None) is False


def test_any_per_frame_token_collections():
    assert any_per_frame_token({"top_left": "<shot>", "bottom_right": "<frame>"}) is True
    assert any_per_frame_token({"top_left": "<shot>"}) is False
    assert any_per_frame_token(["<show>", "<f>"]) is True
    assert any_per_frame_token("plain text") is False
    assert any_per_frame_token(None) is False


def test_build_values_frame_padding():
    values = build_values(_SLATE, frame=42, frame_pad=5, start_frame=1, end_frame=100)
    assert values["frame"] == "00042"
    assert values["startframe"] == "00001"
    assert values["endframe"] == "00100"


def test_build_values_overrides_and_defaults():
    values = build_values(_SLATE, resolution="3840x2160", frame_range="5-9")
    assert values["resolution"] == "3840x2160"
    assert values["framerange"] == "5-9"
    # date defaults to today when slate omits it
    assert values["date"] == datetime.date.today().isoformat()


def test_build_values_omits_frame_when_not_supplied():
    values = build_values(_SLATE)
    assert "frame" not in values
    # <frame> stays literal because it has no value yet
    assert substitute("<frame>", values) == "<frame>"


def test_shottypes_and_submitfor_mapping():
    values = build_values(_SLATE)
    assert substitute("<shottypes>", values) == "2d comp"
    assert substitute("<submitfor>", values) == "WIP"
    assert substitute("<status>", values) == "WIP"  # alias
