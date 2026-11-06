"""Apple ProRes RAW: detection, header, FFmpeg 16-bit decode, develop and convert.

The clips are synthetic (``tests/support/prores_raw_synth.py``) but they are
decoded by FFmpeg's real ``prores_raw`` decoder.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

av = pytest.importorskip("av")

from src.core import prores_raw as pr  # noqa: E402
from src.core.cinemadng.color import _AP0_FROM_XYZ_D60, _D60_XY, _cat  # noqa: E402
from tests.support.prores_raw_synth import FrameHeader, encode_frame, write_mov  # noqa: E402

needs_decoder = pytest.mark.skipif(not pr.is_available(), reason=pr.unavailable_reason())

ROWS, BLOCKS = 4, 16  # 256 x 64 sensor


def _dc(r: int, g: int, b: int, *, rows: int = ROWS, blocks: int = BLOCKS) -> np.ndarray:
    dc = np.zeros((4, rows, blocks), dtype=int)
    dc[0], dc[1], dc[2], dc[3] = r, g, g, b
    return dc


def _header(**kw) -> FrameHeader:
    kw.setdefault("width", BLOCKS * 16)
    kw.setdefault("height", ROWS * 16)
    kw.setdefault("qscale", 16)
    return FrameHeader(**kw)


def _clip(tmp_path, frames: list[np.ndarray] | None = None, name: str = "A001C001.mov", **kw):
    header = _header(**kw)
    frames = frames if frames is not None else [_dc(300, 400, 350)]
    path = tmp_path / name
    write_mov(str(path), [encode_frame(f, header) for f in frames], header.width, header.height)
    return path, header


# ---------------------------------------------------------------------------
# Detection and header
# ---------------------------------------------------------------------------


def test_sniff_finds_prores_raw_and_hq(tmp_path):
    path, _ = _clip(tmp_path)
    assert pr.prores_raw_fourcc(path) == b"aprn"
    hq = tmp_path / "hq.mov"
    header = _header()
    write_mov(str(hq), [encode_frame(_dc(1, 1, 1), header)], 256, 64, fourcc=b"aprh")
    assert pr.prores_raw_fourcc(hq) == b"aprh"


def test_sniff_rejects_other_video(tmp_path):
    path = tmp_path / "prores.mov"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("prores_ks", rate=24)
        stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv422p10le"
        frame = av.VideoFrame.from_ndarray(np.zeros((32, 64, 3), np.uint8), format="rgb24")
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    assert pr.prores_raw_fourcc(path) is None
    text = tmp_path / "notes.mov"
    text.write_bytes(b"not a movie at all")
    assert pr.prores_raw_fourcc(text) is None
    assert pr.prores_raw_fourcc(tmp_path / "missing.mov") is None


def test_sniff_rejects_appledouble_and_other_suffixes(tmp_path):
    path, _ = _clip(tmp_path)
    sidecar = tmp_path / "._A001C001.mov"
    sidecar.write_bytes(path.read_bytes())
    assert pr.prores_raw_fourcc(sidecar) is None
    other = tmp_path / "A001C001.mxf"
    other.write_bytes(path.read_bytes())
    assert pr.prores_raw_fourcc(other) is None


def test_parse_frame_header_round_trip():
    header = _header(
        sensel_range=0xF000,
        wb_red=1.875,
        wb_blue=1.5,
        gain=2.0,
        cct=3200,
        crop=(2, 4, 6, 8),
        curve=(0, 512, 1024, 2048, 4096, 8192, 16384, 32768),
    )
    parsed = pr.parse_frame_header(encode_frame(_dc(1, 1, 1), header))
    assert (parsed.width, parsed.height) == (256, 64)
    assert parsed.vendor == "atm0" and parsed.vendor_name == "Atomos"
    assert parsed.crop == (2, 4, 6, 8)
    assert parsed.cropped_size == (250, 50)
    assert parsed.black == 256 and parsed.white == 0xF000 + 256
    assert parsed.wb_red == pytest.approx(1.875) and parsed.wb_blue == pytest.approx(1.5)
    assert parsed.gain == pytest.approx(2.0) and parsed.cct == 3200
    assert parsed.matrix == pytest.approx(header.matrix, rel=1e-6)
    assert parsed.curve == header.curve


def test_parse_frame_header_rejects_garbage():
    with pytest.raises(ValueError):
        pr.parse_frame_header(b"\0" * 200)
    good = encode_frame(_dc(1, 1, 1), _header())
    with pytest.raises(ValueError):
        pr.parse_frame_header(good[:40])
    bad_len = good[:8] + struct.pack(">H", 10) + good[10:]
    with pytest.raises(ValueError):
        pr.parse_frame_header(bad_len)


# ---------------------------------------------------------------------------
# FFmpeg decode precision
# ---------------------------------------------------------------------------


def _decode_mosaic(dc: np.ndarray, header: FrameHeader) -> np.ndarray:
    ctx = av.CodecContext.create("prores_raw", "r")
    ctx.options = {"apply_cropping": "0"}
    frames = ctx.decode(av.Packet(encode_frame(dc, header)))
    assert frames, "decoder returned no frame"
    return pr._frame_mosaic(frames[0]).copy()


@needs_decoder
def test_decoder_output_is_16_bit_linear():
    """FFmpeg 8 gave 12-bit samples shifted up (all multiples of 16). FFmpeg 9 is 16-bit."""
    values = np.arange(-2048, 2048)
    blocks = 64
    rows = values.size // blocks
    dc = np.zeros((4, rows, blocks), dtype=int)
    dc[0] = values.reshape(rows, blocks)
    mosaic = _decode_mosaic(dc, _header(width=blocks * 16, height=rows * 16))
    red = mosaic[0::2, 0::2].reshape(rows, 8, blocks, 8)[:, 0, :, 0].ravel().astype(int)
    levels = np.unique(red)
    assert len(levels) > 4000
    assert red.min() < 256 and red.max() > 60000  # spans the 16-bit range
    assert np.any(levels % 16 != 0)
    assert np.all(np.diff(red) >= 0)  # monotonic in the coded value


@needs_decoder
def test_decoder_applies_the_header_curve():
    dc = _dc(500, 500, 500)
    linear = _decode_mosaic(dc, _header())
    doubled = _decode_mosaic(dc, _header(curve=(0, 1024, 2048, 4096, 8192, 16384, 32768, 65535)))
    assert doubled.mean() > linear.mean() * 1.8


# ---------------------------------------------------------------------------
# Develop
# ---------------------------------------------------------------------------


def _expected_aces(rgb_cam: np.ndarray, header) -> np.ndarray:
    m = np.asarray(header.matrix, dtype=np.float64).reshape(3, 3) * header.gain
    to_ap0 = _AP0_FROM_XYZ_D60 @ _cat(pr._D65_XY, _D60_XY) @ m
    return rgb_cam @ to_ap0.T


def test_develop_flat_field_matches_reference():
    header = pr.parse_frame_header(
        encode_frame(_dc(1, 1, 1), _header(wb_red=1.8, wb_blue=1.4, gain=1.5))
    )
    mosaic = np.empty((64, 256), dtype=np.uint16)
    mosaic[0::2, 0::2] = 10000
    mosaic[0::2, 1::2] = 20000
    mosaic[1::2, 0::2] = 20000
    mosaic[1::2, 1::2] = 15000
    rgb = pr.develop(mosaic, header)
    span = header.white - header.black
    cam = np.array(
        [
            (10000 - header.black) / span * 1.8,
            (20000 - header.black) / span,
            (15000 - header.black) / span * 1.4,
        ]
    )
    assert rgb.dtype == np.float32
    np.testing.assert_allclose(rgb[30, 100], _expected_aces(cam, header), rtol=1e-5)


def test_develop_white_balanced_neutral_is_neutral_in_aces():
    header = pr.parse_frame_header(encode_frame(_dc(1, 1, 1), _header(wb_red=2.0, wb_blue=1.25)))
    mosaic = np.empty((64, 256), dtype=np.uint16)
    g = 24000
    mosaic[0::2, 1::2] = mosaic[1::2, 0::2] = g
    mosaic[0::2, 0::2] = round((g - 256) / 2.0 + 256)
    mosaic[1::2, 1::2] = round((g - 256) / 1.25 + 256)
    rgb = pr.develop(mosaic, header)[20, 100]
    assert rgb[0] == pytest.approx(rgb[1], rel=2e-4)
    assert rgb[2] == pytest.approx(rgb[1], rel=2e-4)


def test_develop_keeps_every_16_bit_level():
    header = pr.parse_frame_header(encode_frame(_dc(1, 1, 1), _header()))
    ramp = np.arange(0, 65536, dtype=np.uint16)[: 256 * 256].reshape(256, 256)
    mosaic = np.repeat(np.repeat(ramp[:32, :128], 2, axis=0), 2, axis=1)  # 2x2 = one level
    rgb = pr.develop(mosaic, header, half=True)
    assert len(np.unique(rgb[..., 1])) == 32 * 128


def test_develop_does_not_clip_below_black_or_above_white():
    header = pr.parse_frame_header(encode_frame(_dc(1, 1, 1), _header(sensel_range=30000)))
    mosaic = np.full((64, 256), 100, dtype=np.uint16)  # under black (256)
    assert pr.develop(mosaic, header)[10, 10, 1] < 0
    mosaic[:] = 60000  # over white (30256)
    assert pr.develop(mosaic, header)[10, 10].max() > 1.5


def test_develop_crop_and_half_sizes():
    header = pr.parse_frame_header(encode_frame(_dc(1, 1, 1), _header(crop=(2, 4, 0, 2))))
    mosaic = np.full((64, 256), 5000, dtype=np.uint16)
    assert pr.develop(mosaic, header).shape == (62, 250, 3)
    assert pr.develop(mosaic, header, half=True).shape == (31, 125, 3)


def test_bad_matrix_falls_back_with_a_warning():
    header = pr.parse_frame_header(encode_frame(_dc(1, 1, 1), _header(matrix=(0.0,) * 9)))
    warnings = pr.header_warnings(header)
    assert any("no usable colour matrix" in w for w in warnings)
    mosaic = np.full((64, 256), 20000, dtype=np.uint16)
    rgb = pr.develop(mosaic, header)[10, 10]
    assert np.all(np.isfinite(rgb))
    assert rgb[0] == pytest.approx(rgb[1], rel=1e-3)  # Rec.709 fallback keeps neutral


def test_off_white_matrix_warns():
    skewed = (0.6, 0.3, 0.1, 0.3, 0.6, 0.1, 0.0, 0.1, 0.3)
    header = pr.parse_frame_header(encode_frame(_dc(1, 1, 1), _header(matrix=skewed)))
    assert any("from D65" in w for w in pr.header_warnings(header))
    good = pr.parse_frame_header(encode_frame(_dc(1, 1, 1), _header()))
    assert pr.header_warnings(good) == []


# ---------------------------------------------------------------------------
# Clip, sources, dispatch
# ---------------------------------------------------------------------------


@needs_decoder
def test_clip_probe_random_access_and_sequential(tmp_path):
    frames = [_dc(300 + 20 * k, 400, 350) for k in range(5)]
    path, _ = _clip(tmp_path, frames, crop=(2, 4, 0, 2))
    with pr.ProResRawClip(str(path)) as clip:
        info = clip.info
        assert (info.width, info.height) == (250, 62)
        assert (info.sensor_width, info.sensor_height) == (256, 64)
        assert info.frame_count == 5 and info.fps == pytest.approx(24.0)
        by_seek = {i: clip.read_mosaic(i)[0][0, 0] for i in (4, 1, 5, 2, 3)}
        by_seq = {i: m[0, 0] for i, m, _h in clip.iter_mosaics()}
        assert by_seek == by_seq
        assert sorted(by_seq.values()) == [by_seq[i] for i in range(1, 6)]
        assert [i for i, _m, _h in clip.iter_mosaics({2, 4})] == [2, 4]
        with pytest.raises(IndexError):
            clip.read_mosaic(6)


@needs_decoder
def test_dispatch_routes_prores_raw(tmp_path):
    from src.core.frame_source import open_ingest_source, open_preview_decoder
    from src.core.source_plugins import plugin_for_path
    from src.core.video import (
        guess_video_colorspace_candidates,
        is_libav_unsafe_media,
        list_video_files,
        probe_video,
        probe_video_metadata,
        probe_video_row,
    )

    path, _ = _clip(tmp_path, [_dc(300, 400, 350)] * 3, crop=(2, 4, 0, 2))
    p = str(path)
    assert isinstance(plugin_for_path(p), pr.ProResRawSource)
    assert plugin_for_path(p, builtin=False) is None
    assert is_libav_unsafe_media(p)
    assert probe_video(p) == (250, 62, 24.0, 3)
    assert guess_video_colorspace_candidates(p)[0] == "ACES2065-1"
    row = probe_video_row(p)
    assert row["codec"] == "ProRes RAW" and row["resolution"] == "250x62"
    meta = probe_video_metadata(p)
    assert meta["Video codec"] == "ProRes RAW"
    assert meta["Recorder"] == "Atomos"
    assert "error" not in meta
    assert [r["name"] for r in list_video_files(str(tmp_path))] == ["A001C001.mov"]

    with open_ingest_source(p) as src:
        assert src.info.kind == "prores_raw"
        assert src.output_size == (250, 62)
        got = list(src.iter_frames({1, 3}))
    assert [g[0] for g in got] == [1, 3]
    assert got[0][1].shape == (62, 250, 3) and got[0][1].dtype == np.float32
    assert got[0][2]["exrconverter:proresraw:colorspace"] == "ACES2065-1"

    with open_ingest_source(p, scale=0.5) as src:
        assert src.output_size == (124, 30)
        half = next(iter(src.iter_frames()))[1]
    assert half.shape == (30, 124, 3)

    dec = open_preview_decoder(p)
    try:
        frame = dec.get_frame(2)
        assert frame is not None and (frame.shape[1], frame.shape[0]) == dec.decode_size
    finally:
        dec.close()


@needs_decoder
def test_thumbnail_is_display_range(tmp_path):
    path, _ = _clip(tmp_path)
    rgb = pr.get_source_plugin().thumbnail_rgb(str(path))
    assert rgb is not None and rgb.min() >= 0.0 and rgb.max() <= 1.0


@pytest.mark.integration
@needs_decoder
def test_cli_video2exr_writes_developed_aces(tmp_path):
    import OpenImageIO as oiio

    from src.core.ocio_utils import get_bundled_aces_studio_path
    from tests.support.integration import run_converter

    bundled = get_bundled_aces_studio_path()
    if bundled is None:
        pytest.skip("bundled OCIO config missing")
    path, _ = _clip(tmp_path, [_dc(300, 400, 350), _dc(320, 410, 360)], wb_red=1.8, wb_blue=1.4)
    out = tmp_path / "exr"
    result = run_converter(
        "--workers",
        "1",
        "video2exr",
        "--ocio",
        str(bundled),
        "--dst",
        "ACES2065-1",
        "-i",
        str(path),
        "-o",
        str(out),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    log_text = result.stdout + result.stderr
    assert "Auto-detected source color space: ACES2065-1" in log_text
    assert "16-bit linear" in log_text
    exrs = sorted(out.glob("*.exr"))
    assert len(exrs) == 2

    inp = oiio.ImageInput.open(str(exrs[1]))
    spec = inp.spec()
    pixels = inp.read_image(oiio.FLOAT)
    inp.close()
    assert pixels is not None
    assert (spec.width, spec.height) == (256, 64)
    assert spec.getattribute("exrconverter:proresraw:wb_red") == "1.8"
    with pr.ProResRawClip(str(path)) as clip:
        expected, _ = clip.decode_frame(2)
    np.testing.assert_allclose(pixels[10:-10, 10:-10], expected[10:-10, 10:-10], rtol=2e-3)


def test_demosaic_matches_cinemadng_bilinear_inside_the_frame():
    from src.core.cinemadng.color import demosaic_bilinear

    raw = np.random.default_rng(1).random((32, 48)).astype(np.float32)
    fast = pr.demosaic_rggb(raw)
    ref = demosaic_bilinear(raw, (0, 1, 1, 2))
    np.testing.assert_allclose(fast[2:-2, 2:-2], ref[2:-2, 2:-2], rtol=1e-6, atol=1e-7)
    np.testing.assert_array_equal(fast[0::2, 0::2, 0], raw[0::2, 0::2])
