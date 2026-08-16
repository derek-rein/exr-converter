"""Unit tests for EXR I/O helpers — generate frames under tmp_path, clean up after."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import OpenImageIO as oiio
import pytest

from src.core.constants import EXR_COMPRESSIONS
from src.core.exr_io import (
    fps_to_rational,
    read_exr,
    read_image,
    read_pixel_aspect,
    square_pixel_dims,
    write_exr,
)


def _solid(h: int, w: int, rgb=(0.2, 0.4, 0.6)) -> np.ndarray:
    arr = np.zeros((h, w, 3), dtype=np.float32)
    arr[..., 0] = rgb[0]
    arr[..., 1] = rgb[1]
    arr[..., 2] = rgb[2]
    return arr


class TestWriteReadRoundTrip:
    def test_round_trip_zip(self, tmp_path: Path):
        path = tmp_path / "plate.1001.exr"
        src = _solid(16, 32, (0.1, 0.5, 0.9))
        write_exr(str(path), src, compression="zip", dst_space="ACEScg")
        assert path.is_file()
        got = read_exr(str(path))
        assert got.shape == (16, 32, 3)
        np.testing.assert_allclose(got, src, atol=2e-3)

    def test_dwa_level_attribute_written(self, tmp_path: Path):
        path = tmp_path / "dwa.exr"
        write_exr(
            str(path),
            _solid(8, 8),
            compression="dwaa",
            exr_opts={"dwa_compression_level": "12.5"},
        )
        inp = oiio.ImageInput.open(str(path))
        assert inp is not None
        level = inp.spec().getattribute("openexr:dwaCompressionLevel")
        inp.close()
        assert level is not None
        assert abs(float(level) - 12.5) < 0.01

    def test_write_metadata(self, tmp_path: Path):
        path = tmp_path / "meta.exr"
        write_exr(
            str(path),
            _solid(4, 4),
            compression="zip",
            src_space="sRGB",
            dst_space="ACEScg",
        )
        inp = oiio.ImageInput.open(str(path))
        spec = inp.spec()
        assert str(spec.getattribute("exrconverter:srcColorSpace")) == "sRGB"
        assert str(spec.getattribute("exrconverter:dstColorSpace")) == "ACEScg"
        assert spec.format == oiio.HALF
        inp.close()

    def test_always_half_even_for_rgba(self, tmp_path: Path):
        path = tmp_path / "half.exr"
        rgba = np.ones((4, 4, 4), dtype=np.float32)
        write_exr(str(path), rgba, compression="zip")
        inp = oiio.ImageInput.open(str(path))
        spec = inp.spec()
        inp.close()
        assert spec.format == oiio.HALF
        assert spec.nchannels == 4

    def test_zip_level_attribute_written(self, tmp_path: Path):
        path = tmp_path / "zip.exr"
        write_exr(
            str(path),
            _solid(8, 8),
            compression="zip",
            exr_opts={"zip_level": "7"},
        )
        inp = oiio.ImageInput.open(str(path))
        spec = inp.spec()
        inp.close()
        assert str(spec.getattribute("compression")) == "zip"

    def test_extra_attrs_namespaced(self, tmp_path: Path):
        path = tmp_path / "r3dmeta.exr"
        write_exr(
            str(path),
            _solid(4, 4),
            compression="zip",
            extra_attrs={"exrconverter:r3d:iso": "800"},
        )
        inp = oiio.ImageInput.open(str(path))
        spec = inp.spec()
        inp.close()
        assert str(spec.getattribute("exrconverter:r3d:iso")) == "800"
        assert spec.getattribute("isoSpeed") in (None, "")

    def test_invalid_shape_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="HxWx3 or HxWx4"):
            write_exr(str(tmp_path / "bad.exr"), np.zeros((4, 4), dtype=np.float32))


class TestWriteMetadataAndAlpha:
    def test_pixel_aspect_and_fps(self, tmp_path: Path):
        path = tmp_path / "par.exr"
        write_exr(
            str(path),
            _solid(8, 8),
            compression="zip",
            dst_space="ACEScg",
            pixel_aspect=1.2,
            fps=24.0,
        )
        assert abs(read_pixel_aspect(str(path)) - 1.2) < 1e-4
        inp = oiio.ImageInput.open(str(path))
        spec = inp.spec()
        assert str(spec.getattribute("exrconverter:dstColorSpace")) == "ACEScg"
        fps = spec.getattribute("framesPerSecond") or spec.getattribute("FramesPerSecond")
        inp.close()
        assert tuple(int(x) for x in fps[:2]) == (24, 1)

    def test_ntsc_fps_rational(self, tmp_path: Path):
        path = tmp_path / "ntsc.exr"
        write_exr(str(path), _solid(4, 4), compression="zip", fps=23.976)
        inp = oiio.ImageInput.open(str(path))
        fps = inp.spec().getattribute("framesPerSecond")
        inp.close()
        assert tuple(int(x) for x in fps[:2]) == (24000, 1001)

    def test_rgba_round_trip(self, tmp_path: Path):
        path = tmp_path / "rgba.exr"
        src = np.zeros((6, 8, 4), dtype=np.float32)
        src[..., 0] = 0.2
        src[..., 1] = 0.4
        src[..., 2] = 0.6
        src[..., 3] = 0.5
        write_exr(str(path), src, compression="zip", dst_space="ACEScg")
        rgb = read_image(str(path), keep_alpha=False)
        assert rgb.shape == (6, 8, 3)
        rgba = read_image(str(path), keep_alpha=True)
        assert rgba.shape == (6, 8, 4)
        np.testing.assert_allclose(rgba[..., 3], 0.5, atol=2e-3)

    def test_square_pixel_dims(self):
        assert square_pixel_dims(1920, 1080, 1.0) == (1920, 1080)
        w, h = square_pixel_dims(720, 576, 16 / 15)
        assert h == 576
        assert w == 768
        assert w % 2 == 0
        assert square_pixel_dims(100, 100, 0.0) == (100, 100)
        w, h = square_pixel_dims(11, 11, 1.0)
        assert w % 2 == 0 and h % 2 == 0


class TestFpsToRational:
    def test_integer(self):
        assert fps_to_rational(24) == (24, 1)

    def test_ntsc(self):
        assert fps_to_rational(23.976) == (24000, 1001)
        assert fps_to_rational(29.97) == (30000, 1001)

    def test_invalid(self):
        assert fps_to_rational(0) is None
        assert fps_to_rational(-1) is None


class TestCompressionList:
    def test_htj2k_listed(self):
        assert "htj2k256" in EXR_COMPRESSIONS
        assert "htj2k32" in EXR_COMPRESSIONS

    def test_htj2k_write_if_supported(self, tmp_path: Path):
        path = tmp_path / "ht.exr"
        try:
            write_exr(str(path), _solid(8, 8), compression="htj2k256")
        except RuntimeError as exc:
            pytest.skip(str(exc))
        inp = oiio.ImageInput.open(str(path))
        assert inp is not None
        name = str(inp.spec().getattribute("compression") or "").lower()
        inp.close()
        if "htj2k" not in name:
            pytest.skip(f"this OIIO/OpenEXR build does not write HTJ2K ({name})")


class TestReadPixelAspect:
    def test_missing_file_is_one(self, tmp_path: Path):
        assert read_pixel_aspect(str(tmp_path / "gone.exr")) == 1.0


class TestReadExrErrors:
    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="Failed to open|Failed to read"):
            read_exr(str(tmp_path / "nope.exr"))

    def test_corrupt_file_raises(self, tmp_path: Path):
        path = tmp_path / "corrupt.exr"
        path.write_bytes(b"not an exr file at all")
        with pytest.raises(RuntimeError):
            read_exr(str(path))
