"""Invisible watermark: payload template, Preferences, and the EXR → Video hook."""

from __future__ import annotations

import datetime as dt

import pytest

from src.core.trustmark.payload import expand_payload_template
from src.gui.preferences import (
    DEFAULT_INVISIBLE_WM_TEMPLATE,
    PreferencesDialog,
    invisible_wm_strength,
    invisible_wm_template,
    resolve_invisible_watermark,
    sequence_display_name,
)
from src.services.app_settings import Keys

_NOW = dt.datetime(2026, 10, 29, 14, 5)


def test_expand_tokens_and_cut_to_eight(monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "derek")
    out = expand_payload_template("{user}", now=_NOW)
    assert out.text == "derek" and not out.truncated
    out = expand_payload_template("ID{date}", now=_NOW)
    assert out.text == "ID261029"
    out = expand_payload_template("{src}{time}", source_name="shot010", now=_NOW)
    assert out.text == "shot0101" and out.truncated
    assert expand_payload_template("  ", now=_NOW).text == ""
    assert expand_payload_template("é{time}", now=_NOW).text == "1405"
    # Unknown braces stay literal.
    assert expand_payload_template("{x}", now=_NOW).text == "{x}"


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("/a/shot.1001.exr", "shot"),
        ("/a/shot_1001.dpx", "shot"),
        ("/a/plate", "plate"),
        ("/a/still.exr", "still"),
    ],
)
def test_sequence_display_name(path, name):
    assert sequence_display_name(path) == name


def test_defaults_and_resolve(settings):
    assert invisible_wm_template(settings) == DEFAULT_INVISIBLE_WM_TEMPLATE
    assert invisible_wm_strength(settings) == pytest.approx(1.0)
    settings.setValue(Keys.INVISIBLE_WM_TEMPLATE, "ACME{src}")
    settings.setValue(Keys.INVISIBLE_WM_STRENGTH, 0.8)
    logs: list[str] = []
    text, strength = resolve_invisible_watermark(settings, source_name="sh010", log=logs.append)
    assert text == "ACMEsh01"
    assert strength == pytest.approx(0.8)
    assert any("cut to 8" in line for line in logs)
    settings.setValue(Keys.INVISIBLE_WM_TEMPLATE, "")
    text, _ = resolve_invisible_watermark(settings, log=logs.append)
    assert text == ""


def test_preferences_dialog_saves_payload(qapp, settings):
    dlg = PreferencesDialog(settings)
    dlg._wm_template.setText("TEST1234")
    dlg._wm_strength.setValue(0.5)
    assert "TEST1234" in dlg._wm_preview.text()
    dlg._on_accept()
    assert invisible_wm_template(settings) == "TEST1234"
    assert invisible_wm_strength(settings) == pytest.approx(0.5)


def test_exr2video_tab_has_invisible_checkbox(qapp, settings):
    from src.gui.convert_tab import ConvertTab

    tab = ConvertTab("exr2video", settings)
    assert not tab.invisible_watermark_enabled()
    assert tab._invisible_wm_check is not None
    tab._invisible_wm_check.setChecked(True)
    assert tab.invisible_watermark_enabled()
    assert settings.value("exr2video/invisible_watermark", False, type=bool)
    other = ConvertTab("video2exr", settings)
    assert not other.invisible_watermark_enabled()
