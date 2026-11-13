"""The async input probe must apply its result on the GUI thread."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import OpenImageIO as oiio
import pytest
from PySide6.QtCore import QSettings, QThread
from PySide6.QtWidgets import QApplication

from src.gui.convert_tab import ConvertTab


def _write_exr(path: Path) -> None:
    out = oiio.ImageOutput.create(str(path))
    out.open(str(path), oiio.ImageSpec(8, 8, 3, "half"))
    out.write_image(np.zeros((8, 8, 3), dtype=np.float32))
    out.close()


@pytest.fixture
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _pump(app: QApplication, done, timeout: float = 5.0) -> None:
    end = time.time() + timeout
    while not done() and time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def test_probe_result_is_applied_on_the_gui_thread(app: QApplication, tmp_path: Path) -> None:
    for frame in (1001, 1002):
        _write_exr(tmp_path / f"shot.{frame}.exr")
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    tab = ConvertTab("exr2video", settings)

    seen: list[QThread] = []
    original = tab._apply_probe_result

    def spy(result: object) -> None:
        seen.append(QThread.currentThread())
        original(result)

    tab._apply_probe_result = spy  # type: ignore[method-assign]
    tab.set_input_async(str(tmp_path / "shot.1001.exr"))
    _pump(app, lambda: bool(seen))

    assert seen, "probe never finished"
    # Widgets may only be touched from the thread that owns them.
    assert seen[0] is app.thread()
    assert tab.is_ready() or tab.input_path.text()
    tab.deleteLater()
