"""OCIO config panel: the custom file can be picked again while it is selected."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog

from src.core.ocio_utils import OCIO_SOURCE_FILE
from src.gui.color_widgets import OcioConfigPanel


@pytest.fixture
def panel(tmp_path: Path):
    QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    p = OcioConfigPanel(settings)
    yield p
    p.deleteLater()


def _file_index(panel: OcioConfigPanel) -> int:
    combo = panel._source_combo
    return next(i for i in range(combo.count()) if combo.itemData(i) == OCIO_SOURCE_FILE)


def test_activating_the_selected_custom_file_opens_the_picker_again(
    panel: OcioConfigPanel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a.ocio"
    second = tmp_path / "b.ocio"
    first.write_text("ocio_profile_version: 2\n")
    second.write_text("ocio_profile_version: 2\n")
    assert panel.set_custom_config_file(str(first))
    idx = _file_index(panel)
    assert panel._source_combo.currentIndex() == idx

    asked: list[str] = []

    def fake_open(*_args, **_kwargs):
        asked.append("open")
        return str(second), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_open))
    changed: list[bool] = []
    panel.config_changed.connect(lambda: changed.append(True))

    # Choosing the row that is already current fires activated only.
    panel._source_combo.activated.emit(idx)

    assert asked == ["open"]
    assert panel._file_path == str(second)
    assert changed == [True]


def test_switching_to_the_custom_file_asks_once(
    panel: OcioConfigPanel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "c.ocio"
    target.write_text("ocio_profile_version: 2\n")
    asked: list[str] = []

    def fake_open(*_args, **_kwargs):
        asked.append("open")
        return str(target), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_open))
    idx = _file_index(panel)
    assert panel._source_combo.currentIndex() != idx

    # A real pick: the index changes, then Qt emits activated for the same row.
    panel._source_combo.setCurrentIndex(idx)
    panel._source_combo.activated.emit(idx)

    assert asked == ["open"]
