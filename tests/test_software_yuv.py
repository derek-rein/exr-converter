"""Software (PyAV / FFmpeg) Y'CbCr conversion: BT.709, rounding, alpha, tags."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import OpenImageIO as oiio
import pytest

from src.core.color_tags import ColorTags, apply_to_codec_context
from src.core.convert import _encode_rgb_u16, run_exr_to_video
from src.core.ocio_utils import resolve_ocio_for_cli
from src.core.yuv import (
    pix_fmt_without_alpha,
    rgb48_to_video_frame,
    rgb_to_ycbcr_codes,
)
from tests.support.integration import write_synthetic_exr_sequence

KR, KB = 0.2126, 0.0722


def _ideal_12bit(rgb_u16: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = rgb_u16.astype(np.float64) / 65535.0
    y = KR * r[..., 0] + (1 - KR - KB) * r[..., 1] + KB * r[..., 2]
    cb = (r[..., 2] - y) / (2 * (1 - KB))
    cr = (r[..., 0] - y) / (2 * (1 - KR))
    return (16 + 219 * y) * 16, (128 + 224 * cb) * 16, (128 + 224 * cr) * 16


def _gradient(h: int = 64, w: int = 256) -> np.ndarray:
    x = np.linspace(0, 1, w)[None, :] * np.ones((h, 1))
    yv = np.linspace(0, 1, h)[:, None] * np.ones((1, w))
    rgb = np.stack([x, 0.3 + 0.5 * yv, 1 - x], axis=-1)
    return np.rint(rgb * 65535).astype(np.uint16)


def _planes(frame, width: int, height: int) -> list[np.ndarray]:
    out = []
    for plane in frame.planes:
        raw = np.frombuffer(bytes(plane), dtype="<u2").reshape(-1, plane.line_size // 2)
        out.append(raw[:height, :width].astype(np.float64))
    return out


class TestConversion:
    def test_reference_codes_10bit(self):
        rgb = np.array([[[0, 0, 0], [65535, 65535, 65535], [65535, 0, 0]]], dtype=np.uint16)
        y, cb, cr = rgb_to_ycbcr_codes(rgb, 10)
        assert y.tolist() == [[64, 940, 250]]
        assert cb.tolist() == [[512, 512, 409]]
        assert cr.tolist() == [[512, 512, 960]]

    def test_422_averages_pairs(self):
        rgb = np.zeros((2, 4, 3), dtype=np.uint16)
        rgb[:, 1::2, 0] = 65535
        _y, cb, cr = rgb_to_ycbcr_codes(rgb, 10, chroma_step=2)
        assert cb.shape == (2, 2)
        assert np.all(cr == np.rint(512 + 448 * 0.5))

    def test_frame_handles_line_padding(self):
        rgb = _gradient(10, 50)  # 50 px * 2 bytes is not a 32/64-byte multiple
        frame = rgb48_to_video_frame(rgb, "yuv444p10le")
        y, _cb, _cr = _planes(frame, 50, 10)
        ref, _, _ = rgb_to_ycbcr_codes(rgb, 10)
        assert np.array_equal(y, ref.astype(np.float64))
        assert frame.colorspace == 1

    def test_rgb_target_untouched(self):
        frame = rgb48_to_video_frame(_gradient(8, 16), "gbrp12le")
        assert frame.format.name == "rgb48le"

    def test_drop_alpha_names(self):
        assert pix_fmt_without_alpha("yuva444p10le") == "yuv444p10le"
        assert pix_fmt_without_alpha("yuv422p10le") == "yuv422p10le"


class TestProresKs4444Bias:
    """No systematic luma / chroma offset after an FFmpeg decode (was ~+2.5 Y codes)."""

    def test_unbiased_after_decode(self, tmp_path: Path):
        out = tmp_path / "sw4444.mov"
        rgb = _gradient()
        h, w = rgb.shape[:2]
        container = av.open(str(out), mode="w")
        stream = container.add_stream("prores_ks", rate=24)
        stream.width, stream.height, stream.pix_fmt = w, h, "yuv444p10le"
        apply_to_codec_context(stream.codec_context, ColorTags())
        stream.options = {"profile": "4", "vendor": "apl0"}
        _encode_rgb_u16(stream, container, rgb, "yuv444p10le", w, h)
        for packet in stream.encode():
            container.mux(packet)
        container.close()

        probe = av.open(str(out))
        frame = next(probe.decode(video=0))
        y, cb, cr = _planes(frame, w, h)
        probe.close()
        iy, icb, icr = _ideal_12bit(rgb)
        # 10-bit coding: error per pixel is up to ~2 12-bit codes, mean ~0.
        assert abs((y - iy).mean()) < 0.5
        assert abs((cb - icb).mean()) < 0.5
        assert abs((cr - icr).mean()) < 0.5
        assert np.abs(y - iy).mean() < 1.5


def _write_rgba_exr(directory: Path, frames: tuple[int, ...] = (1, 2)) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    h, w = 48, 64
    for n in frames:
        spec = oiio.ImageSpec(w, h, 4, oiio.HALF)
        spec.channelnames = ("R", "G", "B", "A")
        spec.alpha_channel = 3
        px = np.zeros((h, w, 4), dtype=np.float32)
        px[..., :3] = 0.18
        px[:, : w // 2, 3] = 1.0
        px[:, w // 2 :, 3] = 0.25
        path = str(directory / f"rgba.{n:04d}.exr")
        out = oiio.ImageOutput.create(path)
        assert out is not None
        out.open(path, spec)
        out.write_image(px)
        out.close()


@pytest.mark.integration
class TestSoftware4444Alpha:
    @pytest.mark.parametrize("workers", [1, 2])
    def test_no_alpha_plane_without_source_alpha(self, tmp_path: Path, workers: int):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=64, height=48)
        out = tmp_path / "noalpha.mov"
        logs: list[str] = []
        bundled = resolve_ocio_for_cli(None)
        run_exr_to_video(
            str(exr_dir),
            out,
            bundled,
            "ACEScg",
            "ACEScct",
            fps=24,
            workers=workers,
            video_codec="prores_ks",
            pix_fmt_out="yuva444p10le",
            codec_key="prores_4444",
            log=logs.append,
            config_path=str(Path("resources/ocio/aces-studio-v4.ocio").resolve()),
        )
        probe = av.open(str(out))
        cc = probe.streams.video[0].codec_context
        assert "yuva" not in cc.pix_fmt
        assert (cc.color_primaries, cc.color_trc, cc.colorspace) == (2, 2, 1)
        probe.close()
        assert any("no alpha plane" in line for line in logs)

    @pytest.mark.parametrize("workers", [1, 2])
    def test_alpha_carried_when_source_has_it(self, tmp_path: Path, workers: int):
        exr_dir = tmp_path / "exr"
        _write_rgba_exr(exr_dir)
        out = tmp_path / "alpha.mov"
        run_exr_to_video(
            str(exr_dir),
            out,
            resolve_ocio_for_cli(None),
            "ACEScg",
            "Rec.1886 Rec.709 - Display",
            fps=24,
            workers=workers,
            video_codec="prores_ks",
            pix_fmt_out="yuva444p10le",
            codec_key="prores_4444",
            config_path=str(Path("resources/ocio/aces-studio-v4.ocio").resolve()),
        )
        probe = av.open(str(out))
        cc = probe.streams.video[0].codec_context
        assert cc.pix_fmt.startswith("yuva")
        assert (cc.color_primaries, cc.color_trc, cc.colorspace) == (1, 1, 1)
        frame = next(probe.decode(video=0))
        alpha = np.frombuffer(bytes(frame.planes[3]), dtype="<u2").reshape(
            -1, frame.planes[3].line_size // 2
        )[:48, :64]
        probe.close()
        peak = float((1 << frame.format.components[3].bits) - 1)
        assert alpha[:, :24].mean() / peak == pytest.approx(1.0, abs=0.01)
        assert alpha[:, 40:].mean() / peak == pytest.approx(0.25, abs=0.01)
