"""Output path ``<token>`` variables (:mod:`src.core.path_tokens`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.path_tokens import (
    _REEL_KEYS,
    _pick,
    expand_folder,
    expand_name_pattern,
    reel_from_clip_name,
    substitute_path,
)


@pytest.mark.parametrize(
    ("name", "reel"),
    [
        ("A_0001C001_261022_145741_a12S6.mov", "A_0001"),  # ARRI
        ("A001C003_220101_R1AB.mxf", "A001"),  # Sony / ARRI older
        ("A001_C003_0101XY.R3D", "A001"),  # RED
        ("interview_final.mov", ""),
    ],
)
def test_reel_from_clip_name(name, reel):
    assert reel_from_clip_name(name) == reel


def test_reel_from_metadata_keys():
    assert _pick({"meta:com.apple.quicktime.reel_name": "R12"}, _REEL_KEYS) == "R12"
    assert _pick({"reel_id": "A003", "tape_name": "T1"}, _REEL_KEYS) == "A003"
    assert _pick({"meta:ReelName": " B004 "}, _REEL_KEYS) == "B004"


def test_values_cannot_add_folders_or_bad_chars():
    assert substitute_path("<reel>", {"reel": "a/b:c"}) == "a_b_c"
    with pytest.raises(ValueError, match="Unknown name token <shot>"):
        substitute_path("<shot>", {})


def test_expand_name_pattern_splits_folders():
    folders, stem = expand_name_pattern("<reel>\\<name>/x", {"reel": "A1", "name": "n"})
    assert folders == ["A1", "n"] and stem == "x"
    with pytest.raises(ValueError, match="empty name"):
        expand_name_pattern("<reel>/", {"reel": "A1"})


def test_expand_folder_keeps_literal_text():
    assert expand_folder("/mnt/out", {}) == Path("/mnt/out")
    assert expand_folder("/mnt/./out/<reel>", {"reel": "A1"}) == Path("/mnt/./out/A1")
    assert expand_folder("/mnt/<reel>/day", {"reel": ""}) == Path("/mnt/day")


def test_resolution_x_and_y_aliases():
    v = {"srcwidth": "3840", "srcheight": "2160", "width": "1920", "height": "1080"}
    assert substitute_path("<srcresx>x<srcres_y>", v) == "3840x2160"
    assert substitute_path("<source-resolution-x>_<resolution_y>", v) == "3840_1080"
    assert substitute_path("<resx>/<resy>", v) == "1920/1080"
