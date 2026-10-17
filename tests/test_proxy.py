"""Proxy generation (Video → Proxy tab, ``proxy`` CLI) and the watch folder."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import pytest

from src.cli import build_parser, run_cli
from src.core.proxy import (
    DEFAULT_PROXY_CODEC,
    PROXY_SUBFOLDER,
    available_proxy_codecs,
    check_watch_output,
    collect_proxy_sources,
    default_proxy_output,
    is_proxy_source,
    proxy_dims,
    run_video_to_proxy,
)
from src.core.watch_folder import WatchFolder
from tests.support.integration import write_synthetic_video


def _write_clip_with_audio(path: Path, *, frames: int = 12, timecode: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = av.open(str(path), mode="w")
    if timecode:
        c.metadata["timecode"] = timecode
    v = c.add_stream("libx264", rate=24)
    v.width, v.height, v.pix_fmt = 128, 72, "yuv420p"
    v.options = {"preset": "ultrafast"}
    a = c.add_stream("aac", rate=48000)
    a.layout = "stereo"
    for i in range(frames):
        img = np.full((72, 128, 3), i * 10, dtype=np.uint8)
        for p in v.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            c.mux(p)
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
        for p in s.encode():
            c.mux(p)
    c.close()


class TestProxyHelpers:
    def test_dims_scale_and_fit_never_upscale(self):
        assert proxy_dims(3840, 2160, "half") == (1920, 1080)
        assert proxy_dims(3840, 2160, "quarter") == (960, 540)
        assert proxy_dims(4096, 2160, "1080") == (2048, 1080)
        assert proxy_dims(1280, 720, "1080") == (1280, 720)
        assert proxy_dims(1920, 1080, "original") == (1920, 1080)
        w, h = proxy_dims(1001, 501, "half")
        assert w % 2 == 0 and h % 2 == 0

    def test_default_output_is_resolve_proxy_folder(self, tmp_path):
        src = tmp_path / "A001C003.mxf"
        assert default_proxy_output(src) == tmp_path / PROXY_SUBFOLDER / "A001C003.mov"
        assert default_proxy_output(src, "h264").suffix == ".mp4"
        out = default_proxy_output(src, output_dir=tmp_path / "px", suffix="_proxy")
        assert out == tmp_path / "px" / "A001C003_proxy.mov"

    def test_sources_skip_hidden_raw_and_proxy_folder(self, tmp_path):
        for name in ("a.mov", "b.MP4", ".hidden.mov", "._a.mov", "c.r3d", "notes.txt"):
            (tmp_path / name).write_bytes(b"x")
        (tmp_path / PROXY_SUBFOLDER).mkdir()
        (tmp_path / PROXY_SUBFOLDER / "a.mov").write_bytes(b"x")
        (tmp_path / "day2").mkdir()
        (tmp_path / "day2" / "d.mov").write_bytes(b"x")
        flat = [p.name for p in collect_proxy_sources([tmp_path])]
        assert flat == ["a.mov", "b.MP4"]
        deep = [p.name for p in collect_proxy_sources([tmp_path], recursive=True)]
        assert deep == ["a.mov", "b.MP4", "d.mov"]
        assert not is_proxy_source(tmp_path / "c.r3d")

    def test_watch_output_cannot_be_watch_folder(self, tmp_path):
        check_watch_output(tmp_path, None)
        check_watch_output(tmp_path, tmp_path / "out")
        with pytest.raises(ValueError):
            check_watch_output(tmp_path, tmp_path)

    def test_codec_list_defaults_to_prores_proxy(self):
        keys = [c.key for c in available_proxy_codecs()]
        assert keys[0] == DEFAULT_PROXY_CODEC
        assert "h264" in keys and "dnxhr_lb" in keys
        assert not any(k.startswith("prores_ox") for k in keys)


# Big enough that no filesystem stores it inline (0 blocks reads as a cloud stub).
_DATA = bytes(16384)


class TestWatchFolder:
    def test_waits_for_file_to_settle_then_reports_once(self, tmp_path):
        now = [0.0]
        (tmp_path / "old.mov").write_bytes(_DATA)
        w = WatchFolder(tmp_path, settle_seconds=3.0, clock=lambda: now[0])
        assert w.poll() == []  # existing clip ignored

        clip = tmp_path / "new.mov"
        clip.write_bytes(_DATA)
        assert w.poll() == []
        assert w.pending_count == 1
        now[0] = 2.0
        with clip.open("ab") as f:  # still copying: size changes, timer restarts
            f.write(_DATA)
        assert w.poll() == []
        now[0] = 4.0
        assert w.poll() == []
        now[0] = 5.5
        assert w.poll() == [clip]
        now[0] = 20.0
        assert w.poll() == []

    def test_include_existing_and_skips_proxy_folder(self, tmp_path):
        (tmp_path / "a.mov").write_bytes(_DATA)
        (tmp_path / PROXY_SUBFOLDER).mkdir()
        (tmp_path / PROXY_SUBFOLDER / "a.mov").write_bytes(_DATA)
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.mov").write_bytes(_DATA)
        w = WatchFolder(tmp_path, include_existing=True, recursive=True, settle_seconds=0)
        assert [p.name for p in w.poll()] == ["a.mov", "b.mov"]

    def test_excluded_output_dir_is_not_watched(self, tmp_path):
        out = tmp_path / "proxies"
        out.mkdir()
        w = WatchFolder(tmp_path, recursive=True, settle_seconds=0, exclude_dirs=(out,))
        (out / "x.mov").write_bytes(_DATA)
        assert w.poll() == []


class TestProxyCli:
    def test_parser(self):
        a = build_parser().parse_args(["proxy", "-i", "a.mov", "b"])
        assert a.command == "proxy"
        assert a.input == ["a.mov", "b"]
        assert a.codec == DEFAULT_PROXY_CODEC
        assert a.resolution == "half"
        assert a.watch == []
        assert a.watermark == ""
        assert a.watermark_strength == 1.0
        marked = build_parser().parse_args(
            ["proxy", "-i", "a.mov", "--watermark", "AB12", "--watermark-strength", "0.8"]
        )
        assert marked.watermark == "AB12"
        assert marked.watermark_strength == 0.8
        g = build_parser().parse_args(["--mode", "proxy"])
        assert g.mode == "proxy"

    def test_requires_input_or_watch(self):
        assert run_cli(build_parser().parse_args(["proxy"])) == 2

    def test_watermark_payload_must_fit_text_mode(self):
        too_long = build_parser().parse_args(["proxy", "-i", "a.mov", "--watermark", "toolong!!"])
        assert run_cli(too_long) == 2
        too_strong = build_parser().parse_args(
            ["proxy", "-i", "a.mov", "--watermark", "AB12", "--watermark-strength", "9"]
        )
        assert run_cli(too_strong) == 2


@pytest.mark.integration
class TestProxyEncode:
    def test_prores_proxy_keeps_audio_and_timecode(self, tmp_path):
        src = tmp_path / "A001.mov"
        _write_clip_with_audio(src, timecode="01:00:00:00")
        out = default_proxy_output(src)
        run_video_to_proxy(src, out)
        assert out.is_file()
        assert not list(out.parent.glob(".*partial*"))
        with av.open(str(out)) as c:
            v = c.streams.video[0]
            assert (v.codec_context.name, v.width, v.height) == ("prores", 64, 36)
            assert c.streams.audio, "audio should be carried into the proxy"
            assert v.metadata.get("timecode") == "01:00:00:00"
            assert sum(1 for _ in c.decode(v)) == 12

    def test_h264_without_audio(self, tmp_path):
        src = tmp_path / "clip.mov"
        _write_clip_with_audio(src)
        out = default_proxy_output(src, "h264")
        run_video_to_proxy(src, out, codec_key="h264", resolution="original", audio=False)
        with av.open(str(out)) as c:
            assert c.streams.video[0].width == 128
            assert not c.streams.audio

    def test_cli_batch_skips_existing(self, tmp_path):
        write_synthetic_video(tmp_path / "a.mp4", frames=3)
        args = build_parser().parse_args(["proxy", "-i", str(tmp_path)])
        assert run_cli(args) == 0
        proxy = tmp_path / PROXY_SUBFOLDER / "a.mov"
        assert proxy.is_file()
        mtime = proxy.stat().st_mtime_ns
        assert run_cli(args) == 0
        assert proxy.stat().st_mtime_ns == mtime
