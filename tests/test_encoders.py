"""Encoder selection, threading, and hardware fallback (no GPU required)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import PyOpenColorIO as OCIO
import pytest
from PySide6.QtCore import QSettings

from src.core.constants import ENCODER_AUTO, ENCODER_SOFTWARE
from src.core.convert import _ordered_results
from src.core.encoders import (
    EncoderChoice,
    EncoderFailed,
    configure_threads,
    hardware_family,
    hardware_options,
    pick_encoder,
    run_with_fallback,
)
from src.core.errors import ConversionCancelled
from src.core.ocio_utils import apply_packed_rgb, get_app_anchor_config
from src.gui.preferences import encoder_mode
from src.services.app_settings import Keys


@pytest.mark.parametrize(
    ("codec", "expected"),
    [("prores_ks", "FRAME"), ("cfhd", "FRAME"), ("dnxhd", None), ("ffv1", None)],
)
def test_configure_threads_only_frame_threads_where_faster(codec: str, expected) -> None:
    stream = SimpleNamespace(thread_type=None)
    configure_threads(stream, codec)
    assert stream.thread_type == expected


def test_hardware_family_is_8_and_10_bit_h264_hevc_av1_only() -> None:
    assert hardware_family("h264", 8) == "h264"
    assert hardware_family("hevc", 10) == "hevc"
    assert hardware_family("hevc_8", 8) == "hevc"
    assert hardware_family("hevc_12", 12) == ""
    assert hardware_family("av1", 10) == "av1"
    assert hardware_family("av1", 12) == ""
    assert hardware_family("prores_hq", 10) == ""


def test_hardware_options_constant_quality() -> None:
    nv = hardware_options("nvenc", crf=20, preset="slow", bit_rate=0)
    assert nv == {"preset": "p6", "tune": "hq", "rc": "vbr", "cq": "18"}
    assert hardware_options("qsv", crf=20, preset="fast", bit_rate=0)["global_quality"] == "18"
    amf = hardware_options("amf", crf=20, preset="medium", bit_rate=0)
    assert amf["rc"] == "cqp" and amf["qp_i"] == amf["qp_p"] == "18"
    vt = hardware_options("videotoolbox", crf=20, preset="medium", bit_rate=0)
    assert vt["flags"] == "+qscale" and int(vt["global_quality"]) == 60 * 118


def test_hardware_options_media_foundation_requires_hardware() -> None:
    mf = hardware_options("mf", crf=20, preset="medium", bit_rate=0)
    assert mf == {"hw_encoding": "1", "rate_control": "quality", "quality": "60"}
    assert hardware_options("mf", crf=20, preset="medium", bit_rate=8_000_000) == {
        "hw_encoding": "1"
    }


def test_av1_skips_videotoolbox_and_ends_with_media_foundation(monkeypatch) -> None:
    from src.core import encoders

    tried: list[str] = []
    monkeypatch.setattr(
        encoders, "encoder_opens", lambda c, w, h: tried.append(c.libav_codec) or False
    )
    sw = EncoderChoice("libsvtav1", "yuv420p10le", {"crf": "28"})
    kw = {"codec_key": "av1", "bit_depth": 10, "crf": 28, "preset": "medium"}
    monkeypatch.setattr(encoders.sys, "platform", "darwin")
    assert pick_encoder(sw, width=64, height=64, **kw) is sw
    assert tried == []
    monkeypatch.setattr(encoders.sys, "platform", "win32")
    assert pick_encoder(sw, width=64, height=64, **kw) is sw
    assert tried == ["av1_nvenc", "av1_qsv", "av1_amf", "av1_mf"]


def test_hardware_options_bitrate_drops_quality_target() -> None:
    assert "cq" not in hardware_options("nvenc", crf=20, preset="medium", bit_rate=10_000_000)
    assert hardware_options("videotoolbox", crf=20, preset="medium", bit_rate=10_000_000) == {}
    with pytest.raises(ValueError):
        hardware_options("cuda", crf=20, preset="medium", bit_rate=0)


def test_pick_encoder_software_mode_and_unsupported_codecs_stay_software() -> None:
    sw = EncoderChoice("libx265", "yuv420p10le", {"crf": "18"})
    kw = {"crf": 18, "preset": "medium", "width": 64, "height": 64}
    assert pick_encoder(sw, codec_key="hevc", bit_depth=10, mode=ENCODER_SOFTWARE, **kw) is sw
    assert pick_encoder(sw, codec_key="hevc_12", bit_depth=12, mode=ENCODER_AUTO, **kw) is sw


def test_pick_encoder_hardware_choice_falls_back_to_software() -> None:
    sw = EncoderChoice("libx264", "yuv420p", {"crf": "18"})
    choice = pick_encoder(
        sw, codec_key="h264", bit_depth=8, crf=18, preset="medium", width=256, height=256
    )
    if choice.hardware:
        assert choice.pix_fmt == "nv12"
        assert choice.fallback is sw
    else:
        assert choice is sw


def test_run_with_fallback_retries_once_with_software() -> None:
    sw = EncoderChoice("libx264", "yuv420p")
    hw = EncoderChoice("h264_nvenc", "nv12", backend="nvenc", fallback=sw)
    tried: list[str] = []
    logs: list[str] = []

    def attempt(choice: EncoderChoice) -> str:
        tried.append(choice.libav_codec)
        if choice.hardware:
            raise EncoderFailed("h264_nvenc could not open")
        return "ok"

    assert run_with_fallback(hw, attempt, logs.append) == "ok"
    assert tried == ["h264_nvenc", "libx264"]
    assert "libx264" in logs[0]


def test_run_with_fallback_reraises_without_fallback() -> None:
    def attempt(_choice: EncoderChoice) -> None:
        raise EncoderFailed("broken")

    with pytest.raises(EncoderFailed):
        run_with_fallback(EncoderChoice("libx264", "yuv420p"), attempt)


def test_apply_packed_rgb_strips_match_single_apply() -> None:
    cfg = get_app_anchor_config()
    proc = cfg.getProcessor("ACEScg", "ACEScct").getDefaultCPUProcessor()
    rng = np.random.default_rng(3)
    src = (rng.random((517, 301, 3), dtype=np.float32) * 4).astype(np.float32)
    whole = src.copy()
    proc.apply(OCIO.PackedImageDesc(whole, 301, 517, 3))
    strips = src.copy()
    apply_packed_rgb(proc, strips)
    assert np.array_equal(whole, strips)


def _slow_square(i: int) -> tuple[int, np.ndarray]:
    return i, np.full(4, i * i)


def test_ordered_results_keeps_job_order() -> None:
    with ThreadPoolExecutor(4) as pool:
        out = list(
            _ordered_results(
                pool,
                _slow_square,
                ((i,) for i in range(20)),
                max_inflight=6,
                item_bytes=32,
                cancel_check=None,
            )
        )
    assert [int(a[0]) for a in out] == [i * i for i in range(20)]


def test_ordered_results_cancel() -> None:
    with ThreadPoolExecutor(2) as pool:
        with pytest.raises(ConversionCancelled):
            list(
                _ordered_results(
                    pool,
                    _slow_square,
                    ((i,) for i in range(20)),
                    max_inflight=2,
                    item_bytes=32,
                    cancel_check=lambda: True,
                )
            )


def test_encoder_mode_pref_rejects_unknown_values(tmp_path) -> None:
    s = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    assert encoder_mode(s) == ENCODER_AUTO
    s.setValue(Keys.ENCODER_MODE, ENCODER_SOFTWARE)
    assert encoder_mode(s) == ENCODER_SOFTWARE
    s.setValue(Keys.ENCODER_MODE, "cuda")
    assert encoder_mode(s) == ENCODER_AUTO
