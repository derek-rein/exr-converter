"""Browser polish: search box, folder search process, Metadata label, in/out points."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSize

from src.gui.browser_chrome import _DirSearchWorker, _SearchField


def _wait(qapp, pred: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        qapp.processEvents()
        time.sleep(0.002)


class TestSearchField:
    @pytest.mark.parametrize("height", [22, 30, 41])
    def test_clear_button_is_vertically_centred(self, qapp, height: int) -> None:
        field = _SearchField()
        field.resize(QSize(240, height))
        field.setText("a")
        btn = field.clear_button()
        assert not btn.isHidden()
        top = btn.geometry().top()
        bottom_gap = field.height() - btn.geometry().bottom() - 1
        assert abs(top - bottom_gap) <= 1
        # Typed text never runs under the button.
        assert field.textMargins().right() >= btn.width()

    def test_clear_button_hidden_when_empty_and_clears(self, qapp) -> None:
        field = _SearchField()
        assert field.clear_button().isHidden()
        field.setText("shot")
        field.clear_button().click()
        assert field.text() == ""
        assert field.clear_button().isHidden()

    def test_busy_spinner_sits_left_of_clear(self, qapp) -> None:
        field = _SearchField()
        field.resize(QSize(240, 30))
        field.setText("a")
        field.set_busy(True)
        assert field.is_busy()
        assert field._spinner.geometry().right() < field.clear_button().geometry().left()
        field.set_busy(False)
        assert not field.is_busy()


def test_folder_search_runs_in_a_process_and_drops_stale(qapp, tmp_path: Path) -> None:
    for name in ("shot_010", "shot_020", "plates"):
        (tmp_path / "a" / name).mkdir(parents=True)
    worker = _DirSearchWorker(dirs_only=True)
    found: list[str] = []
    done: list[int] = []
    worker.batch_ready.connect(lambda items: found.extend(i[0] for i in items))
    worker.search_finished.connect(done.append)
    try:
        worker.start_search(str(tmp_path), "plates")  # replaced before it reports
        worker.start_search(str(tmp_path), "shot")
        assert worker._worker is not None and worker._worker.uses_process
        _wait(qapp, lambda: bool(done))
        assert sorted(found) == ["shot_010", "shot_020"]
        assert done == [2]
    finally:
        worker.shutdown()


def test_checkbox_is_named_metadata(qapp, tmp_path: Path, monkeypatch) -> None:
    from src.gui.sequence_browser import SequenceBrowserDialog
    from src.gui.video_browser import VideoBrowserDialog

    for cls in (SequenceBrowserDialog, VideoBrowserDialog):
        monkeypatch.setattr(cls, "_save_browser_layout", lambda self: None)
        dlg = cls(start_dir=str(tmp_path))
        try:
            assert dlg._inspect_cb.text() == "Metadata"
        finally:
            dlg.reject()
            dlg.deleteLater()
            qapp.processEvents()


def _write_exr_seq(d: Path, n: int = 12, start: int = 1001) -> Path:
    import OpenImageIO as oiio

    d.mkdir(parents=True, exist_ok=True)
    for f in range(start, start + n):
        p = d / f"plate.{f}.exr"
        img = np.full((8, 16, 3), (f - start) / n, np.float32)
        out = oiio.ImageOutput.create(str(p))
        out.open(str(p), oiio.ImageSpec(16, 8, 3, "half"))
        out.write_image(img)
        out.close()
    return d / f"plate.{start}.exr"


class TestInOutPoints:
    def test_player_in_out(self, qapp, tmp_path: Path) -> None:
        from src.gui.player.sequence_player import SequencePlayer

        first = _write_exr_seq(tmp_path / "seq")
        player = SequencePlayer(show_in_out=True, prefer_gpu=False, show_cache_ui=False)
        try:
            prepared = SequencePlayer.prepare_sequence(str(first))
            assert player.load_sequence(str(first), prepared=prepared)
            assert player.in_out_range() is None
            changes: list[tuple] = []
            player.in_out_changed.connect(lambda a, b: changes.append((a, b)))
            player.set_frame(1004)
            player.set_in_point()
            player.set_frame(1009)
            player.set_out_point()
            assert player.in_out_range() == (1004, 1009)
            assert changes[-1] == (1004, 1009)
            # Playback loops inside in/out.
            assert player._next_playback_frame(1009) == 1004
            # An in point after the out point clears the out point.
            player.set_in_point(1010)
            assert (player.in_point(), player.out_point()) == (1010, None)
            assert player.in_out_range() == (1010, 1012)
            player.clear_in_out()
            assert player.in_out_range() is None
            # A new clip starts without in/out.
            player.set_in_point(1002)
            assert player.load_sequence(str(first), prepared=prepared)
            assert player.in_out_range() is None
        finally:
            player.shutdown()
            player.deleteLater()
            qapp.processEvents()

    def test_sequence_browser_open_returns_in_out_range(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:
        from src.gui.browser_state import VIEW_PREVIEW
        from src.gui.sequence_browser import SequenceBrowserDialog

        first = _write_exr_seq(tmp_path / "seq")
        monkeypatch.setattr(SequenceBrowserDialog, "_save_browser_layout", lambda self: None)
        dlg = SequenceBrowserDialog(start_dir=str(first))
        try:
            _wait(qapp, lambda: not dlg.is_scanning())
            dlg._view_seg.setCurrentData(VIEW_PREVIEW)
            player = dlg._player
            assert player is not None
            _wait(qapp, lambda: player.first_shot_frame() == 1001)
            assert dlg.selected_frame_range() == ""
            player.set_in_point(1003)
            player.set_out_point(1007)
            assert dlg.selected_frame_range() == "1003-1007"
            # Leaving and re-entering Preview keeps the clip and its in/out.
            dlg._view_seg.setCurrentData("list")
            dlg._view_seg.setCurrentData(VIEW_PREVIEW)
            assert player.in_out_range() == (1003, 1007)
            dlg.accept()
            assert dlg.accepted_frame_range() == "1003-1007"
        finally:
            dlg.deleteLater()
            qapp.processEvents()
