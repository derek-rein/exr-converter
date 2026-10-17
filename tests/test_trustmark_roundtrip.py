"""Embed a TrustMark mark and read it back. Linux CI only.

The encoder and decoder ONNX files download on first use (about 64 MB).
macOS and Windows CI skip this so those jobs stay cheap.
"""

from __future__ import annotations

import sys
from pathlib import Path

import av
import numpy as np
import pytest

from src.core.proxy import run_video_to_proxy
from src.core.trustmark.detect import decode_rgb, detect_video
from src.core.trustmark.embed import FrameWatermark

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="TrustMark model roundtrip runs on Linux CI only",
)

_PAYLOAD = "mysecret"


def _pattern(height: int, width: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    image = np.zeros((height, width, 3), np.float32)
    image[..., 0] = xx / width
    image[..., 1] = yy / height
    image[..., 2] = 0.5 + 0.5 * np.sin(xx / 13.0) * np.cos(yy / 9.0)
    noise = rng.random((height, width, 3), dtype=np.float32) * 0.2
    return np.clip(image * 0.85 + noise, 0.0, 1.0) * 255.0


def _write_clip(path: Path, *, frames: int = 4, width: int = 640, height: int = 360) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("libx264", rate=24)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.options = {"preset": "ultrafast", "crf": "16"}
    for index in range(frames):
        rgb = np.rint(_pattern(height, width, seed=index + 3)).astype(np.uint8)
        for packet in stream.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def test_embed_and_detect_a_synthetic_frame() -> None:
    mark = FrameWatermark(_PAYLOAD, 1.0)
    frame = np.rint(_pattern(256, 256, seed=1)).astype(np.uint8)
    plain, plain_ok, _flips = decode_rgb(frame)
    assert plain_ok is False
    marked = mark.apply(frame)
    assert not np.array_equal(marked, frame)
    text, ok, flips = decode_rgb(marked)
    assert (text, ok) == (_PAYLOAD, True)
    assert flips >= 0
    # Wider than 2:1: the mark lives in the center square.
    wide = np.rint(_pattern(180, 640, seed=2)).astype(np.uint8)
    wide_text, wide_ok, _wide_flips = decode_rgb(mark.apply(wide))
    assert (wide_text, wide_ok) == (_PAYLOAD, True)


@pytest.mark.parametrize("codec_key", ["h264", "prores_proxy"])
def test_mark_survives_proxy_encode(tmp_path: Path, codec_key: str) -> None:
    source = tmp_path / "clip.mov"
    _write_clip(source)
    output = tmp_path / ("proxy.mp4" if codec_key == "h264" else "proxy.mov")
    run_video_to_proxy(
        source,
        output,
        codec_key=codec_key,
        resolution="original",
        audio=False,
        watermark_text=_PAYLOAD,
        watermark_strength=1.0,
    )
    found = detect_video(output, samples=4)
    assert found.detected is True
    assert found.text == _PAYLOAD
    assert found.frames == 4
