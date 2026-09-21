"""Unit tests for optional Blackmagic RAW support (no proprietary SDK required)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from src.core.braw import (
    BRAW_SUFFIXES,
    DECODE_FULL,
    DECODE_HALF,
    DECODE_QUARTER,
    braw_src_colorspace_candidates,
    decode_mode_for_scale,
    is_braw_path,
    preferred_decoder_kinds,
)
from src.core.braw.paths import (
    MACOS_API_FRAMEWORK,
    _has_runtime_api,
    find_redistributable_dir,
)


def test_is_braw_path_extensions() -> None:
    assert is_braw_path("clip.braw")
    assert is_braw_path("clip.BRAW")
    assert is_braw_path(Path("/tmp/A001_C001_0101AB.braw"))
    assert not is_braw_path("plate.mov")
    assert not is_braw_path("seq.####.exr")
    assert not is_braw_path("clip.R3D")
    assert not is_braw_path("._clip.braw")
    assert not is_braw_path(Path("/tmp/._A001_C001.braw"))


def test_braw_suffixes_set() -> None:
    assert ".braw" in BRAW_SUFFIXES


def test_decode_mode_for_scale() -> None:
    assert decode_mode_for_scale(1.0) == DECODE_FULL
    assert decode_mode_for_scale(0.5) == DECODE_HALF
    assert decode_mode_for_scale(0.25) == DECODE_QUARTER


def test_preferred_decoder_kinds_order() -> None:
    kinds = preferred_decoder_kinds()
    assert kinds[-1] == "cpu"
    if sys.platform == "darwin":
        assert kinds == ("metal", "opencl", "cpu")
    else:
        assert kinds == ("cuda", "opencl", "cpu")


def test_decoder_kind_when_unavailable() -> None:
    from src.core.braw import native as native_mod

    prev = (
        native_mod._init_attempted,
        native_mod._init_ok,
        native_mod._init_error,
        native_mod._lib,
    )
    native_mod._init_attempted = True
    native_mod._init_ok = False
    native_mod._init_error = "test: bridge missing"
    native_mod._lib = None
    try:
        from src.core.braw import decoder_kind

        assert decoder_kind() == ""
    finally:
        (
            native_mod._init_attempted,
            native_mod._init_ok,
            native_mod._init_error,
            native_mod._lib,
        ) = prev


def test_src_colorspace_candidates_include_aces2065() -> None:
    cands = braw_src_colorspace_candidates("x.braw")
    assert any("ACES2065" in c or "lin_ap0" in c.lower() for c in cands)
    assert cands[0]


def test_bmd_notice_is_nonempty() -> None:
    from src.core.braw import BMD_REDISTRIBUTABLE_NOTICE

    assert "Blackmagic" in BMD_REDISTRIBUTABLE_NOTICE
    assert "reverse engineer" in BMD_REDISTRIBUTABLE_NOTICE.lower()


def test_bridge_candidates_include_exe_braw_dir(monkeypatch, tmp_path: Path) -> None:
    """Packaged apps place libbraw_bridge next to the binary under braw/."""
    from src.core import braw as braw_mod

    fake_exe = tmp_path / "MacOS" / "exr_converter"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_bytes(b"")
    bridge_name = braw_mod._bridge_names()[0]
    bridge = tmp_path / "MacOS" / "braw" / bridge_name
    bridge.parent.mkdir(parents=True)
    bridge.write_bytes(b"")

    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "argv", [str(fake_exe)])
    if hasattr(sys, "frozen"):
        monkeypatch.delattr(sys, "frozen", raising=False)

    cands = braw_mod._bridge_candidates()
    assert any(p.name == bridge_name and "braw" in p.parts for p in cands)
    assert bridge.resolve() in {p.resolve() for p in cands if p.exists()}


def test_bridge_candidates_include_macos_frameworks_braw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """macOS .app ships the bridge next to BlackmagicRawAPI.framework."""
    from src.core import braw as braw_mod

    fake_exe = tmp_path / "Contents" / "MacOS" / "exr_converter"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_bytes(b"")
    bridge_name = braw_mod._bridge_names()[0]
    bridge = tmp_path / "Contents" / "Frameworks" / "braw" / bridge_name
    bridge.parent.mkdir(parents=True)
    bridge.write_bytes(b"")

    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "argv", [str(fake_exe)])
    if hasattr(sys, "frozen"):
        monkeypatch.delattr(sys, "frozen", raising=False)

    cands = braw_mod._bridge_candidates()
    assert bridge.resolve() in {p.resolve() for p in cands if p.exists()}


def test_has_runtime_api_accepts_macos_framework(tmp_path: Path) -> None:
    folder = tmp_path / "braw"
    folder.mkdir()
    assert not _has_runtime_api(folder)
    (folder / MACOS_API_FRAMEWORK).mkdir()
    assert _has_runtime_api(folder)


def test_find_redistributable_dir_macos_framework(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_exe = tmp_path / "Contents" / "MacOS" / "exr_converter"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_bytes(b"")
    libs = tmp_path / "Contents" / "Frameworks" / "braw"
    (libs / MACOS_API_FRAMEWORK).mkdir(parents=True)
    monkeypatch.delenv("EXR_CONVERTER_BRAW_LIBS", raising=False)
    monkeypatch.delenv("BRAW_SDK_LIBS", raising=False)
    monkeypatch.delenv("BRAW_SDK_ROOT", raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "argv", [str(fake_exe)])
    found = find_redistributable_dir(libs / "libbraw_bridge.dylib")
    assert found is not None
    assert found.resolve() == libs.resolve()


def _force_braw_unavailable() -> tuple:
    from src.core.braw import native as native_mod

    prev = (
        native_mod._init_attempted,
        native_mod._init_ok,
        native_mod._init_error,
        native_mod._lib,
    )
    native_mod._init_attempted = True
    native_mod._init_ok = False
    native_mod._init_error = "test: bridge missing"
    native_mod._lib = None
    return prev


def _restore_braw_state(prev: tuple) -> None:
    from src.core.braw import native as native_mod

    (
        native_mod._init_attempted,
        native_mod._init_ok,
        native_mod._init_error,
        native_mod._lib,
    ) = prev


def test_probe_braw_without_bridge_raises() -> None:
    prev = _force_braw_unavailable()
    try:
        from src.core.video import probe_video

        with pytest.raises(RuntimeError):
            probe_video("/tmp/nonexistent_clip_for_test.braw")
    finally:
        _restore_braw_state(prev)


def test_video_suffixes_include_braw() -> None:
    from src.core.video import _VIDEO_SUFFIXES

    assert ".braw" in _VIDEO_SUFFIXES


def test_run_video_to_exr_braw_missing_sdk(tmp_path: Path) -> None:
    from src.core.braw import BRAWUnavailableError
    from src.core.convert import run_video_to_exr

    prev = _force_braw_unavailable()
    try:
        fake = tmp_path / "clip.braw"
        fake.write_bytes(b"not a real braw")

        with pytest.raises(BRAWUnavailableError):
            run_video_to_exr(
                str(fake),
                tmp_path / "out",
                ocio_cfg=None,
                src_space="ACES2065-1",
                dst_space="ACEScg",
                config_source="",
                config_path="",
            )
    finally:
        _restore_braw_state(prev)


class _MustNotOpenPyAV(BaseException):
    """Not a subclass of Exception — probe_video_metadata catches Exception."""


def _boom_if_pyav_opens(*_args, **_kwargs):
    raise _MustNotOpenPyAV("PyAV must not open BRAW (native SIGSEGV)")


def test_scan_video_files_lists_braw_without_sdk(tmp_path: Path, monkeypatch) -> None:
    import av

    from src.core.video import scan_video_files

    monkeypatch.setattr(av, "open", _boom_if_pyav_opens)
    prev = _force_braw_unavailable()
    try:
        clip = tmp_path / "A001_C001.braw"
        clip.write_bytes(b"not-real-braw")
        rows = scan_video_files(str(tmp_path))
        names = [r["name"] for r in rows]
        assert "A001_C001.braw" in names
        row = next(r for r in rows if r["name"] == "A001_C001.braw")
        assert "BRAW" in row.get("codec", "")
    finally:
        _restore_braw_state(prev)


def test_probe_video_does_not_open_braw_with_pyav(monkeypatch) -> None:
    import av

    from src.core.video import probe_video

    monkeypatch.setattr(av, "open", _boom_if_pyav_opens)
    prev = _force_braw_unavailable()
    try:
        with pytest.raises(RuntimeError):
            probe_video("/tmp/A001_C001.braw")
    finally:
        _restore_braw_state(prev)


def test_guess_colorspace_does_not_open_braw_with_pyav(monkeypatch) -> None:
    import av

    from src.core.video import guess_video_colorspace_candidates

    monkeypatch.setattr(av, "open", _boom_if_pyav_opens)
    cands = guess_video_colorspace_candidates("/tmp/A001_C001.braw")
    assert any("aces" in c.lower() or "ap0" in c.lower() for c in cands)


def test_open_preview_decoder_does_not_open_braw_with_pyav(tmp_path: Path, monkeypatch) -> None:
    import av

    from src.core.braw import BRAWUnavailableError
    from src.core.frame_source import open_preview_decoder

    clip = tmp_path / "A001_C001.braw"
    clip.write_bytes(b"not-real-braw")
    monkeypatch.setattr(av, "open", _boom_if_pyav_opens)
    prev = _force_braw_unavailable()
    try:
        with pytest.raises(BRAWUnavailableError):
            open_preview_decoder(str(clip))
    finally:
        _restore_braw_state(prev)


def test_detect_interlaced_does_not_open_braw_with_pyav(monkeypatch) -> None:
    import av

    from src.core.video import detect_interlaced

    monkeypatch.setattr(av, "open", _boom_if_pyav_opens)
    assert detect_interlaced("/tmp/A001_C001.braw") is False


def test_probe_video_metadata_does_not_open_braw_with_pyav(tmp_path: Path, monkeypatch) -> None:
    import av

    from src.core.video import probe_video_metadata

    clip = tmp_path / "A001_C001.braw"
    clip.write_bytes(b"not-real-braw")
    monkeypatch.setattr(av, "open", _boom_if_pyav_opens)
    prev = _force_braw_unavailable()
    try:
        meta = probe_video_metadata(str(clip))
    finally:
        _restore_braw_state(prev)
    blob = " ".join(meta.values()).lower()
    assert "braw" in blob
    assert "sdk" in blob


def test_load_video_thumbnail_does_not_open_braw_with_pyav(tmp_path: Path, monkeypatch) -> None:
    import av

    from src.gui.browser_thumbs import load_video_thumbnail_rgb

    clip = tmp_path / "A001_C001.braw"
    clip.write_bytes(b"not-real-braw")
    monkeypatch.setattr(av, "open", _boom_if_pyav_opens)
    prev = _force_braw_unavailable()
    try:
        assert load_video_thumbnail_rgb(str(clip)) is None
    finally:
        _restore_braw_state(prev)


def _sample_braw_path() -> Path | None:
    env = os.environ.get("BRAW_SAMPLE", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    for cand in (
        Path.home() / ".braw-sdk" / "slim" / "profile.braw",
        Path.home() / ".braw-sdk" / "profile.braw",
        Path("/home/ubuntu/.braw-sdk/slim/profile.braw"),
    ):
        if cand.is_file():
            return cand
    return None


@pytest.mark.integration
def test_decode_profile_braw_one_frame() -> None:
    """Smoke-test official profile.braw when the bridge + sample are present."""
    from src.core.braw import BRAWClip, is_available

    sample = _sample_braw_path()
    if sample is None:
        pytest.skip("profile.braw not found (set BRAW_SAMPLE or unpack SDK to ~/.braw-sdk)")
    if not is_available():
        pytest.skip("BRAW bridge not built (make braw-bridge)")

    with BRAWClip(sample) as clip:
        info = clip.info
        assert info.width >= 2
        assert info.height >= 2
        assert info.frame_count >= 1
        rgb = clip.decode_frame(0)
        assert rgb.dtype.name == "float32"
        assert rgb.ndim == 3
        assert rgb.shape[2] == 3
        assert rgb.shape[0] == info.height or rgb.shape[0] > 0
        assert rgb.shape[1] == info.width or rgb.shape[1] > 0
        assert rgb.size > 0
        from src.core.braw import decoder_kind

        kind = decoder_kind()
        assert kind in preferred_decoder_kinds()
