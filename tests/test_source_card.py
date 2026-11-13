"""Source card: empty and loaded states, click, and the convert-tab wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import src.rc_resources  # noqa: F401
from src.gui.convert_tab import ConvertTab, VideoInput
from src.gui.source_card import CARD_HEIGHT, SourceCard


@pytest.fixture
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_card_height_is_the_same_empty_and_loaded(app: QApplication) -> None:
    card = SourceCard("Drop a video here")
    assert card.is_empty()
    assert card.height() == CARD_HEIGHT
    card.set_source("clip.mov", ["1920 × 1080", "", "24 fps"])
    assert not card.is_empty()
    assert card.height() == CARD_HEIGHT
    assert card.details() == ["1920 × 1080", "24 fps"]
    card.set_empty()
    assert card.is_empty()


def test_card_paints_in_both_states(app: QApplication) -> None:
    card = SourceCard("Drop a video here")
    card.resize(500, CARD_HEIGHT)
    assert not card.grab().isNull()
    card.set_source("a_very_long_clip_name_" * 8 + ".mov", ["3840 × 2160"] * 12)
    assert not card.grab().isNull()


def test_click_and_keyboard_activate(app: QApplication) -> None:
    card = SourceCard("Drop a video here")
    card.resize(400, CARD_HEIGHT)
    card.show()
    hits: list[bool] = []
    card.clicked.connect(lambda: hits.append(True))
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    QTest.keyClick(card, Qt.Key.Key_Return)
    QTest.keyClick(card, Qt.Key.Key_Space)
    assert hits == [True, True, True]
    assert card.accessibleName() == "Drop a video here"


def test_convert_tab_card_follows_the_probed_video(app: QApplication, tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    tab = ConvertTab("video2exr", settings)
    assert tab.source_card.is_empty()

    tab._video_info = VideoInput("/media/A001_C003.mov", 4096, 2160, 23.976, 480)
    tab._emit_readiness()
    assert tab.source_card.title() == "A001_C003.mov"
    assert tab.source_card.details() == ["4096 × 2160", "480 frames", "23.976 fps", "0:20"]

    tab._video_info = None
    tab._emit_readiness()
    assert tab.source_card.is_empty()
    tab.deleteLater()
