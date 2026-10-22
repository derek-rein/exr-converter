"""Transcode engine (Transcode tab, ``transcode`` CLI): presets, naming, and encodes."""

from __future__ import annotations

import datetime
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from src.cli import build_parser, run_cli
from src.core.errors import ConversionCancelled
from src.core.transcode import (
    AUDIO_AAC,
    AUDIO_COPY,
    AUDIO_NONE,
    AUDIO_PCM,
    AUDIO_PCM16,
    OVERWRITE_RENAME,
    OVERWRITE_REPLACE,
    OVERWRITE_SKIP,
    RATE_BITRATE,
    RES_CUSTOM,
    TranscodeSettings,
    _timecode_for_rate,
    audio_modes_for_container,
    available_transcode_codecs,
    collect_transcode_sources,
    containers_for_codec,
    encoder_available,
    encoder_options,
    format_output_name,
    is_transcode_source,
    plan_output,
    resolve_audio,
    resolve_container,
    run_transcode,
    transcode_dims,
    validate_settings,
)
from src.core.trustmark.embed import FrameWatermark


def _write_clip(
    path: Path,
    *,
    frames: int = 12,
    width: int = 128,
    height: int = 72,
    timecode: str = "",
    audio: bool = True,
) -> None:
    """Tiny H.264 clip (moving gradient) with optional AAC audio and timecode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = av.open(str(path), mode="w")
    if timecode:
        c.metadata["timecode"] = timecode
    v = c.add_stream("libx264", rate=24)
    v.width, v.height, v.pix_fmt = width, height, "yuv420p"
    v.options = {"preset": "ultrafast"}
    a = None
    if audio:
        a = c.add_stream("aac", rate=48000)
        a.layout = "stereo"
    ramp = np.linspace(0, 255, width, dtype=np.float32)
    for i in range(frames):
        img = np.zeros((height, width, 3), dtype=np.uint8)
        img[..., 0] = ((ramp + i * 8) % 256).astype(np.uint8)
        img[..., 1] = 120
        img[..., 2] = (255 - ramp).astype(np.uint8)
        for p in v.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            c.mux(p)
    if a is not None:
        pts = 0
        while pts < 48000 * frames // 24:
            arr = np.zeros((2, 1024), dtype=np.float32)
            f = av.AudioFrame.from_ndarray(arr, format="fltp", layout="stereo")
            f.sample_rate = 48000
            f.pts = pts
            pts += 1024
            for p in a.encode(f):
                c.mux(p)
    for s in (v, a):
        if s is None:
            continue
        for p in s.encode():
            c.mux(p)
    c.close()


class TestCodecs:
    def test_vfx_codec_ladder_is_offered(self):
        keys = {s.key for s in available_transcode_codecs()}
        for k in (
            "prores_proxy",
            "prores_lt",
            "prores_422",
            "prores",
            "prores_4444",
            "prores_xq",
            "dnxhr_lb",
            "dnxhr_sq",
            "dnxhr_hq",
            "dnxhr_hqx",
            "dnxhr_444",
            "h264",
            "hevc_8",
            "hevc",
        ):
            assert k in keys
        assert ("av1" in keys) == encoder_available("libsvtav1")
        assert not any(k.startswith("prores_ox_") for k in keys)

    def test_containers_follow_codec(self):
        assert containers_for_codec("prores") == ("mov",)
        assert containers_for_codec("dnxhr_hqx") == ("mov", "mxf")
        assert containers_for_codec("h264") == ("mp4", "mov")
        assert containers_for_codec("av1") == ("mp4", "mkv")
        assert resolve_container("prores", "mp4") == "mov"
        assert resolve_container("dnxhr_sq", ".MXF") == "mxf"
        assert resolve_container("hevc") == "mp4"

    def test_encoder_options_intra_profiles(self):
        opts, rate = encoder_options(TranscodeSettings(codec_key="prores"))
        assert opts["profile"] == "3" and rate == 0
        assert encoder_options(TranscodeSettings(codec_key="prores_xq"))[0]["profile"] == "5"
        opts, _ = encoder_options(TranscodeSettings(codec_key="dnxhr_444"))
        assert opts == {"profile": "dnxhr_444"}

    def test_encoder_options_long_gop(self):
        opts, rate = encoder_options(TranscodeSettings(codec_key="h264"))
        assert opts["crf"] == "18" and opts["preset"] == "medium" and rate == 0
        opts, _ = encoder_options(TranscodeSettings(codec_key="h264", crf=23, preset="slow"))
        assert (opts["crf"], opts["preset"]) == ("23", "slow")
        opts, rate = encoder_options(
            TranscodeSettings(codec_key="hevc", rate_control=RATE_BITRATE, bitrate_mbps=12.5)
        )
        assert "crf" not in opts and rate == 12_500_000
        assert opts["x265-params"] == "log-level=error"
        opts, _ = encoder_options(TranscodeSettings(codec_key="av1", preset="veryslow"))
        assert opts["preset"] == "2" and opts["crf"] == "28"
        opts, _ = encoder_options(TranscodeSettings(codec_key="h264", codec_opts={"tune": "film"}))
        assert opts["tune"] == "film"


class TestValidate:
    def test_resolves_default_container(self):
        assert validate_settings(TranscodeSettings(codec_key="h264")).container == "mp4"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"codec_key": "nope"},
            {"codec_key": "prores_ox_hq"},
            {"codec_key": "prores", "container": "mp4"},
            {"resolution": RES_CUSTOM},
            {"resolution": "huge"},
            {"fps": -1},
            {"audio": "mp3"},
            {"codec_key": "h264", "crf": 60},
            {"codec_key": "h264", "rate_control": RATE_BITRATE, "bitrate_mbps": 0},
            {"codec_key": "h264", "preset": "warp"},
            {"color_src": "ACEScg"},
        ],
    )
    def test_rejects(self, kwargs):
        with pytest.raises(ValueError):
            validate_settings(TranscodeSettings(**kwargs))

    def test_av1_crf_range_is_wider(self):
        validate_settings(TranscodeSettings(codec_key="av1", crf=55))

    def test_color_is_off_unless_both_spaces(self):
        assert not TranscodeSettings().color_enabled
        assert not TranscodeSettings(color_src="A", color_dst="A").color_enabled
        assert TranscodeSettings(color_src="A", color_dst="B").color_enabled


class TestDims:
    def test_keep_and_presets_never_upscale(self):
        assert transcode_dims(3840, 2160) == (3840, 2160)
        assert transcode_dims(3840, 2160, "half") == (1920, 1080)
        assert transcode_dims(3840, 2160, "75") == (2880, 1620)
        assert transcode_dims(4096, 2160, "1080") == (2048, 1080)
        assert transcode_dims(1280, 720, "2160") == (1280, 720)

    def test_odd_sizes_round_to_even(self):
        w, h = transcode_dims(1001, 501)
        assert w % 2 == 0 and h % 2 == 0

    def test_custom_exact_or_aspect(self):
        assert transcode_dims(1920, 1080, RES_CUSTOM, 2048, 858) == (2048, 858)
        assert transcode_dims(1920, 1080, RES_CUSTOM, 1280, 0) == (1280, 720)
        assert transcode_dims(1920, 1080, RES_CUSTOM, 0, 540) == (960, 540)
        assert transcode_dims(1920, 1080, RES_CUSTOM, 3840, 0) == (3840, 2160)


class TestAudio:
    def test_modes_per_container(self):
        assert AUDIO_PCM not in audio_modes_for_container("mp4")
        assert AUDIO_AAC not in audio_modes_for_container("mxf")
        assert set(audio_modes_for_container("mov")) == {
            AUDIO_COPY,
            AUDIO_AAC,
            AUDIO_PCM,
            AUDIO_NONE,
        }

    def test_resolve(self):
        assert resolve_audio(AUDIO_COPY, "mov", "aac") == (AUDIO_COPY, "")
        assert resolve_audio(AUDIO_COPY, "mp4", "pcm_s24le")[0] == "aac"
        assert resolve_audio(AUDIO_COPY, "mxf", "aac")[0] == "pcm_s24le"
        assert resolve_audio(AUDIO_AAC, "mxf")[0] == "pcm_s24le"
        assert resolve_audio(AUDIO_PCM, "mov") == ("pcm_s24le", "")
        assert resolve_audio(AUDIO_PCM16, "mov") == ("pcm_s16le", "")
        assert resolve_audio(AUDIO_PCM, "mp4")[0] == "aac"
        assert resolve_audio(AUDIO_NONE, "mov") == (AUDIO_NONE, "")


class TestNaming:
    def test_tokens(self):
        name = format_output_name(
            "<name>_<codec>_<width>x<height>_<fps>_<date>",
            "/a/A001C003.mxf",
            codec_key="prores",
            width=1920,
            height=1080,
            fps=23.976,
            today=datetime.date(2026, 10, 21),
        )
        assert name == "A001C003_ProRes422HQ_1920x1080_23.976_2026-10-21"
        assert format_output_name("", "x.mov", codec_key="h264") == "x_H264"

    def test_unknown_token_and_bad_chars(self):
        with pytest.raises(ValueError, match="Unknown name token"):
            format_output_name("<shot>", "x.mov", codec_key="h264")
        assert format_output_name("a:b/<name>", "x.mov", codec_key="h264") == "a_b_x"

    def test_plan_output_locations_and_overwrite(self, tmp_path):
        src = tmp_path / "clip.mov"
        src.write_bytes(b"x")
        s = TranscodeSettings(codec_key="h264")
        out = plan_output(src, s)
        assert out == tmp_path / "clip_H264.mp4"
        assert plan_output(src, s, output_dir=tmp_path / "o") == tmp_path / "o" / "clip_H264.mp4"
        out.write_bytes(b"x")
        assert plan_output(src, s, overwrite=OVERWRITE_SKIP) is None
        assert plan_output(src, s, overwrite=OVERWRITE_REPLACE) == out
        assert plan_output(src, s, overwrite=OVERWRITE_RENAME) == tmp_path / "clip_H264_1.mp4"
        taken = {str(tmp_path / "clip_H264_1.mp4")}
        assert (
            plan_output(src, s, overwrite=OVERWRITE_RENAME, taken=taken)
            == tmp_path / "clip_H264_2.mp4"
        )

    def test_plan_output_never_replaces_source(self, tmp_path):
        src = tmp_path / "clip.mov"
        src.write_bytes(b"x")
        s = TranscodeSettings(codec_key="prores")
        with pytest.raises(ValueError, match="replace the source"):
            plan_output(src, s, pattern="<name>", overwrite=OVERWRITE_REPLACE)
        assert plan_output(src, s, pattern="<name>", overwrite=OVERWRITE_RENAME) == (
            tmp_path / "clip_1.mov"
        )

    def test_timecode_drop_frame_only_at_ntsc_rates(self):
        assert _timecode_for_rate("01:00:00;00", Fraction(30000, 1001)) == "01:00:00;00"
        assert _timecode_for_rate("01:00:00;00", Fraction(24)) == "01:00:00:00"
        assert _timecode_for_rate("01:00:00:00", Fraction(25)) == "01:00:00:00"


class TestSources:
    def test_sources(self, tmp_path):
        for name in ("a.mov", "b.MXF", "c.mp4", ".hidden.mov", "._a.mov", "notes.txt", "d.ari"):
            (tmp_path / name).write_bytes(b"x")
        found = [p.name for p in collect_transcode_sources([tmp_path])]
        assert found == ["a.mov", "b.MXF", "c.mp4"]
        assert not is_transcode_source(tmp_path / "d.ari")


class TestWatermarkU16:
    def test_mark_is_added_at_16_bit(self):
        class _Fake(FrameWatermark):
            def __init__(self) -> None:  # no model download
                pass

            def apply(self, rgb):
                out = rgb.astype(np.int16)
                out[0, 0, 0] += 3
                return np.clip(out, 0, 255).astype(np.uint8)

        rgb = np.full((4, 4, 3), 1000, dtype=np.uint16)
        out = _Fake().apply_u16(rgb)
        assert out.dtype == np.uint16
        assert int(out[0, 0, 0]) == 1000 + 3 * 257
        assert np.array_equal(out[1:], rgb[1:])
        with pytest.raises(ValueError):
            _Fake().apply_u16(rgb.astype(np.uint8))


class TestCliArgs:
    def test_parses(self):
        args = build_parser().parse_args(
            ["transcode", "-i", "a.mov", "--codec", "h264", "--crf", "20", "--if-exists", "rename"]
        )
        assert (args.command, args.codec, args.crf, args.if_exists) == (
            "transcode",
            "h264",
            20,
            "rename",
        )

    def test_src_needs_dst(self, tmp_path):
        args = build_parser().parse_args(["transcode", "-i", str(tmp_path), "--src", "ACEScg"])
        assert run_cli(args) == 2

    def test_bad_watermark(self, tmp_path):
        args = build_parser().parse_args(
            ["transcode", "-i", str(tmp_path), "--watermark", "toolong!!"]
        )
        assert run_cli(args) == 2

    def test_bad_container(self, tmp_path):
        args = build_parser().parse_args(
            ["transcode", "-i", str(tmp_path), "--codec", "prores", "--container", "mp4"]
        )
        assert run_cli(args) == 2

    def test_gui_mode_flag(self):
        assert build_parser().parse_args(["--mode", "transcode"]).mode == "transcode"


def _probe(path: Path) -> dict:
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        frames = sum(1 for _ in c.decode(v))
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        return {
            "codec": v.codec_context.name,
            "profile": v.codec_context.profile,
            "size": (v.width, v.height),
            "pix_fmt": v.codec_context.pix_fmt,
            "rate": v.average_rate,
            "frames": frames,
            "audio": [a.codec_context.name for a in c.streams.audio],
            "timecode": v.metadata.get("timecode") or c.metadata.get("timecode"),
        }


@pytest.mark.integration
class TestTranscodeEncode:
    def test_prores_hq_keeps_audio_timecode_and_frames(self, tmp_path):
        src = tmp_path / "A001.mov"
        _write_clip(src, timecode="01:00:00:00")
        out = plan_output(src, TranscodeSettings())
        assert out is not None and out.name == "A001_ProRes422HQ.mov"
        run_transcode(src, out, TranscodeSettings())
        info = _probe(out)
        assert info["codec"] == "prores" and info["profile"] == "HQ"
        assert info["pix_fmt"] == "yuv422p10le"
        assert info["size"] == (128, 72) and info["frames"] == 12
        assert info["audio"] == ["aac"]  # copied as is
        assert info["timecode"] == "01:00:00:00"
        assert not list(tmp_path.glob(".*partial*"))

    def test_h264_mp4_half_size_with_aac(self, tmp_path):
        src = tmp_path / "clip.mov"
        _write_clip(src)
        s = TranscodeSettings(codec_key="h264", resolution="half", audio=AUDIO_AAC, crf=20)
        out = run_transcode(src, tmp_path / "clip.mp4", s)
        info = _probe(out)
        assert info["codec"] == "h264" and info["size"] == (64, 36)
        assert info["audio"] == ["aac"] and info["frames"] == 12

    def test_dnxhr_mxf_with_pcm(self, tmp_path):
        src = tmp_path / "clip.mov"
        _write_clip(src, width=1280, height=720, frames=3)
        s = TranscodeSettings(codec_key="dnxhr_sq", container="mxf")
        out = run_transcode(src, tmp_path / "clip.mxf", s)
        info = _probe(out)
        assert info["codec"] == "dnxhd" and info["audio"] == ["pcm_s24le"]

    def test_conform_changes_rate_and_drops_audio(self, tmp_path):
        src = tmp_path / "clip.mov"
        _write_clip(src)
        s = TranscodeSettings(codec_key="prores_proxy", fps=25.0)
        out = run_transcode(src, tmp_path / "out.mov", s)
        info = _probe(out)
        assert info["rate"] == 25 and info["frames"] == 12 and info["audio"] == []

    def test_no_audio_option(self, tmp_path):
        src = tmp_path / "clip.mov"
        _write_clip(src)
        out = run_transcode(
            src, tmp_path / "o.mov", TranscodeSettings(codec_key="prores_lt", audio=AUDIO_NONE)
        )
        assert _probe(out)["audio"] == []

    def test_ocio_transform_changes_pixels(self, tmp_path):
        from src.cli import _resolve_config_source

        src = tmp_path / "clip.mov"
        _write_clip(src, audio=False, frames=2)
        cs, cp = _resolve_config_source(None)
        plain = run_transcode(src, tmp_path / "plain.mov", TranscodeSettings(codec_key="prores"))
        graded = run_transcode(
            src,
            tmp_path / "graded.mov",
            TranscodeSettings(
                codec_key="prores",
                color_src="sRGB Encoded Rec.709 (sRGB)",
                color_dst="ACEScct",
                config_source=cs,
                config_path=cp,
            ),
        )

        def _first(p: Path) -> np.ndarray:
            with av.open(str(p)) as c:
                return next(c.decode(video=0)).to_ndarray(format="rgb48le").astype(np.float64)

        assert np.abs(_first(plain) - _first(graded)).mean() > 1000

    def test_cancel_leaves_nothing(self, tmp_path):
        src = tmp_path / "clip.mov"
        _write_clip(src)
        out = tmp_path / "o.mov"
        with pytest.raises(ConversionCancelled):
            run_transcode(src, out, TranscodeSettings(), cancel_check=lambda: True)
        assert not out.exists()
        assert not list(tmp_path.glob(".*partial*"))

    @pytest.mark.skipif(not encoder_available("libsvtav1"), reason="no SVT-AV1 in this FFmpeg")
    def test_av1(self, tmp_path):
        src = tmp_path / "clip.mov"
        _write_clip(src, frames=4, audio=False)
        s = TranscodeSettings(codec_key="av1", preset="ultrafast")
        out = run_transcode(src, tmp_path / "o.mp4", s)
        assert _probe(out)["codec"] in ("libdav1d", "av1", "libaom-av1")

    def test_cli_batch_naming_and_skip(self, tmp_path):
        _write_clip(tmp_path / "in" / "a.mov", frames=3)
        out_dir = tmp_path / "out"
        argv = ["transcode", "-i", str(tmp_path / "in"), "-o", str(out_dir), "--codec", "h264"]
        assert run_cli(build_parser().parse_args(argv)) == 0
        made = out_dir / "a_H264.mp4"
        assert made.is_file()
        mtime = made.stat().st_mtime_ns
        assert run_cli(build_parser().parse_args(argv)) == 0
        assert made.stat().st_mtime_ns == mtime
        assert run_cli(build_parser().parse_args([*argv, "--if-exists", "rename"])) == 0
        assert (out_dir / "a_H264_1.mp4").is_file()
