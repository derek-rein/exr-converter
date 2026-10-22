"""Generic optional source-plugin hook (no plugins ship in the public repo)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from src.core import source_plugins

FAKE_PLUGIN = textwrap.dedent(
    """
    from __future__ import annotations

    import numpy as np

    from src.core.frame_source import MediaInfo


    class _Ingest:
        def __init__(self, path, scale):
            self._info = MediaInfo(
                path=path, width=8, height=4, fps=25.0, frame_count=3, kind="fake"
            )
            self._scale = scale
            self.output_stem = "renamed"

        @property
        def info(self):
            return self._info

        @property
        def output_size(self):
            return 8, 4

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

        def log_header(self, log_fn):
            if log_fn:
                log_fn("fake header")

        def iter_frames(self, frame_set=None, *, cancel_check=None):
            for i in sorted(frame_set or {1, 2, 3}):
                yield i, np.full((4, 8, 3), i / 10.0, np.float32), {"fake:frame": str(i)}


    class _Preview:
        def __init__(self, path):
            self.path = path

        def close(self):
            pass

        def get_frame(self, idx):
            return np.zeros((2, 4, 3), np.float32)

        @property
        def decode_size(self):
            return 4, 2


    class FakePlugin:
        name = "Fake RAW"
        suffixes = frozenset({".fakeraw", ".mxf"})
        available = True

        def claims(self, path):
            if path.lower().endswith(".mxf"):
                with open(path, "rb") as fh:
                    return fh.read(8) == b"FAKERAW!"
            return True

        def is_available(self):
            return self.available

        def unavailable_reason(self):
            return "fake runtime missing"

        def codec_label(self, path):
            return "FAKE"

        def sequence_key(self, path):
            return "seq.####.fakeraw" if "/seq." in path.replace(chr(92), "/") else ""

        def probe(self, path):
            return 8, 4, 25.0, 3

        def probe_metadata(self, path):
            return {"Format": "Fake RAW"}

        def colorspace_candidates(self, path):
            return ["Fake Log"]

        def preview_scale(self):
            return 0.5

        def open_preview(self, path, *, fps=0.0):
            return _Preview(path)

        def open_ingest(self, path, *, scale=1.0):
            return _Ingest(path, scale)

        def thumbnail_rgb(self, path):
            return np.full((4, 8, 3), 0.5, np.float32)

        def about_lines(self):
            return ["Fake SDK Version 1.2.3"]

        def notices(self):
            return [("Fake notice", "Copyright Fake Corp.")]


    def get_source_plugin():
        return FakePlugin()
    """
)


@pytest.fixture
def fake_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod_dir = tmp_path / "mods"
    mod_dir.mkdir()
    (mod_dir / "fake_src_plugin.py").write_text(FAKE_PLUGIN, encoding="utf-8")
    (mod_dir / "broken_src_plugin.py").write_text("raise ImportError('boom')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(mod_dir))
    # Ignore any private plugins a local private build may have dropped in.
    monkeypatch.setattr(source_plugins, "ENABLED_MODULE", "src.plugins._no_such_module")
    monkeypatch.setenv(
        source_plugins.ENV_PLUGINS, "broken_src_plugin, fake_src_plugin, missing_src_plugin"
    )
    source_plugins.reset_for_tests()
    yield source_plugins.plugins()[0]
    source_plugins.reset_for_tests()
    for name in ("fake_src_plugin", "broken_src_plugin"):
        sys.modules.pop(name, None)


def test_no_plugins_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(source_plugins.ENV_PLUGINS, raising=False)
    monkeypatch.setattr(source_plugins, "ENABLED_MODULE", "src.plugins._no_such_module")
    source_plugins.reset_for_tests()
    try:
        assert source_plugins.plugins() == []
        assert source_plugins.plugin_for_path("/x/clip.ari") is None
        assert source_plugins.about_lines() == []
        assert source_plugins.notices() == []
    finally:
        source_plugins.reset_for_tests()


def test_discovery_skips_broken_modules(fake_plugin) -> None:
    assert [p.name for p in source_plugins.plugins()] == ["Fake RAW"]
    assert ".fakeraw" in source_plugins.plugin_suffixes()


def test_claims_with_sniff(fake_plugin, tmp_path: Path) -> None:
    good = tmp_path / "a.mxf"
    good.write_bytes(b"FAKERAW!" + b"\0" * 16)
    other = tmp_path / "b.mxf"
    other.write_bytes(b"\x06\x0e\x2b\x34" + b"\0" * 16)
    assert source_plugins.plugin_for_path(good) is fake_plugin
    assert source_plugins.plugin_for_path(other) is None
    assert source_plugins.plugin_for_path(tmp_path / "._c.fakeraw") is None


def test_core_dispatch(fake_plugin, tmp_path: Path) -> None:
    from src.core.frame_source import open_ingest_source, open_preview_decoder
    from src.core.video import (
        guess_video_colorspace_candidates,
        is_libav_unsafe_media,
        probe_video,
        probe_video_metadata,
        scan_video_files,
    )

    clip = tmp_path / "shot.fakeraw"
    clip.write_bytes(b"x" * 32)
    assert probe_video(str(clip)) == (8, 4, 25.0, 3)
    assert is_libav_unsafe_media(str(clip))
    assert guess_video_colorspace_candidates(str(clip)) == ["Fake Log"]
    assert probe_video_metadata(str(clip)) == {"Format": "Fake RAW"}
    with open_ingest_source(clip) as src:
        frames = list(src.iter_frames({2}))
    assert [f[0] for f in frames] == [2]
    assert frames[0][2] == {"fake:frame": "2"}
    assert open_preview_decoder(str(clip)).decode_size == (4, 2)

    rows = {r["name"]: r for r in scan_video_files(str(tmp_path))}
    assert rows["shot.fakeraw"]["codec"] == "FAKE"
    assert rows["shot.fakeraw"]["resolution"] == "8x4"


def test_unavailable_runtime_reports_reason(fake_plugin, tmp_path: Path) -> None:
    from src.core.frame_source import open_ingest_source
    from src.core.video import scan_video_files

    fake_plugin.available = False
    clip = tmp_path / "shot.fakeraw"
    clip.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="fake runtime missing"):
        open_ingest_source(clip)
    rows = {r["name"]: r for r in scan_video_files(str(tmp_path))}
    assert rows["shot.fakeraw"]["codec"] == "FAKE (SDK missing)"


def test_convert_writes_exr_with_plugin_source(fake_plugin, tmp_path: Path) -> None:
    import OpenImageIO as oiio

    from src.core.convert import run_video_to_exr

    clip = tmp_path / "shot.fakeraw"
    clip.write_bytes(b"x")
    out_dir = tmp_path / "out"
    run_video_to_exr(
        str(clip),
        out_dir,
        ocio_cfg=None,
        src_space="ACES2065-1",
        dst_space="ACES2065-1",
        config_source="",
        config_path="",
        workers=1,
    )
    outs = sorted(out_dir.glob("*.exr"))
    assert len(outs) == 3
    assert all(o.name.startswith("renamed.") for o in outs)
    inp = oiio.ImageInput.open(str(outs[0]))
    assert inp is not None
    px = np.asarray(inp.read_image(format="float"))
    inp.close()
    assert px.shape[:2] == (4, 8)


def test_about_and_notices(fake_plugin) -> None:
    assert source_plugins.about_lines() == ["Fake SDK Version 1.2.3"]
    assert source_plugins.notices() == [("Fake notice", "Copyright Fake Corp.")]


def test_frame_sequence_rows_collapse(fake_plugin, tmp_path: Path) -> None:
    from src.core.video import scan_video_files

    for i in range(1, 4):
        (tmp_path / f"seq.{i:04d}.fakeraw").write_bytes(b"x")
    rows = scan_video_files(str(tmp_path))
    seq_rows = [r for r in rows if r["name"] == "seq.####.fakeraw"]
    assert len(seq_rows) == 1
    assert seq_rows[0]["path"].endswith("seq.0001.fakeraw")
