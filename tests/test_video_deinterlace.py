"""Tests for the bwdif deinterlace path in :mod:`src.core.video`.

These guard the hardening that keeps interlaced sources (e.g. 1080i Sony MXF)
from ever being written as combed EXR frames, while leaving progressive
footage untouched and the frame count unchanged.
"""

from __future__ import annotations

import numpy as np
import pytest

av = pytest.importorskip("av")

from src.core.video import decode_video_frames  # noqa: E402

_N_FRAMES = 6


def _make_clip(path, frames, *, fps: int = 25, pix: str = "yuv444p", codec: str = "ffv1") -> None:
    """Encode *frames* (list of HxWx3 uint8 RGB) to a lossless clip."""
    container = av.open(str(path), mode="w")
    stream = container.add_stream(codec, rate=fps)
    stream.height, stream.width = frames[0].shape[:2]
    stream.pix_fmt = pix
    try:
        for arr in frames:
            vf = av.VideoFrame.from_ndarray(arr, format="rgb24").reformat(format=pix)
            for pkt in stream.encode(vf):
                container.mux(pkt)
        for pkt in stream.encode():
            container.mux(pkt)
    finally:
        container.close()


def _decode(path, mode: str) -> list[np.ndarray]:
    container = av.open(str(path))
    stream = container.streams.video[0]
    try:
        return [f.to_ndarray(format="rgb24") for f in decode_video_frames(container, stream, mode)]
    finally:
        container.close()


@pytest.fixture
def progressive_clip(tmp_path):
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 256, (16, 16, 3), dtype=np.uint8) for _ in range(_N_FRAMES)]
    path = tmp_path / "progressive.mkv"
    _make_clip(path, frames)
    return path


@pytest.fixture
def moving_clip(tmp_path):
    """A genuinely woven clip: even/odd rows come from time-shifted moments.

    Diagonal stripes give vertical detail, and the two fields are taken from
    different phases (so they disagree on moving edges) — exactly the comb a
    deinterlacer must reconstruct.
    """
    yy, xx = np.mgrid[0:32, 0:32]
    frames = []
    for i in range(_N_FRAMES):
        field_a = (((xx + yy + i) % 8) < 4).astype(np.uint8) * 255
        field_b = (((xx + yy + i + 3) % 8) < 4).astype(np.uint8) * 255
        woven = field_a.copy()
        woven[1::2] = field_b[1::2]
        frames.append(np.repeat(woven[:, :, None], 3, axis=2))
    path = tmp_path / "moving.mkv"
    _make_clip(path, frames)
    return path


class TestFrameCountPreserved:
    @pytest.mark.parametrize("mode", ["off", "auto", "on"])
    def test_count_unchanged(self, progressive_clip, mode):
        assert len(_decode(progressive_clip, mode)) == _N_FRAMES


class TestProgressiveUntouched:
    def test_auto_is_passthrough_for_progressive(self, progressive_clip):
        # 'auto' only deinterlaces frames flagged interlaced, so a progressive
        # source must come back bit-identical to 'off'.
        off = _decode(progressive_clip, "off")
        auto = _decode(progressive_clip, "auto")
        assert len(off) == len(auto)
        for a, b in zip(off, auto, strict=True):
            assert np.array_equal(a, b)


class TestDeinterlaceRunsUnderMotion:
    def test_force_on_alters_pixels(self, moving_clip):
        # deint=all forces bwdif on every frame; with inter-frame motion it must
        # reconstruct fields by interpolation, so output differs from the raw
        # woven frames. This proves the filter graph is actually wired in.
        off = _decode(moving_clip, "off")
        on = _decode(moving_clip, "on")
        assert len(off) == len(on) == _N_FRAMES
        assert any(not np.array_equal(a, b) for a, b in zip(off, on, strict=True))
