"""Background folder tree for the file browsers (no disk access on the GUI thread)."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QObject, Signal

from src.gui import browser_tree
from src.gui.browser_tree import (
    FILE_MODE_SEQUENCES,
    FILE_MODE_VIDEOS,
    KIND_DIR,
    KIND_FILE,
    KIND_SEQUENCE,
    BrowserTreeModel,
    VolumePoller,
    list_directory,
)
from src.gui.browser_volumes import VolumeInfo


def _wait(qapp, pred: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        qapp.processEvents()
        time.sleep(0.002)


def _frames(d: Path, stem: str, n: int, ext: str = "exr", start: int = 1001) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for f in range(start, start + n):
        (d / f"{stem}.{f:04d}.{ext}").touch()


@pytest.fixture
def shot_tree(tmp_path: Path) -> Path:
    root = tmp_path / "show"
    _frames(root / "shot_010", "shot_010_comp", 5)
    _frames(root / "shot_010", "shot_010_bg", 3, ext="dpx")
    (root / "shot_010" / "notes.txt").touch()
    (root / "shot_010" / ".hidden.1001.exr").touch()
    (root / "shot_2").mkdir()
    (root / "shot_10").mkdir()
    (root / ".git").mkdir()
    (root / "clip.mov").touch()
    (root / "clip.MP4").touch()
    return root


class TestListDirectory:
    def test_folders_first_in_natural_order(self, shot_tree: Path) -> None:
        rows = list_directory(str(shot_tree))
        assert [r.name for r in rows] == ["shot_2", "shot_010", "shot_10"]
        assert all(r.kind == KIND_DIR for r in rows)

    def test_sequences_collapse_to_one_row_each(self, shot_tree: Path) -> None:
        rows = list_directory(str(shot_tree / "shot_010"), FILE_MODE_SEQUENCES)
        by_name = {r.name: r for r in rows}
        assert set(by_name) == {"shot_010_bg.####.dpx", "shot_010_comp.####.exr"}
        comp = by_name["shot_010_comp.####.exr"]
        assert comp.kind == KIND_SEQUENCE
        assert comp.label == "shot_010_comp.####.exr  1001-1005"
        assert comp.first_frame == str(shot_tree / "shot_010" / "shot_010_comp.1001.exr")
        assert "5 frame(s)" in comp.tooltip

    def test_video_files(self, shot_tree: Path) -> None:
        rows = list_directory(str(shot_tree), FILE_MODE_VIDEOS, frozenset({".mov", ".mp4"}))
        files = [r for r in rows if r.kind == KIND_FILE]
        assert [r.name for r in files] == ["clip.mov", "clip.MP4"]
        # Folders still come first.
        assert rows[0].kind == KIND_DIR

    def test_skip_paths(self, shot_tree: Path) -> None:
        skip = frozenset({os.path.normcase(os.path.normpath(str(shot_tree / "shot_2")))})
        rows = list_directory(str(shot_tree), skip_paths=skip)
        assert "shot_2" not in [r.name for r in rows]

    def test_missing_folder_raises(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            list_directory(str(tmp_path / "nope"))


def test_macos_hides_volumes_folder_under_root(monkeypatch: pytest.MonkeyPatch, qapp) -> None:
    """Drives are already top-level rows; /Volumes under / listed them twice."""
    monkeypatch.setattr(browser_tree.sys, "platform", "darwin")
    poller = _FakePoller([])
    model = BrowserTreeModel(poller=poller)  # type: ignore[arg-type]
    assert os.path.normcase(os.path.normpath("/Volumes")) in model._skip_paths


class _FakePoller(QObject):
    """Stands in for :class:`VolumePoller` (no QStorageInfo in tests)."""

    volumes_changed = Signal(list)

    def __init__(self, vols: list[VolumeInfo]) -> None:
        super().__init__()
        self._vols = vols

    def volumes(self) -> list[VolumeInfo]:
        return list(self._vols)

    def poll(self) -> None:
        pass

    def stop(self) -> None:
        pass


@pytest.fixture
def model(qapp, shot_tree: Path):
    vol = VolumeInfo(path=str(shot_tree.parent), name="Root", is_system_root=True)
    m = BrowserTreeModel(file_mode=FILE_MODE_SEQUENCES, poller=_FakePoller([vol]))  # type: ignore[arg-type]
    yield m
    m.shutdown()


def _reveal(qapp, model: BrowserTreeModel, path: str) -> QModelIndex:
    got: list[QModelIndex] = []
    model.reveal(path, got.append)
    _wait(qapp, lambda: bool(got))
    return got[0]


class TestBrowserTreeModel:
    def test_volume_rows(self, model: BrowserTreeModel, shot_tree: Path) -> None:
        assert model.rowCount() == 1
        top = model.index(0, 0)
        assert model.data(top) == "Root"
        assert model.filePath(top) == str(shot_tree.parent)
        # Not listed yet: lazy, but expandable.
        assert model.hasChildren(top)
        assert model.canFetchMore(top)

    def test_reveal_lists_each_level_in_background(
        self, qapp, model: BrowserTreeModel, shot_tree: Path
    ) -> None:
        target = shot_tree / "shot_010"
        idx = _reveal(qapp, model, str(target))
        assert idx.isValid()
        assert model.filePath(idx) == str(target)
        assert model.index(str(target)) == idx
        # Expanding the folder (a view calls fetchMore) lists its rows.
        assert model.canFetchMore(idx)
        model.fetchMore(idx)
        # Sequence rows below the folder, one per sequence.
        _wait(qapp, lambda: model.rowCount(idx) == 2)
        kids = [model.index(r, 0, idx) for r in range(model.rowCount(idx))]
        assert {model.kind(k) for k in kids} == {KIND_SEQUENCE}
        assert not model.hasChildren(kids[0])
        assert not model.isDir(kids[0])
        comp = next(k for k in kids if "comp" in model.fileName(k))
        assert model.first_frame(comp).endswith("shot_010_comp.1001.exr")
        assert model.parent(comp) == idx

    def test_reveal_missing_path_returns_deepest_ancestor(
        self, qapp, model: BrowserTreeModel, shot_tree: Path
    ) -> None:
        idx = _reveal(qapp, model, str(shot_tree / "shot_010" / "gone" / "deeper"))
        assert model.filePath(idx) == str(shot_tree / "shot_010")

    def test_refresh_merges_without_losing_rows(
        self, qapp, model: BrowserTreeModel, shot_tree: Path
    ) -> None:
        show = _reveal(qapp, model, str(shot_tree))
        model.fetchMore(show)
        _wait(qapp, lambda: model.rowCount(show) == 3)
        keep = model.node_for_path(str(shot_tree / "shot_010"))
        assert keep is not None
        (shot_tree / "shot_3").mkdir()
        (shot_tree / "shot_2").rmdir()
        model.refresh(str(shot_tree), max_age=0)

        def names() -> list[str]:
            return [model.fileName(model.index(r, 0, show)) for r in range(model.rowCount(show))]

        _wait(qapp, lambda: names() == ["shot_3", "shot_010", "shot_10"])
        # The kept row is the same node (its expansion / children survive).
        assert model.node_for_path(str(shot_tree / "shot_010")) is keep

    def test_volume_list_merge(self, qapp, model: BrowserTreeModel, tmp_path: Path) -> None:
        stick = tmp_path / "stick"
        stick.mkdir()
        changed: list[bool] = []
        model.volumes_changed.connect(lambda: changed.append(True))
        vols = model.volumes() + [VolumeInfo(path=str(stick), name="STICK")]
        model.set_volumes(vols)
        assert model.rowCount() == 2
        assert changed
        model.set_volumes(vols)  # same list: no change signal
        assert len(changed) == 1


def test_volume_poller_emits_only_on_change(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    vols = [VolumeInfo(path="/", name="Root", is_system_root=True)]
    calls: list[int] = []

    def fake_list() -> list[VolumeInfo]:
        calls.append(1)
        return list(vols)

    monkeypatch.setattr(browser_tree, "list_browser_volumes", fake_list)
    poller = VolumePoller(interval_ms=60_000)
    seen: list[list] = []
    poller.volumes_changed.connect(seen.append)
    _wait(qapp, lambda: len(seen) == 1)
    poller.poll()
    _wait(qapp, lambda: len(calls) >= 2 and not poller._busy)
    assert len(seen) == 1  # unchanged list: no second emit
    vols.append(VolumeInfo(path="/mnt/x", name="X"))
    poller.poll()
    _wait(qapp, lambda: len(seen) == 2)
    poller.stop()


def test_places_sidebar_keeps_rows_when_volumes_unchanged(qapp) -> None:
    """Rebuilding every poll re-laid out the sidebar: the flicker over Preview."""
    from src.gui.browser_chrome import _PlacesSidebar

    side = _PlacesSidebar()
    vols = [VolumeInfo(path="/", name="Root", is_system_root=True)]
    side.set_volumes(vols)
    first = side._list.item(side._vol_start)
    side.set_volumes(list(vols))
    assert side._list.item(side._vol_start) is first
    side.set_volumes(vols + [VolumeInfo(path="/mnt/x", name="X")])
    assert side._vol_end - side._vol_start == 2


@pytest.mark.skipif(sys.platform == "win32", reason="posix paths")
def test_dialog_tree_shows_sequences(
    qapp, shot_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.gui.sequence_browser import SequenceBrowserDialog

    vol = VolumeInfo(path=str(shot_tree.parent), name="Root", is_system_root=True)
    monkeypatch.setattr(browser_tree, "list_browser_volumes", lambda: [vol])
    monkeypatch.setattr(SequenceBrowserDialog, "_save_browser_layout", lambda self: None)
    dlg = SequenceBrowserDialog(start_dir=str(shot_tree / "shot_010"))
    try:
        model = dlg._fs_model
        _wait(qapp, lambda: not dlg.is_scanning())
        _wait(qapp, lambda: model.index(str(shot_tree / "shot_010")).isValid())
        folder = model.index(str(shot_tree / "shot_010"))
        _wait(qapp, lambda: model.rowCount(folder) == 2)
        bg = next(
            model.index(r, 0, folder)
            for r in range(2)
            if "bg" in model.fileName(model.index(r, 0, folder))
        )
        # Clicking a sequence in the tree selects it in the list.
        dlg._on_tree_clicked(bg)
        assert dlg._selected_name == "shot_010_bg"
        assert dlg.selected_path().endswith("shot_010_bg.1001.dpx")
    finally:
        dlg.reject()
        dlg.deleteLater()
        qapp.processEvents()
