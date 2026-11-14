"""Settings dialogs are built from Card and FormGrid, like the convert tabs."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QWidget,
)

from src.gui.components import NARROW, WIDE_MIN, Card, FormGrid


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    QApplication.instance() or QApplication([])
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


def _shown(dlg: QDialog) -> QDialog:
    dlg.show()
    QApplication.processEvents()
    return dlg


def _form_labels(dlg: QWidget) -> list[str]:
    out: list[str] = []
    for form in dlg.findChildren(FormGrid):
        for row in range(form.rowCount()):
            item = form.itemAtPosition(row, 0)
            if item is not None and isinstance(item.widget(), QLabel):
                out.append(item.widget().text())
    return out


def _primary_ok(dlg: QDialog) -> QPushButton:
    box = dlg.findChild(QDialogButtonBox)
    assert box is not None
    ok = box.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None
    return ok


def _assert_tab_order_follows_reading_order(dlg: QDialog) -> None:
    """Focusable controls come up in the order they read: top to bottom, left to right."""
    box = dlg.findChild(QDialogButtonBox)
    seen: list[QWidget] = []
    start = dlg.nextInFocusChain()
    widget = start
    while True:
        if (
            widget is not dlg
            and widget.isVisibleTo(dlg)
            and widget.isEnabled()
            and widget.focusPolicy() & Qt.FocusPolicy.TabFocus
            and (box is None or not box.isAncestorOf(widget))
            # The line edit inside a spin box or combo is part of that control.
            and not isinstance(widget.parentWidget(), (QAbstractSpinBox, QComboBox))
            and widget not in seen
        ):
            seen.append(widget)
        widget = widget.nextInFocusChain()
        if widget is start:
            break
    placed = [(w.mapTo(dlg, w.rect().center()), w) for w in seen]
    for (a, wa), (b, wb) in zip(placed, placed[1:], strict=False):
        same_row = abs(a.y() - b.y()) <= 8
        ok = (a.x() < b.x()) if same_row else (a.y() < b.y())
        assert ok, f"{wa!r} ({a}) comes before {wb!r} ({b}) in the tab order"


# -- Codec / compression settings ----------------------------------------------


@pytest.mark.parametrize("key", ["h264", "hevc", "cineform"])
def test_codec_dialog_is_one_card_with_a_form(settings: QSettings, key: str) -> None:
    from src.gui.convert_tab import VideoCodecSettingsDialog

    dlg = _shown(VideoCodecSettingsDialog(key, settings))
    try:
        assert len(dlg.findChildren(Card)) == 1
        assert not dlg.findChildren(QGroupBox)
        labels = _form_labels(dlg)
        assert "Encode format" in labels
        if key == "cineform":
            assert "Quality" in labels
            assert dlg._cineform_quality.width() >= WIDE_MIN
        else:
            assert "CRF (quality)" in labels and "Preset" in labels
            assert dlg._preset.width() >= WIDE_MIN
            # Slider and its number box share one expanding WIDE field.
            assert dlg._crf_spin.parentWidget().width() >= WIDE_MIN
        assert all(not t.endswith(":") for t in labels)
        assert _primary_ok(dlg).property("variant") == "primary"
        _assert_tab_order_follows_reading_order(dlg)
    finally:
        dlg.close()


def test_codec_dialog_keeps_values_and_settings(settings: QSettings) -> None:
    from src.gui.convert_tab import VideoCodecSettingsDialog

    settings.setValue("codec_opts/hevc_crf", 22)
    settings.setValue("codec_opts/hevc_preset", "slow")
    dlg = VideoCodecSettingsDialog("hevc", settings)
    assert dlg.get_settings() == {"crf": "22", "preset": "slow"}
    dlg._crf_spin.setValue(30)
    dlg.accept()
    assert int(settings.value("codec_opts/hevc_crf")) == 30
    assert settings.value("codec_opts/hevc_preset") == "slow"


@pytest.mark.parametrize("key", ["dwaa", "zip"])
def test_compression_dialog_is_one_card_with_a_form(settings: QSettings, key: str) -> None:
    from src.gui.convert_tab import _EXR_COMPRESSION_HELP, ExrCompressionSettingsDialog

    dlg = _shown(ExrCompressionSettingsDialog(key, settings))
    try:
        cards = dlg.findChildren(Card)
        assert len(cards) == 1
        assert cards[0].title() == key.upper()
        assert cards[0].description() == _EXR_COMPRESSION_HELP[key]
        labels = _form_labels(dlg)
        assert labels == (["Compression level"] if key == "dwaa" else ["Zip level"])
        spin = dlg._dwa_spin if key == "dwaa" else dlg._zip_spin
        assert spin.parentWidget().width() >= WIDE_MIN
        assert _primary_ok(dlg).property("variant") == "primary"
        _assert_tab_order_follows_reading_order(dlg)
    finally:
        dlg.close()


def test_compression_dialog_saves_level(settings: QSettings) -> None:
    from src.gui.convert_tab import ExrCompressionSettingsDialog

    dlg = ExrCompressionSettingsDialog("dwab", settings)
    assert dlg.get_settings() == {"dwa_compression_level": "45"}
    dlg._dwa_spin.setValue(100)
    dlg.accept()
    assert float(settings.value("exr_opts/dwa_level")) == 100.0


# -- Preferences ------------------------------------------------------------------

PREFS_CARDS = [
    "Video player",
    "Slate",
    "Hardware acceleration",
    "Playback cache",
    "Invisible watermark",
    "Interface",
]


def test_preferences_cards_and_rows(settings: QSettings) -> None:
    from src.gui.preferences import PreferencesDialog

    dlg = _shown(PreferencesDialog(settings))
    try:
        assert not dlg.findChildren(QGroupBox)
        cards = dlg.findChildren(Card)
        assert [c.title() for c in cards] == PREFS_CARDS
        # Descriptions are one short plain line.
        for card in cards:
            assert len(card.description()) < 90, card.description()
            assert "<" not in card.description()
        # One label column for the whole dialog, as in the convert tabs.
        lefts = {
            w.mapTo(dlg, w.rect().topLeft()).x()
            for w in (dlg._thumb_row, dlg._encoder_combo, dlg._cache_slider.parentWidget())
        }
        assert len(lefts) == 1
        labels = [t for t in _form_labels(dlg) if t]
        for expected in (
            "Open with",
            "Thumbnail frame",
            "H.264 / HEVC encoding",
            "Decoding",
            "Budget",
            "Payload",
            "Strength",
        ):
            assert expected in labels
        assert all(not t.endswith(":") for t in labels)
        # Three short choices are radios in one row, in order.
        assert [r.text() for r in dlg._thumb_radios.values()] == ["First", "Middle", "Last"]
        assert sum(r.isChecked() for r in dlg._thumb_radios.values()) == 1
        xs = [r.mapTo(dlg, r.rect().topLeft()) for r in dlg._thumb_radios.values()]
        assert len({p.y() for p in xs}) == 1 and xs == sorted(xs, key=lambda p: p.x())
        for combo in (dlg._encoder_combo, dlg._decoder_combo):
            assert combo.width() >= WIDE_MIN
        assert dlg._wm_strength.width() == NARROW[1]
        # Budget slider and its read-out share one row.
        slider_y = dlg._cache_slider.mapTo(dlg, dlg._cache_slider.rect().center()).y()
        value_y = dlg._cache_value.mapTo(dlg, dlg._cache_value.rect().center()).y()
        assert abs(slider_y - value_y) <= 2
        assert _primary_ok(dlg).property("variant") == "primary"
    finally:
        dlg.close()


def test_preferences_keeps_the_detail_it_moved(settings: QSettings) -> None:
    from src.gui.preferences import PreferencesDialog

    dlg = PreferencesDialog(settings)
    text = " ".join(label.text() for label in dlg.findChildren(QLabel))
    assert "{user} {host} {date} {time} {src}" in text
    assert "Same OCIO viewer as sequence Preview" in text
    assert "IINA, VLC, mpv, ffplay" in text
    assert "slate editor and file-browser Preview" in text
    assert "printable ASCII" in text


def test_preferences_fits_a_small_screen(settings: QSettings) -> None:
    from PySide6.QtWidgets import QScrollArea

    from src.gui.preferences import PreferencesDialog

    dlg = _shown(PreferencesDialog(settings))
    try:
        assert dlg.height() <= 760
        scroll = dlg.findChild(QScrollArea)
        assert scroll is not None
        # Every card scrolls; the button row stays put below.
        assert all(scroll.widget().isAncestorOf(c) for c in dlg.findChildren(Card))
        assert not scroll.isAncestorOf(dlg.findChild(QDialogButtonBox))
    finally:
        dlg.close()


def test_preferences_tab_order(settings: QSettings) -> None:
    from src.gui.preferences import PreferencesDialog

    dlg = PreferencesDialog(settings)
    dlg.resize(dlg.width(), 2000)  # everything on screen at once
    _shown(dlg)
    try:
        dlg._custom_radio.setChecked(True)
        QApplication.processEvents()
        _assert_tab_order_follows_reading_order(dlg)
    finally:
        dlg.close()


def test_preferences_custom_path_follows_the_radio(settings: QSettings) -> None:
    from src.gui.preferences import PreferencesDialog

    dlg = PreferencesDialog(settings)
    assert dlg._builtin_radio.isChecked()
    assert not dlg._path_edit.isEnabled() and not dlg._browse_btn.isEnabled()
    dlg._custom_radio.setChecked(True)
    assert dlg._path_edit.isEnabled() and dlg._browse_btn.isEnabled()


# -- Quick Look ------------------------------------------------------------------


def test_quicklook_dialog_is_two_cards(settings: QSettings) -> None:
    from src.gui.quicklook_dialog import QuickLookDialog

    dlg = _shown(QuickLookDialog(settings))
    try:
        assert not dlg.findChildren(QGroupBox)
        cards = dlg.findChildren(Card)
        assert [c.title() for c in cards] == ["Quick Look", "Formats"]
        for card in cards:
            assert 0 < len(card.description()) < 90
        assert dlg._formats_box is cards[1]
        labels = [t for t in _form_labels(dlg) if t]
        assert "Status" in labels
        assert all(not t.endswith(":") for t in labels)
        text = " ".join(label.text() for label in dlg.findChildren(QLabel))
        assert "Off until you turn it on here" in text
        assert "numbered frames play in the preview" in text
        assert "Finder keeps its own preview" in text
        assert _primary_ok(dlg).property("variant") == "primary"
        dlg.enable_check.setChecked(True)
        QApplication.processEvents()
        _assert_tab_order_follows_reading_order(dlg)
    finally:
        dlg.close()


# -- About -----------------------------------------------------------------------


def test_about_is_one_card_with_ghost_notice_buttons(settings: QSettings) -> None:
    from PySide6.QtWidgets import QTextBrowser

    from src.core.constants import APP_VERSION
    from src.gui.window import AboutDialog

    dlg = _shown(AboutDialog())
    try:
        cards = dlg.findChildren(Card)
        assert [c.title() for c in cards] == ["EXR Converter"]
        assert 0 < len(cards[0].description()) < 90
        labels = [label.text() for label in dlg.findChildren(QLabel)]
        assert f"Version {APP_VERSION}" in labels
        assert {"Author", "Links", "Libraries", "Notices"} <= set(_form_labels(dlg))
        text = " ".join(labels)
        assert "Derek Rein" in text
        assert 'href="https://derekvfx.ca"' in text
        assert 'href="https://vfx.systems"' in text
        assert "OpenColorIO" in text and "PySide6" in text
        html = dlg.findChild(QTextBrowser).toHtml()
        for kept in ("ACES Studio Config v4", "MIT License", "Adobe TrustMark", "DNG technology"):
            assert kept in html
        notices = [b for b in cards[0].findChildren(QPushButton) if "notice" in b.text()]
        assert len(notices) >= 2
        assert all(b.property("variant") == "ghost" for b in notices)
        assert _primary_ok(dlg).property("variant") == "primary"
        _assert_tab_order_follows_reading_order(dlg)
    finally:
        dlg.close()


def test_preferences_buttons_are_cancel_then_ok_like_the_main_window(qapp) -> None:
    from PySide6.QtCore import QSettings

    from src.gui.preferences import PreferencesDialog

    dlg = _shown(PreferencesDialog(QSettings("VFXTools", "EXRConverterFooterTest")))
    try:
        box = dlg.findChild(QDialogButtonBox)
        ok = box.button(QDialogButtonBox.StandardButton.Ok)
        cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
        ok_x = ok.mapTo(dlg, ok.rect().topLeft()).x()
        cancel_x = cancel.mapTo(dlg, cancel.rect().topLeft()).x()
        assert cancel_x < ok_x
        assert ok_x + ok.width() > dlg.width() - 40  # primary at the right edge
    finally:
        dlg.close()
