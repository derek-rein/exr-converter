"""Baked Quick Look view LUT matches the player's default OCIO display view."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import PyOpenColorIO as OCIO

from src.core.ocio_utils import (
    default_display_view,
    get_bundled_aces_studio_path,
    get_compositing_space,
    get_working_space,
    make_cpu_processor,
    make_display_processor,
)
from src.core.quicklook_lut import (
    LUT_CUBE_SIZE,
    LUT_MAX_EXP,
    LUT_MIN_EXP,
    bake_input_table,
    bake_view_lut,
    bake_view_lut_bundle,
    parse_view_lut,
    parse_view_lut_bundle,
    sample_view_lut,
    shaper,
    write_view_lut,
)
from src.core.quicklook_prefs import schedule_quicklook_view_lut

ROOT = Path(__file__).resolve().parents[1]


def _apply(processor: OCIO.CPUProcessor, rgb: list[float]) -> list[float]:
    image = np.array([rgb], dtype=np.float32)
    processor.applyRGB(image)
    return [float(image[0, 0]), float(image[0, 1]), float(image[0, 2])]


def test_shaper_endpoints() -> None:
    assert shaper(0.0) == 0.0
    assert shaper(-1.0) == 0.0
    assert shaper(float("nan")) == 0.0
    assert shaper(2.0**LUT_MIN_EXP) == 0.0
    assert abs(shaper(2.0**LUT_MAX_EXP) - 1.0) < 1e-5
    assert 0.0 < shaper(0.18) < 1.0


def test_baked_cube_matches_player_default_view() -> None:
    path = get_bundled_aces_studio_path()
    assert path is not None
    config = OCIO.Config.CreateFromFile(str(path))
    blob = bake_view_lut(config)
    lut = parse_view_lut(blob)
    assert lut is not None
    assert lut.cube_size == LUT_CUBE_SIZE
    assert lut.min_exp == LUT_MIN_EXP
    assert lut.max_exp == LUT_MAX_EXP
    assert lut.source == "ACEScg"
    assert lut.display == "sRGB - Display"
    assert lut.view == "ACES 2.0 - SDR 100 nits (Rec.709)"
    assert blob[:4] == b"EXQL"
    assert len(blob) < 2_000_000

    source = get_working_space(config)
    display, view = default_display_view(config, color_space=source)
    direct = make_display_processor(config, source, display, view, exposure=0.0, gamma=1.0)
    working = get_compositing_space(config)
    to_working = make_cpu_processor(config, source, working)
    player = make_display_processor(config, working, display, view, exposure=0.0, gamma=1.0)
    samples = (
        [0.0, 0.0, 0.0],
        [0.18, 0.18, 0.18],
        [1.0, 1.0, 1.0],
        [0.18, 0.05, 0.02],
        [4.0, 1.0, 0.2],
    )
    for rgb in samples:
        baked = sample_view_lut(lut, (rgb[0], rgb[1], rgb[2]))
        expected = _apply(direct, rgb)
        played = _apply(player, _apply(to_working, rgb))
        for channel in range(3):
            assert abs(baked[channel] - expected[channel]) < 0.02
            assert abs(expected[channel] - played[channel]) < 1e-4
    assert parse_view_lut(b"nope") is None
    assert parse_view_lut(blob[:-4]) is None


def test_write_view_lut_round_trip(tmp_path: Path) -> None:
    path = get_bundled_aces_studio_path()
    assert path is not None
    config = OCIO.Config.CreateFromFile(str(path))
    dest = tmp_path / "group" / "view.lut"
    written = write_view_lut(bake_view_lut(config, cube_size=5), [dest])
    assert written == [dest]
    parsed = parse_view_lut(dest.read_bytes())
    assert parsed is not None
    assert parsed.cube_size == 5
    schedule_quicklook_view_lut(config)


def test_swift_shaper_constants_match() -> None:
    text = (ROOT / "packaging/macos/quicklook/Sources/EXRQLCore/ViewLUT.swift").read_text(
        encoding="utf-8"
    )
    assert "view.lut" in text
    assert "log2" in text
    assert 'Data("EXQL".utf8)' in text


def test_notarization_probe_is_dispatch_only() -> None:
    text = (ROOT / ".github/workflows/quicklook-notarize.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "pull_request:" in text
    assert "paths:" in text
    assert "\n  push:" not in text
    assert "xcrun notarytool submit" in text
    assert "appex.entitlements" in text
    assert "gh release create" not in text
    assert "build_quicklook_appex.py" in text


def test_view_bundle_has_p3_and_srgb_views() -> None:
    path = get_bundled_aces_studio_path()
    assert path is not None
    config = OCIO.Config.CreateFromFile(str(path))
    bundle = parse_view_lut_bundle(bake_view_lut_bundle(config, cube_size=5))
    assert bundle is not None
    pairs = [(e.cg_space, e.lut.display, e.lut.view) for e in bundle.entries]
    assert bundle.p3_default is not None and bundle.srgb_default is not None
    assert pairs[bundle.p3_default] == (
        "displayP3",
        "Display P3 - Display",
        "ACES 2.0 - SDR 100 nits (P3 D65)",
    )
    assert pairs[bundle.srgb_default] == (
        "sRGB",
        "sRGB - Display",
        "ACES 2.0 - SDR 100 nits (Rec.709)",
    )
    assert ("sRGB", "sRGB - Display", "Video (colorimetric)") in pairs
    assert all("HDR" not in view for _tag, _display, view in pairs)
    assert parse_view_lut_bundle(b"nope") is None


def test_input_table_matches_ocio() -> None:
    path = get_bundled_aces_studio_path()
    assert path is not None
    config = OCIO.Config.CreateFromFile(str(path))
    table = json.loads(bake_input_table(config))
    assert table["target"] == "ACEScg"
    spaces = {entry["name"]: entry for entry in table["spaces"]}
    assert "curve" not in spaces["ACEScg"]
    assert "curve" in spaces["ARRI LogC4"]
    assert "lin_rec709" in spaces["Linear Rec.709 (sRGB)"]["aliases"]

    codes = np.linspace(table["curve_min"], table["curve_max"], len(spaces["ARRI LogC4"]["curve"]))
    for name, rgb in (
        ("ACES2065-1", [0.18, 0.05, 0.4]),
        ("Linear Rec.709 (sRGB)", [0.5, 0.2, 0.02]),
        ("ARRI LogC4", [0.45, 0.30, 0.55]),
        ("sRGB Encoded Rec.709 (sRGB)", [0.7, 0.4, 0.1]),
    ):
        entry = spaces[name]
        values = np.array(rgb, dtype=np.float64)
        if "curve" in entry:
            values = np.interp(values, codes, np.array(entry["curve"]))
        matrix = np.array(entry["matrix"]).reshape(3, 3)
        predicted = matrix @ values
        expected = _apply(make_cpu_processor(config, name, "ACEScg"), rgb)
        for channel in range(3):
            assert abs(predicted[channel] - expected[channel]) <= 0.01 * max(
                abs(expected[channel]), 0.05
            )


def test_swift_reads_the_bundle_and_inputs() -> None:
    core = ROOT / "packaging/macos/quicklook/Sources/EXRQLCore"
    assert 'Data("EXQB".utf8)' in (core / "ViewLUT.swift").read_text(encoding="utf-8")
    assert '"views.lut"' in (core / "ViewLUT.swift").read_text(encoding="utf-8")
    assert '"inputs.json"' in (core / "InputSpaces.swift").read_text(encoding="utf-8")
    assert "exrconverter:dstColorSpace" in (core / "InputSpaces.swift").read_text(encoding="utf-8")
