"""Fuzzy sequence search: in-memory index thread + SequenceBrowserDialog wiring."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtTest import QTest

from src.gui.sequence_browser import SequenceBrowserDialog
from src.gui.sequence_search import SequenceHit, SequenceIndex


def _make_seq(
    directory: Path, stem: str, frames: int, *, sep: str = ".", ext: str = "exr", start: int = 1001
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for f in range(start, start + frames):
        (directory / f"{stem}{sep}{f:04d}.{ext}").touch()


def _wait(qapp, pred: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        qapp.processEvents()
        time.sleep(0.002)


@pytest.fixture
def two_roots(tmp_path: Path) -> tuple[Path, Path]:
    """Root A: shot comps (dot pad). Root B: nested plates (underscore pad) + dpx."""
    a = tmp_path / "rootA"
    b = tmp_path / "rootB"
    _make_seq(a / "shot_010", "shot_010_comp", 12)
    _make_seq(a / "shot_020", "shot_020_comp", 8)
    _make_seq(b / "plates" / "bg", "plate_bg_v001", 10, sep="_", start=1)
    _make_seq(b / "plates" / "fg", "fg_roto", 5, ext="dpx")
    # Noise that must not index: hidden files, single frames, non-image files.
    (b / "plates" / ".hidden.0001.exr").touch()
    (b / "plates" / "notes.txt").touch()
    (b / "plates" / "lonely.exr").touch()
    return a, b


class _Recorder:
    def __init__(self, index: SequenceIndex) -> None:
        self.count = -1
        self.busy = True
        self.results: dict[int, list[SequenceHit]] = {}
        index.signals.index_changed.connect(self._on_index, Qt.ConnectionType.QueuedConnection)
        index.signals.search_ready.connect(self._on_search, Qt.ConnectionType.QueuedConnection)

    def _on_index(self, count: int, busy: bool) -> None:
        self.count = count
        self.busy = busy

    def _on_search(self, gen: int, _q: str, hits: object, _count: int, _busy: bool) -> None:
        assert isinstance(hits, list)
        self.results[gen] = hits


@pytest.fixture
def index(qapp):
    idx = SequenceIndex()
    yield idx
    assert idx.stop()


def _search(qapp, idx: SequenceIndex, rec: _Recorder, query: str, gen: int, **kw):
    idx.search(query, gen, **kw)
    _wait(qapp, lambda: gen in rec.results)
    return rec.results[gen]


class TestSequenceIndex:
    def test_index_two_roots_and_typo_query(self, qapp, index, two_roots) -> None:
        a, b = two_roots
        rec = _Recorder(index)
        index.ensure_indexed(str(a))
        index.ensure_indexed(str(b))
        _wait(qapp, lambda: rec.count == 4 and not rec.busy)

        hits = _search(qapp, index, rec, "shto_010_comp", 1)
        assert hits, "typo query should match"
        top = hits[0]
        assert top.stem == "shot_010_comp"
        assert top.parent == str(a / "shot_010")
        assert top.frame_count == 12
        assert top.frame_range == "1001-1012"
        assert top.first_frame == str(a / "shot_010" / "shot_010_comp.1001.exr")
        assert top.score >= 60

    def test_partial_query_and_underscore_pad(self, qapp, index, two_roots) -> None:
        a, b = two_roots
        rec = _Recorder(index)
        index.ensure_indexed(str(a))
        index.ensure_indexed(str(b))
        _wait(qapp, lambda: rec.count == 4 and not rec.busy)

        hits = _search(qapp, index, rec, "plate", 1)
        assert hits[0].stem == "plate_bg_v001"
        assert hits[0].pattern == "plate_bg_v001_####.exr"
        comps = _search(qapp, index, rec, "comp", 2)
        assert {h.stem for h in comps[:2]} == {"shot_010_comp", "shot_020_comp"}
        assert _search(qapp, index, rec, "roto", 3)[0].ext == ".dpx"

    def test_empty_results_and_cutoff(self, qapp, index, two_roots) -> None:
        a, _b = two_roots
        rec = _Recorder(index)
        index.ensure_indexed(str(a))
        _wait(qapp, lambda: rec.count == 2 and not rec.busy)

        assert _search(qapp, index, rec, "zzqqxxvv", 1) == []
        # WRatio("shto_010_comp", "shot_010_comp") is ~92: in at 60, out at 95.
        assert _search(qapp, index, rec, "shto_010_comp", 2, cutoff=60)
        assert _search(qapp, index, rec, "shto_010_comp", 3, cutoff=95) == []
        assert len(_search(qapp, index, rec, "comp", 4, limit=1)) == 1

    def test_search_before_index_is_empty(self, qapp, index) -> None:
        rec = _Recorder(index)
        assert _search(qapp, index, rec, "anything", 1) == []

    def test_clear_drops_rows(self, qapp, index, two_roots) -> None:
        a, _b = two_roots
        rec = _Recorder(index)
        index.ensure_indexed(str(a))
        _wait(qapp, lambda: rec.count == 2 and not rec.busy)
        index.clear()
        _wait(qapp, lambda: rec.count == 0)
        assert _search(qapp, index, rec, "shot_010_comp", 1) == []
        # Same root can be indexed again after a clear.
        index.ensure_indexed(str(a))
        _wait(qapp, lambda: rec.count == 2 and not rec.busy)

    def test_child_of_indexed_root_is_not_rewalked(self, qapp, index, two_roots) -> None:
        a, _b = two_roots
        rec = _Recorder(index)
        index.ensure_indexed(str(a / "shot_010"))
        _wait(qapp, lambda: rec.count == 1 and not rec.busy)
        index.ensure_indexed(str(a))  # parent supersedes and adds the sibling
        _wait(qapp, lambda: rec.count == 2 and not rec.busy)
        rec.busy = False
        index.ensure_indexed(str(a / "shot_020"))  # covered: no new walk
        hits = _search(qapp, index, rec, "shot_020_comp", 1)
        assert hits and not rec.busy

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
    def test_symlink_loop_does_not_hang(self, qapp, index, tmp_path: Path) -> None:
        root = tmp_path / "loop"
        _make_seq(root / "a", "loop_seq", 3)
        os.symlink(root, root / "a" / "back_to_root")
        rec = _Recorder(index)
        index.ensure_indexed(str(root))
        _wait(qapp, lambda: rec.count == 1 and not rec.busy, timeout=5.0)

    def test_stop_joins_and_refuses_new_work(self, qapp, two_roots) -> None:
        a, _b = two_roots
        idx = SequenceIndex()
        idx.ensure_indexed(str(a))
        assert idx.is_running()
        assert idx.stop()
        assert not idx.is_running()
        idx.ensure_indexed(str(a))
        assert not idx.is_running()

    def test_event_loop_stays_responsive_while_indexing(self, qapp, index, tmp_path: Path) -> None:
        root = tmp_path / "big"
        for i in range(600):
            _make_seq(root / f"seq{i // 50:02d}" / f"shot_{i:04d}", f"shot_{i:04d}_comp", 30)
        rec = _Recorder(index)

        gaps: list[int] = []
        clock = QElapsedTimer()
        clock.start()
        last = [0]

        def tick() -> None:
            now = clock.elapsed()
            gaps.append(now - last[0])
            last[0] = now

        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(tick)
        timer.start()
        index.ensure_indexed(str(root))
        # Query mid-walk too: searches are served between walk steps.
        index.search("shot_0007_comp", 1)
        _wait(qapp, lambda: rec.count == 600 and not rec.busy, timeout=60.0)
        timer.stop()
        elapsed = clock.elapsed()

        assert 1 in rec.results
        assert len(gaps) >= max(3, elapsed // 40), (len(gaps), elapsed)
        assert max(gaps[1:] or [0]) < 250, max(gaps)
        hits = _search(qapp, index, rec, "shot_0007_comp", 2)
        assert hits[0].stem == "shot_0007_comp"


@pytest.fixture
def no_layout_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SequenceBrowserDialog, "_save_browser_layout", lambda self: None)


def _open_dialog(qapp, start: Path) -> SequenceBrowserDialog:
    dlg = SequenceBrowserDialog(start_dir=str(start))
    dlg._view_seg.blockSignals(True)
    dlg._view_seg.setCurrentData("list")
    dlg._view_seg.blockSignals(False)
    dlg._view_stack.setCurrentIndex(0)
    return dlg


def _close(qapp, dlg: SequenceBrowserDialog) -> None:
    dlg.reject()
    dlg.deleteLater()
    qapp.processEvents()


@pytest.mark.usefixtures("no_layout_io")
class TestSequenceBrowserSearch:
    def test_typo_search_jumps_and_selects(self, qapp, two_roots) -> None:
        a, b = two_roots
        dlg = _open_dialog(qapp, b / "plates" / "bg")
        try:
            dlg._navigate_to(str(a))
            _wait(qapp, lambda: dlg._index_count == 3 and not dlg._index_busy)
            assert dlg._selected_dir == str(a)

            dlg._seq_search_edit.setText("shto_010_comp")
            assert dlg._view_stack.currentIndex() == 3
            _wait(qapp, lambda: dlg._search_results.count() > 0)
            hit = dlg._search_results.item(0).data(Qt.ItemDataRole.UserRole)
            assert isinstance(hit, SequenceHit) and hit.stem == "shot_010_comp"

            accepted: list[bool] = []
            dlg.accepted.connect(lambda: accepted.append(True))
            QTest.keyClick(dlg._seq_search_edit, Qt.Key.Key_Return)
            assert not accepted  # Enter jumped to the hit instead of pressing Open
            assert dlg._seq_search_edit.text() == ""
            assert dlg._view_stack.currentIndex() == 0
            assert dlg._selected_dir == str(a / "shot_010")
            assert dlg._selected_name == "shot_010_comp"
            assert dlg.selected_path() == str(a / "shot_010" / "shot_010_comp.1001.exr")
            assert dlg._path_edit.text() == str(a / "shot_010")
            tree_path = dlg._fs_model.filePath(dlg._tree.currentIndex())
            assert os.path.normpath(tree_path) == str(a / "shot_010")
            assert dlg._table.selectionModel().selectedRows()[0].row() == 0
        finally:
            _close(qapp, dlg)

    def test_empty_search_leaves_browse_untouched(self, qapp, two_roots) -> None:
        a, _b = two_roots
        dlg = _open_dialog(qapp, a / "shot_010")
        try:
            rows = dlg._table.rowCount()
            name = dlg._selected_name
            dlg._seq_search_edit.setText("   ")
            assert dlg._view_stack.currentIndex() == 0
            dlg._seq_search_edit.setText("shot")
            assert dlg._view_stack.currentIndex() == 3
            rejected: list[bool] = []
            dlg.rejected.connect(lambda: rejected.append(True))
            QTest.keyClick(dlg._seq_search_edit, Qt.Key.Key_Escape)
            assert not rejected  # Escape cleared the search, did not close the dialog
            assert dlg._seq_search_edit.text() == ""
            assert dlg._view_stack.currentIndex() == 0
            assert dlg._table.rowCount() == rows
            assert dlg._selected_name == name
        finally:
            _close(qapp, dlg)

    def test_no_match_shows_empty_state_and_drops_stale(self, qapp, two_roots) -> None:
        a, _b = two_roots
        dlg = _open_dialog(qapp, a)
        try:
            _wait(qapp, lambda: dlg._index_count == 2 and not dlg._index_busy)
            dlg._seq_search_edit.setText("zzqqxxvv")
            QTest.keyClick(dlg._seq_search_edit, Qt.Key.Key_Return)  # skip debounce
            _wait(qapp, lambda: "No sequences match" in dlg._search_empty.text())
            assert dlg._search_results.count() == 0
            assert not dlg._search_empty.isHidden()
            # A result for an older generation is ignored.
            stale = SequenceHit(99.0, "x", "x.####.exr", str(a), "x.1001.exr", ".exr", 1, "1")
            dlg._on_seq_search_ready(dlg._search_gen - 1, "zzqqxxvv", [stale], 2, False)
            assert dlg._search_results.count() == 0
        finally:
            _close(qapp, dlg)

    def test_root_change_rebuilds_index(self, qapp, two_roots) -> None:
        a, b = two_roots
        dlg = _open_dialog(qapp, a)
        try:
            dlg._navigate_to(str(b))
            _wait(qapp, lambda: dlg._index_count == 4 and not dlg._index_busy)
            dlg._fs_model.volumes_changed.emit()
            # Cleared, then only the current folder (root B) is indexed again.
            _wait(qapp, lambda: dlg._index_count == 2 and not dlg._index_busy)
        finally:
            _close(qapp, dlg)

    def test_close_stops_thread_and_discards_index(self, qapp, two_roots) -> None:
        a, _b = two_roots
        dlg = _open_dialog(qapp, a)
        index = dlg._seq_index
        assert index.is_running()
        dlg.reject()
        assert not index.is_running()
        index.ensure_indexed(str(a))  # closed: no restart
        assert not index.is_running()
        dlg.deleteLater()
        qapp.processEvents()
