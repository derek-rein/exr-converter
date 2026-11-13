"""Source colour space guess from video codec tags (PyAV returns H.273 ints)."""

from __future__ import annotations

import pytest

from src.core.video import colorspace_candidates_from_tags

REC709 = [
    "Output - Rec.709",
    "Rec.1886 Rec.709 - Display",
    "Gamma 2.4 Rec.709 - Texture",
    "sRGB - Display",
]
PQ = ["Output - Rec.2100-PQ", "Rec.2100-PQ - Display"]
HLG = ["Output - Rec.2100-HLG", "Rec.2100-HLG - Display"]
REC2020 = [
    "Output - Rec.2020",
    "Rec.1886 Rec.2020 - Display",
    "Output - Rec.709",
    "sRGB - Display",
]
SRGB = [
    "sRGB - Display",
    "Output - sRGB",
    "Output - Rec.709",
    "Rec.1886 Rec.709 - Display",
]


def _guess(**kw):
    args = {
        "codec": "h264",
        "pix_fmt": "yuv420p",
        "color_trc": 0,
        "color_primaries": 0,
        "colorspace": 0,
        "make": "",
        "encoder": "",
    }
    args.update(kw)
    return colorspace_candidates_from_tags(**args)


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"color_trc": 16, "color_primaries": 9, "colorspace": 9}, PQ),
        ({"color_trc": 18, "color_primaries": 9, "colorspace": 9}, HLG),
        ({"color_trc": 8}, ["scene_linear"]),
        ({"color_trc": 13, "color_primaries": 1}, SRGB),
        ({"color_trc": 1, "color_primaries": 9}, REC2020),
        ({"color_trc": 1, "colorspace": 9}, REC2020),
        ({"color_trc": 1, "color_primaries": 1, "colorspace": 1}, REC709),
        ({"color_trc": 2, "color_primaries": 2, "colorspace": 2}, REC709),
        ({}, REC709),
    ],
)
def test_h273_int_codes(kw, expected):
    assert _guess(**kw) == expected


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"color_trc": "smpte2084"}, PQ),
        ({"color_trc": "arib-std-b67"}, HLG),
        ({"color_trc": "linear"}, ["scene_linear"]),
        ({"color_trc": "iec61966-2-1"}, SRGB),
        ({"color_primaries": "bt2020"}, REC2020),
        ({"color_trc": "", "color_primaries": "", "colorspace": ""}, REC709),
    ],
)
def test_ffmpeg_names_still_work(kw, expected):
    assert _guess(**kw) == expected


def test_log_codec_and_apple_hint():
    assert _guess(codec="applelog") == ["Apple Log", "Cineon", "scene_linear"]
    assert _guess(codec="somelog") == ["Cineon", "Apple Log", "scene_linear"]


def test_ffv1_10bit_is_linear():
    assert _guess(codec="ffv1", pix_fmt="yuv444p10le") == ["scene_linear"]
    assert _guess(codec="ffv1", pix_fmt="yuv420p") == REC709


@pytest.mark.integration
def test_pq_clip_guesses_pq(tmp_path):
    av = pytest.importorskip("av")
    import numpy as np

    from src.core.video import guess_video_colorspace_candidates

    path = tmp_path / "pq.mp4"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("libx264", rate=24)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        stream.codec_context.color_trc = 16
        stream.codec_context.color_primaries = 9
        stream.codec_context.colorspace = 9
        rgb = np.zeros((64, 64, 3), np.uint8)
        for _ in range(3):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24").reformat(format="yuv420p")
            for pkt in stream.encode(frame):
                out.mux(pkt)
        for pkt in stream.encode():
            out.mux(pkt)
    assert guess_video_colorspace_candidates(str(path)) == PQ
