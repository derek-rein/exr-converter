"""Cloud-placeholder / safe listing helpers for the in-app file browsers."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.local_fs import (
    ONLINE_ONLY_LABEL,
    _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    _UF_DATALESS,
    can_probe_media,
    entry_is_file,
    is_cloud_placeholder,
    is_incomplete_download_name,
    iter_dir_entries,
    path_is_dir,
    path_is_file,
    should_skip_search_dir,
)


class _FakeStat:
    def __init__(
        self,
        *,
        mode: int = stat.S_IFREG | 0o644,
        size: int = 1_000_000,
        blocks: int = 16,
        flags: int = 0,
    ) -> None:
        self.st_mode = mode
        self.st_size = size
        self.st_blocks = blocks
        self.st_flags = flags


def test_incomplete_download_names() -> None:
    assert is_incomplete_download_name("clip.mov.partial")
    assert is_incomplete_download_name("/tmp/a.exr.crdownload")
    assert is_incomplete_download_name(".dropbox")
    assert not is_incomplete_download_name("clip.mov")
    assert not is_incomplete_download_name("plate.1001.exr")


def test_should_skip_search_dir() -> None:
    assert should_skip_search_dir(".dropbox")
    assert should_skip_search_dir(".dropbox.cache")
    assert not should_skip_search_dir("Dropbox")
    assert not should_skip_search_dir("plates")


def test_path_is_dir_and_file_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "ok"
    real.mkdir()
    assert path_is_dir(real)
    assert not path_is_file(real)

    def boom(self: Path) -> bool:
        raise OSError("cloud placeholder")

    monkeypatch.setattr(Path, "is_dir", boom)
    monkeypatch.setattr(Path, "is_file", boom)
    assert path_is_dir(real) is False
    assert path_is_file(real) is False


def test_iter_dir_entries_skips_unreadable_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert list(iter_dir_entries(missing)) == []


def test_iter_dir_entries_lists_local(tmp_path: Path) -> None:
    (tmp_path / "a.mov").write_bytes(b"x")
    (tmp_path / "b.exr").write_bytes(b"y")
    names = {e.name for e in iter_dir_entries(tmp_path)}
    assert names == {"a.mov", "b.exr"}


def test_entry_is_file_swallows_oserror() -> None:
    class Boom:
        def is_file(self, follow_symlinks: bool = True) -> bool:
            raise OSError("reparse")

    assert entry_is_file(Boom()) is False  # type: ignore[arg-type]


def test_dataless_flag_is_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.local_fs.lstat_nofollow",
        lambda _p: _FakeStat(flags=_UF_DATALESS, size=2_000_000, blocks=8),
    )
    assert is_cloud_placeholder("/cloud/clip.mov") is True
    assert can_probe_media("/cloud/clip.mov") is False


def test_zero_allocated_blocks_is_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.local_fs.lstat_nofollow",
        lambda _p: _FakeStat(size=50_000_000, blocks=0, flags=0),
    )
    assert is_cloud_placeholder("/dropbox/plate.1001.exr") is True
    assert can_probe_media("/dropbox/plate.1001.exr") is False


def test_local_file_with_blocks_is_not_placeholder(tmp_path: Path) -> None:
    p = tmp_path / "local.mov"
    p.write_bytes(b"not-empty")
    assert is_cloud_placeholder(p) is False
    assert can_probe_media(p) is True


def test_zero_byte_local_is_not_probeable(tmp_path: Path) -> None:
    p = tmp_path / "empty.mov"
    p.write_bytes(b"")
    assert can_probe_media(p) is False


def test_windows_recall_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.local_fs.lstat_nofollow",
        lambda _p: _FakeStat(size=10_000, blocks=20, flags=0),
    )
    monkeypatch.setattr(
        "src.core.local_fs._windows_file_attributes",
        lambda _p: _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    )
    assert is_cloud_placeholder(r"C:\Dropbox\clip.mov") is True
    assert can_probe_media(r"C:\Dropbox\clip.mov") is False


def test_scan_video_files_does_not_open_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.video import scan_video_files

    clip = tmp_path / "hero.mov"
    clip.write_bytes(b"not-a-real-mov")
    opened: list[str] = []

    def fake_open(path: str, *args: object, **kwargs: object) -> None:
        opened.append(str(path))
        raise AssertionError("av.open must not run on placeholders")

    monkeypatch.setattr("src.core.video.can_probe_media", lambda *a, **k: False)
    monkeypatch.setattr("src.core.video.is_cloud_placeholder", lambda *a, **k: True)
    monkeypatch.setitem(__import__("sys").modules, "av", SimpleNamespace(open=fake_open))
    rows = scan_video_files(str(tmp_path))
    assert opened == []
    assert len(rows) == 1
    assert rows[0]["name"] == "hero.mov"
    assert rows[0]["codec"] == ONLINE_ONLY_LABEL
    assert rows[0]["path"] == str(clip)


def test_scan_video_files_skips_unstatable_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.video import scan_video_files

    good = tmp_path / "good.mov"
    good.write_bytes(b"x")

    class Bad:
        name = "ghost.mov"
        path = str(tmp_path / "ghost.mov")

        def is_file(self, follow_symlinks: bool = True) -> bool:
            raise OSError("reparse")

        def is_dir(self, follow_symlinks: bool = True) -> bool:
            raise OSError("reparse")

        def stat(self, follow_symlinks: bool = True) -> os.stat_result:
            raise OSError("reparse")

    class Good:
        name = good.name
        path = str(good)

        def is_file(self, follow_symlinks: bool = True) -> bool:
            return True

        def is_dir(self, follow_symlinks: bool = True) -> bool:
            return False

        def stat(self, follow_symlinks: bool = True) -> os.stat_result:
            return good.stat()

    monkeypatch.setattr("src.core.video.iter_dir_entries", lambda _d: [Bad(), Good()])
    monkeypatch.setattr("src.core.video.can_probe_media", lambda *a, **k: False)
    monkeypatch.setattr("src.core.video.is_cloud_placeholder", lambda *a, **k: False)
    rows = scan_video_files(str(tmp_path))
    names = [r["name"] for r in rows]
    assert "good.mov" in names
    assert "ghost.mov" not in names


def test_scan_exr_sequences_lists_without_opening_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.sequence import scan_exr_sequences

    d = tmp_path / "seq"
    d.mkdir()
    (d / "plate.0001.exr").write_bytes(b"not-exr")
    (d / "plate.0002.exr").write_bytes(b"not-exr")

    opened: list[str] = []

    def fake_open(_path: str) -> None:
        opened.append(_path)
        raise AssertionError("OIIO must not open placeholders")

    oiio = SimpleNamespace(ImageInput=SimpleNamespace(open=fake_open))
    monkeypatch.setitem(__import__("sys").modules, "OpenImageIO", oiio)
    monkeypatch.setattr("src.core.sequence.can_probe_media", lambda *a, **k: False)
    rows = scan_exr_sequences(str(d))
    assert opened == []
    assert len(rows) == 1
    assert rows[0]["name"] == "plate"
    assert rows[0]["frames"] == 2
    assert rows[0]["resolution"] == ""


def test_find_image_seqs_survives_listdir_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fileseq

    from src.core.sequence import _find_image_seqs

    def boom(_directory: str) -> list:
        raise OSError("Dropbox listing failed")

    monkeypatch.setattr(fileseq, "findSequencesOnDisk", boom)
    assert _find_image_seqs(str(tmp_path)) == []


def test_browser_thumbs_skip_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.gui.browser_thumbs import load_browser_thumbnail_rgb, load_video_thumbnail_rgb

    p = tmp_path / "a.exr"
    p.write_bytes(b"x")
    monkeypatch.setattr("src.gui.browser_thumbs.can_probe_media", lambda *a, **k: False)
    assert load_browser_thumbnail_rgb(str(p)) is None
    assert load_video_thumbnail_rgb(str(tmp_path / "a.mov")) is None


def test_resolve_sequence_pattern_when_frame_unstatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.gui.browser_path import resolve_sequence_browser_path

    def is_file(_p: object) -> bool:
        return False

    def is_dir(p: object) -> bool:
        try:
            return Path(p).resolve() == tmp_path.resolve()
        except OSError:
            return False

    monkeypatch.setattr("src.gui.browser_path.path_is_file", is_file)
    monkeypatch.setattr("src.gui.browser_path.path_is_dir", is_dir)
    got = resolve_sequence_browser_path(str(tmp_path / "shot.####.exr"))
    assert got == (str(tmp_path), "shot")


def test_search_skip_dirs_include_dropbox() -> None:
    from src.gui.browser_chrome import _SEARCH_SKIP_DIRS, _SEARCH_SKIP_SUFFIXES

    assert ".dropbox" in _SEARCH_SKIP_DIRS
    assert ".dropbox.cache" in _SEARCH_SKIP_DIRS
    assert ".partial" in _SEARCH_SKIP_SUFFIXES


def test_fs_model_disables_cloud_hostile_options(qapp) -> None:
    from PySide6.QtWidgets import QFileSystemModel

    from src.gui.browser_volumes import MultiRootDirModel

    model = MultiRootDirModel()
    opts = getattr(QFileSystemModel, "Option", None)
    if opts is None:
        pytest.skip("QFileSystemModel.Option missing")
    watch = getattr(opts, "DontWatchForChanges", None)
    icons = getattr(opts, "DontUseCustomDirectoryIcons", None)
    if watch is not None:
        assert model._fs.testOption(watch) is True
    if icons is not None:
        assert model._fs.testOption(icons) is True
