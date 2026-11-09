"""Hardware decode selection and fallback (the GPU tests skip without one)."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import pytest

from src.core import decoders
from src.core.constants import DECODER_AUTO, DECODER_SOFTWARE
from src.core.decoders import (
    candidate_devices,
    clear_probe_cache,
    normalize_frame,
    open_input,
    set_decode_mode,
)
from src.core.video import av_frame_to_rgb_ndarray


def _make_clip(path: Path, codec: str, pix_fmt: str) -> Path:
    h, w = 128, 192
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.stack([xx * 255 // w, yy * 255 // h, (xx + yy) * 255 // (w + h)], -1).astype(np.uint8)
    with av.open(str(path), "w") as c:
        s = c.add_stream(codec, rate=24)
        s.width, s.height, s.pix_fmt = w, h, pix_fmt
        for _ in range(4):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24").reformat(format=pix_fmt)
            c.mux(s.encode(frame))
        c.mux(s.encode(None))
    return path


@pytest.fixture(autouse=True)
def _reset_decode_mode():
    clear_probe_cache()
    set_decode_mode(DECODER_AUTO)
    yield
    clear_probe_cache()
    set_decode_mode(DECODER_AUTO)


@pytest.fixture
def h264_420(tmp_path: Path) -> Path:
    return _make_clip(tmp_path / "a.mp4", "libx264", "yuv420p")


def test_unknown_mode_means_auto() -> None:
    set_decode_mode("gpu-please")
    assert decoders.decode_mode() == DECODER_AUTO


def test_software_mode_never_uses_a_device(h264_420: Path) -> None:
    set_decode_mode(DECODER_SOFTWARE)
    c, device = open_input(str(h264_420))
    c.close()
    assert device == ""


def test_no_devices_means_software(monkeypatch, h264_420: Path) -> None:
    monkeypatch.setattr(decoders, "candidate_devices", lambda: [])
    c, device = open_input(str(h264_420))
    c.close()
    assert device == ""


def test_failed_probe_falls_back_and_is_cached(monkeypatch, h264_420: Path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(decoders, "candidate_devices", lambda: ["cuda", "d3d11va"])
    monkeypatch.setattr(decoders, "_first_frame_decodes", lambda p, d, o: calls.append(d) or False)
    for _ in range(2):
        c, device = open_input(str(h264_420))
        assert device == ""
        assert c.streams.video[0].codec_context.name == "h264"  # a usable software container
        c.close()
    assert calls == ["cuda", "d3d11va"]  # the second open reuses the cached failures


def test_other_codecs_and_422_are_not_offered_to_the_gpu(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(decoders, "candidate_devices", lambda: ["cuda"])
    monkeypatch.setattr(decoders, "_first_frame_decodes", lambda *a: pytest.fail("probed"))
    for codec, pix_fmt, name in (
        ("libx264", "yuv422p", "h422.mp4"),
        ("mpeg4", "yuv420p", "mp4v.mp4"),
    ):
        c, device = open_input(str(_make_clip(tmp_path / name, codec, pix_fmt)))
        c.close()
        assert device == ""


def test_normalize_frame_only_touches_hardware_nv12() -> None:
    frame = av.VideoFrame(64, 64, "nv12")
    assert normalize_frame(frame, "").format.name == "nv12"
    assert normalize_frame(frame, "cuda").format.name == "yuv420p"
    p010 = av.VideoFrame(64, 64, "p010le")
    assert normalize_frame(p010, "cuda") is p010


@pytest.mark.skipif(not candidate_devices(), reason="no hardware decode device in this FFmpeg")
def test_hardware_decode_matches_software_pixels(h264_420: Path) -> None:
    c, device = open_input(str(h264_420))
    if not device:
        c.close()
        pytest.skip("this machine cannot decode H.264 on the GPU")
    hw = normalize_frame(next(c.decode(c.streams.video[0])), device)
    c.close()
    set_decode_mode(DECODER_SOFTWARE)
    c, _ = open_input(str(h264_420))
    sw = next(c.decode(c.streams.video[0]))
    c.close()
    a = av_frame_to_rgb_ndarray(hw, pix_fmt="rgb48le")
    b = av_frame_to_rgb_ndarray(sw, pix_fmt="rgb48le")
    assert np.array_equal(a, b)
