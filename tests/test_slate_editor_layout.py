"""Slate editor on Card / FormGrid: switch cards keep the checkable-group behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QStatusTipEvent
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton

from src.services.slate_model import SlateModel


def _model(tmp_path: Path) -> SlateModel:
    settings = QSettings(str(tmp_path / "slate.ini"), QSettings.Format.IniFormat)
    return SlateModel(settings, "exr2video")


@pytest.fixture
def panel(tmp_path, qapp):
    from src.gui.slate_widgets import SlateFormPanel

    model = _model(tmp_path)
    form = SlateFormPanel(model)
    yield form, model
    form.deleteLater()


def test_cards_replace_group_boxes(panel) -> None:
    from src.gui.components import Card

    form, _model = panel
    for card in (form._slate_group, form._burnin_group, form._watermark_group):
        assert isinstance(card, Card)
    assert [c.title() for c in form.overlay_groups()] == ["Burn-in", "Watermark"]


@pytest.mark.parametrize(
    ("card_attr", "flag", "field_attr"),
    [
        ("_slate_group", "slate_enabled", "show_edit"),
        ("_burnin_group", "burnin_enabled", "burnin_top_left"),
        ("_watermark_group", "watermark_enabled", "watermark_text_edit"),
    ],
)
def test_switch_enables_body_and_writes_the_model(panel, card_attr, flag, field_attr) -> None:
    form, model = panel
    card = getattr(form, card_attr)
    field = getattr(form, field_attr)
    start = getattr(model, flag)
    assert card.isChecked() is start
    assert field.isEnabled() is start

    card._switch.click()
    assert getattr(model, flag) is (not start)
    assert field.isEnabled() is (not start)
    # The toggle is one undoable document step, as with the group box.
    model.undo_stack.undo()
    assert getattr(model, flag) is start
    assert card.isChecked() is start


def test_model_change_updates_switch(panel) -> None:
    form, model = panel
    model.set_burnin_enabled(not model.burnin_enabled)
    assert form._burnin_group.isChecked() is model.burnin_enabled
    assert form.burnin_top_left.isEnabled() is model.burnin_enabled


def test_fill_button_is_ghost(panel) -> None:
    form, _model = panel
    assert form._fill_burnin_btn.property("variant") == "ghost"


def test_dialog_bottom_row_shows_status_tips(tmp_path, qapp: QApplication) -> None:
    from src.gui.slate_widgets import SlateDialog

    dlg = SlateDialog(_model(tmp_path), mode="exr2video")
    try:
        assert dlg.minimumWidth() >= 1100 and dlg.minimumHeight() >= 600
        QApplication.sendEvent(dlg, QStatusTipEvent("Shot name"))
        assert dlg._status_bar.currentMessage() == "Shot name"
        assert dlg._status_bar.property("role") == "hint"
        box = dlg.findChild(QDialogButtonBox)
        ok = box.button(QDialogButtonBox.StandardButton.Ok)
        assert isinstance(ok, QPushButton) and ok.property("variant") == "primary"
    finally:
        dlg.reject()
        dlg.deleteLater()
