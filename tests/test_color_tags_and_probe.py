"""Destination colour tags (H.273) and EXR source colour space inference."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import OpenImageIO as oiio
import pytest

from src.cli import build_parser, resolve_e2v_spaces
from src.core.color_tags import ColorTags, color_tags_for_destination, tags_from_interop_id
from src.core.ocio_utils import resolve_ocio_for_cli
from src.core.sequence import (
    colorspace_from_chromaticities,
    probe_pixel_colorspace,
    probe_pixel_colorspace_detail,
)

REC709 = (0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290)
AP1 = (0.713, 0.293, 0.165, 0.830, 0.128, 0.044, 0.32168, 0.33767)
AP0 = (0.7347, 0.2653, 0.0, 1.0, 0.0001, -0.0770, 0.32168, 0.33767)


def _write_exr(path: Path, *, chromaticities=None, attrs: dict[str, str] | None = None) -> Path:
    spec = oiio.ImageSpec(16, 8, 3, oiio.HALF)
    if chromaticities is not None:
        spec.attribute("chromaticities", oiio.TypeDesc("float[8]"), tuple(chromaticities))
    for key, val in (attrs or {}).items():
        spec.attribute(key, val)
    out = oiio.ImageOutput.create(str(path))
    assert out is not None
    out.open(str(path), spec)
    out.write_image(np.full((8, 16, 3), 0.18, dtype=np.float32))
    out.close()
    return path


class TestColorTags:
    @pytest.mark.parametrize(
        ("interop", "expected"),
        [
            ("g24_rec709_display", ColorTags(1, 1)),
            ("srgb_rec709_display", ColorTags(1, 13)),
            ("lin_rec709_scene", ColorTags(1, 8)),
            ("pq_rec2020_display", ColorTags(9, 16)),
            ("hlg_rec2020_display", ColorTags(9, 18)),
            ("srgb_p3d65_display", ColorTags(12, 13)),
            ("ocio:acescct_ap1_scene", ColorTags()),
            ("lin_ap1_scene", ColorTags()),
            ("lin_ap0_scene", ColorTags()),
            ("", ColorTags()),
        ],
    )
    def test_interop_ids(self, interop, expected):
        assert tags_from_interop_id(interop) == expected

    @pytest.mark.parametrize(
        ("dst", "expected"),
        [
            ("ACEScct", ColorTags(2, 2, 1)),
            ("ACEScg", ColorTags(2, 2, 1)),
            ("Rec.1886 Rec.709 - Display", ColorTags(1, 1, 1)),
            ("sRGB - Display", ColorTags(1, 13, 1)),
            ("Linear Rec.709 (sRGB)", ColorTags(1, 8, 1)),
        ],
    )
    def test_bundled_config_destinations(self, dst, expected):
        cfg = resolve_ocio_for_cli(None)
        assert color_tags_for_destination(cfg, "ACES2065-1", dst) == expected

    def test_no_config_is_unspecified(self):
        assert color_tags_for_destination(None, "", "ACEScct").is_unspecified


class TestChromaticities:
    def test_known_primaries(self):
        assert colorspace_from_chromaticities(REC709) == "Linear Rec.709 (sRGB)"
        assert colorspace_from_chromaticities(AP1) == "ACEScg"
        assert colorspace_from_chromaticities(AP0) == "ACES2065-1"

    def test_unknown_or_bad(self):
        assert colorspace_from_chromaticities((0.5,) * 8) == ""
        assert colorspace_from_chromaticities((0.64, 0.33)) == ""
        assert colorspace_from_chromaticities(None) == ""

    def test_probe_reads_exr_chromaticities(self, tmp_path: Path):
        p709 = _write_exr(tmp_path / "a.0001.exr", chromaticities=REC709)
        pap1 = _write_exr(tmp_path / "b.0001.exr", chromaticities=AP1)
        assert probe_pixel_colorspace_detail(str(p709)) == (
            "Linear Rec.709 (sRGB)",
            "chromaticities",
        )
        assert probe_pixel_colorspace(str(pap1)) == "ACEScg"

    def test_interop_id_beats_chromaticities(self, tmp_path: Path):
        p = _write_exr(
            tmp_path / "c.0001.exr",
            chromaticities=AP1,
            attrs={"colorInteropID": "lin_rec709_scene"},
        )
        assert probe_pixel_colorspace_detail(str(p)) == ("lin_rec709_scene", "colorInteropID")

    def test_our_tag_wins(self, tmp_path: Path):
        p = _write_exr(
            tmp_path / "d.0001.exr",
            chromaticities=REC709,
            attrs={"exrconverter:dstColorSpace": "ACEScg"},
        )
        assert probe_pixel_colorspace(str(p)) == "ACEScg"


class TestCliSourceDefault:
    def _resolve(self, path: Path) -> tuple[str, list[str]]:
        cfg = resolve_ocio_for_cli(None)
        args = build_parser().parse_args(["exr2video", "-i", str(path), "--dst", "ACEScct"])
        logs: list[str] = []
        src, _dst = resolve_e2v_spaces(cfg, args, logs.append)
        return src, logs

    def test_rec709_chromaticities_pick_linear_rec709(self, tmp_path: Path):
        p = _write_exr(tmp_path / "shot.0001.exr", chromaticities=REC709)
        src, logs = self._resolve(p)
        assert src == "Linear Rec.709 (sRGB)"
        assert any("chromaticities" in line for line in logs)
        assert not any(line.startswith("Warning") for line in logs)

    def test_ap1_chromaticities_pick_acescg(self, tmp_path: Path):
        p = _write_exr(tmp_path / "shot.0001.exr", chromaticities=AP1)
        src, _logs = self._resolve(p)
        assert src == "ACEScg"

    def test_blender_interop_id(self, tmp_path: Path):
        p = _write_exr(tmp_path / "f_0001.exr", attrs={"colorInteropID": "lin_rec709_scene"})
        src, _logs = self._resolve(p)
        assert src == "Linear Rec.709 (sRGB)"

    def test_no_metadata_warns_with_assumed_space(self, tmp_path: Path):
        p = _write_exr(tmp_path / "bare.0001.exr")
        src, logs = self._resolve(p)
        assert src == "ACEScg"
        warnings = [line for line in logs if line.startswith("Warning")]
        assert len(warnings) == 1
        assert "Assuming ACEScg" in warnings[0]
        assert "--src" in warnings[0]
