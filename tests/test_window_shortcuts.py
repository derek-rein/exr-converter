"""Main-window keyboard shortcuts: Convert and tab switching."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

import src.rc_resources  # noqa: F401
from src.gui.window import MainWindow


@pytest.fixture
def win():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def _shortcut(window: MainWindow, keys: str) -> QShortcut:
    want = QKeySequence(keys)
    for sc in window.findChildren(QShortcut):
        if sc.key() == want:
            return sc
    raise AssertionError(f"no shortcut bound to {keys}")


def test_ctrl_number_switches_tabs(win: MainWindow) -> None:
    for index in range(win._tabs.count()):
        _shortcut(win, f"Ctrl+{index + 1}").activated.emit()
        assert win._tabs.currentIndex() == index


def test_ctrl_return_does_nothing_while_convert_is_disabled(win: MainWindow) -> None:
    win._tabs.setCurrentIndex(0)
    assert not win._go.isEnabled()
    started: list[bool] = []
    win._start = lambda: started.append(True)  # type: ignore[method-assign]
    _shortcut(win, "Ctrl+Return").activated.emit()
    assert started == []


def test_ctrl_return_starts_when_convert_is_enabled(win: MainWindow) -> None:
    started: list[bool] = []
    win._start = lambda: started.append(True)  # type: ignore[method-assign]
    win._go.setEnabled(True)
    _shortcut(win, "Ctrl+Return").activated.emit()
    assert started == [True]


def test_convert_label_has_no_padding_spaces(win: MainWindow) -> None:
    assert win._go.text() == "Convert"
    native = QKeySequence("Ctrl+Return").toString(QKeySequence.SequenceFormat.NativeText)
    assert native in win._go.toolTip()


def test_a_failed_conversion_expands_the_log(
    win: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    win._set_log_shown(False)
    assert not win._log_toggle.isChecked()
    win._on_failed("encoder exploded")
    assert win._log_toggle.isChecked()
    assert win._statusbar.currentMessage() == "Conversion failed."


def test_keyboard_tab_switch_does_not_animate(win: MainWindow) -> None:
    bar = win._tabs.tabBar()
    win._tabs.setCurrentIndex(0)
    _shortcut(win, "Ctrl+3").activated.emit()
    assert win._tabs.currentIndex() == 2
    assert bar.last_change_was_instant()
    win._tabs.setCurrentIndex(1)  # a click path
    assert not bar.last_change_was_instant()
