"""Shared pytest fixtures.

Qt objects (``QObject``/``QTimer``/``QSettings``/``QPainter``) require a live
``QApplication`` and a usable platform plugin.  We force the headless
``offscreen`` plugin *before* PySide6 is imported so the suite runs in CI and
on developer machines without a display.
"""

from __future__ import annotations

import os

# Must be set before the first PySide6 import anywhere in the process.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """A process-wide ``QApplication`` (Qt allows only one)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def settings(tmp_path, qapp) -> QSettings:
    """An isolated ``QSettings`` backed by a temp ini file (no user pollution)."""
    path = tmp_path / "settings.ini"
    s = QSettings(str(path), QSettings.Format.IniFormat)
    return s
