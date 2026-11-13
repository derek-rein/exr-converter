"""Smoke tests for SequencePlayer (offscreen Qt)."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from src.gui.player.sequence_player import SequencePlayer


def test_sequence_player_constructs_offscreen(qapp, settings: QSettings) -> None:
    player = SequencePlayer(settings=settings, prefer_gpu=False, show_cache_ui=False)
    try:
        assert player is not None
        assert player._current_frame == 1
        assert player._fps == 24.0
        # Empty media: set_frame should not crash.
        player.set_frame(1)
    finally:
        player.close()
        player.deleteLater()
        qapp.processEvents()


def test_transport_chrome(qapp, settings: QSettings) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    player = SequencePlayer(settings=settings, prefer_gpu=False, show_cache_ui=True)
    try:
        shuttle = player._shuttle
        play = shuttle._btn_play
        others = (shuttle._btn_first, shuttle._btn_back, shuttle._btn_fwd, shuttle._btn_last)
        for btn in (play, *others):
            # Space / arrows stay with the player.
            assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert all(play.width() > b.width() and play.height() > b.height() for b in others)
        texts = {lbl.text() for lbl in player.findChildren(QLabel)}
        assert {"Gain", "Gamma", "Cache"} <= texts
        assert player._frame_readout.property("role") == "muted"

        player.set_range(1001, 1012)
        assert player._frame_readout.text() == "1001 · 12 frames"
        player.set_frame(1005)
        assert player._frame_readout.text() == "1005 · 12 frames"
        player.clear()
        assert player._frame_readout.text() == "1 · 1 frame"
    finally:
        player.close()
        player.deleteLater()
        qapp.processEvents()
