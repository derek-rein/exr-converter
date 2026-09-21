from __future__ import annotations

from pathlib import Path

import pytest


class _MustNotOpenPyAV(BaseException):
    pass


def _boom(*_a, **_k):
    raise _MustNotOpenPyAV("PyAV must not open libav-unsafe media")


def _patch_av_open(monkeypatch) -> None:
    import av

    monkeypatch.setattr(av, "open", _boom)


@pytest.mark.parametrize(
    "name",
    ["clip.R3D", "clip.nev", "clip.braw", "clip.ari", "clip.arx", "._clip.R3D", "._clip.mov"],
)
def test_is_libav_unsafe_media(name: str) -> None:
    from src.core.video import is_libav_unsafe_media

    assert is_libav_unsafe_media(f"/tmp/{name}") is True
    assert is_libav_unsafe_media("/tmp/clip.mov") is False


@pytest.mark.parametrize("name", ["clip.ari", "clip.arx", "._A004.R3D"])
def test_detect_interlaced_skips_unsafe(name: str, monkeypatch) -> None:
    from src.core.video import detect_interlaced

    _patch_av_open(monkeypatch)
    result = detect_interlaced(f"/tmp/{name}")
    assert result in (False, None)


@pytest.mark.parametrize("name", ["shot.ari", "shot.arx"])
def test_probe_video_metadata_skips_ari(tmp_path: Path, monkeypatch, name: str) -> None:
    from src.core.video import probe_video_metadata

    clip = tmp_path / name
    clip.write_bytes(b"not-real")
    _patch_av_open(monkeypatch)
    meta = probe_video_metadata(str(clip))
    blob = " ".join(meta.values()).lower()
    assert "arri" in blob or "ari" in blob or "unsupported" in blob


@pytest.mark.parametrize("name", ["shot.ari", "shot.arx"])
def test_probe_video_skips_ari(tmp_path: Path, monkeypatch, name: str) -> None:
    from src.core.video import probe_video

    clip = tmp_path / name
    clip.write_bytes(b"not-real")
    _patch_av_open(monkeypatch)
    with pytest.raises(RuntimeError):
        probe_video(str(clip))


@pytest.mark.parametrize("name", ["shot.ari", "shot.arx"])
def test_thumbnail_skips_ari(tmp_path: Path, monkeypatch, name: str) -> None:
    from src.gui.browser_thumbs import load_video_thumbnail_rgb

    clip = tmp_path / name
    clip.write_bytes(b"not-real")
    _patch_av_open(monkeypatch)
    assert load_video_thumbnail_rgb(str(clip)) is None


@pytest.mark.parametrize("name", ["shot.ari", "shot.arx"])
def test_preview_and_ingest_skip_ari(tmp_path: Path, monkeypatch, name: str) -> None:
    from src.core.frame_source import open_ingest_source, open_preview_decoder

    clip = tmp_path / name
    clip.write_bytes(b"not-real")
    _patch_av_open(monkeypatch)
    with pytest.raises(RuntimeError):
        open_ingest_source(str(clip))
    with pytest.raises(RuntimeError):
        open_preview_decoder(str(clip))
