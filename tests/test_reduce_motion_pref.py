"""Preferences: the Reduce motion switch is saved and applied at once."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src.gui import motion
from src.gui.preferences import PreferencesDialog


@pytest.fixture
def settings(tmp_path: Path):
    QApplication.instance() or QApplication([])
    yield QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    motion.set_reduced(None)


def test_checkbox_starts_from_the_saved_value(settings: QSettings) -> None:
    assert PreferencesDialog(settings)._reduce_motion_cb.isChecked() is False
    settings.setValue(motion.SETTINGS_KEY, True)
    assert PreferencesDialog(settings)._reduce_motion_cb.isChecked() is True


def test_accept_saves_and_applies(settings: QSettings) -> None:
    dlg = PreferencesDialog(settings)
    dlg._reduce_motion_cb.setChecked(True)
    dlg._on_accept()
    assert settings.value(motion.SETTINGS_KEY, False, type=bool) is True
    assert motion.reduced() is True
    assert motion.ms(motion.BASE) == 0


def test_apply_saved_preference_at_startup(settings: QSettings) -> None:
    settings.setValue(motion.SETTINGS_KEY, True)
    motion.apply_saved_preference(settings)
    assert motion.reduced() is True
    settings.setValue(motion.SETTINGS_KEY, False)
    motion.apply_saved_preference(settings)
    # Off means "follow the environment", which is reduced when there is no display.
    assert motion.reduced() is motion.reduced_from_env(__import__("os").environ)
