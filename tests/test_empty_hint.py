"""EmptyHint shows only while its view has no rows and never eats the mouse."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget

import src.rc_resources  # noqa: F401
from src.gui.empty_hint import EmptyHint


@pytest.fixture
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_hint_follows_row_count(app: QApplication) -> None:
    view = QListWidget()
    view.resize(300, 120)
    view.show()
    hint = EmptyHint(view, "Drop clips here", "or use Add Clips")
    assert hint.isVisible()
    view.addItem("a.mov")
    assert not hint.isVisible()
    view.clear()
    assert hint.isVisible()
    assert not hint.grab().isNull()


def test_hint_does_not_take_mouse_events(app: QApplication) -> None:
    view = QListWidget()
    hint = EmptyHint(view, "Drop clips here")
    assert hint.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
