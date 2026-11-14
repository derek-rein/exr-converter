from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src.gui import style


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture
def restore_theme():
    yield
    style.set_theme("dark")
    style._mode = style.DEFAULT_THEME_MODE


def test_both_themes_define_every_token() -> None:
    assert set(style.LIGHT) == set(style.DARK)
    qss = Path("resources/style.qss").read_text(encoding="utf-8")
    used = set(re.findall(r"@([A-Z_]+)", qss)) - {"VARNAME"}  # VARNAME is prose in a comment
    assert used <= set(style.DARK), used - set(style.DARK)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_text_is_readable_on_every_surface(theme: str) -> None:
    p = style.THEMES[theme]
    for surface in ("BG", "BG_CARD", "BG_INPUT"):
        assert _contrast(p["FG"], p[surface]) >= 7, (theme, surface)
        assert _contrast(p["FG_DIM"], p[surface]) >= 4.5, (theme, surface)
    for status in ("ERROR", "WARNING", "SUCCESS", "LINK"):
        assert _contrast(p[status], p["BG"]) >= 4.5, (theme, status)
    assert _contrast(p["ACCENT_FG"], p["ACCENT"]) >= 6.5


def test_system_follows_the_platform_and_falls_back_to_dark() -> None:
    assert style.resolve_theme("system", "light") == "light"
    assert style.resolve_theme("system", "dark") == "dark"
    assert style.resolve_theme("system", None) == "dark"
    assert style.resolve_theme("", None) == "dark"  # unknown mode means system
    assert style.resolve_theme("garbage", "light") == "light"
    assert style.resolve_theme("light", "dark") == "light"
    assert style.resolve_theme("dark", "light") == "dark"
    assert style.DEFAULT_THEME_MODE == "system"


def test_switching_updates_tokens_stylesheet_and_icons(restore_theme) -> None:
    import src.rc_resources  # noqa: F401
    from src.gui import icons

    app = QApplication.instance() or QApplication([])
    style.apply_theme(app, "dark")
    dark_sheet = style.load_stylesheet()
    dark_icon = icons.icon("check", "FG")
    assert style.color("BG") == style.DARK["BG"]

    style.apply_theme(app, "light")
    assert style.current_theme() == "light"
    assert style.color("BG") == style.LIGHT["BG"]
    assert style.PALETTE is style._PALETTE  # same dict: importers see the change
    assert style.LIGHT["BG"] in style.load_stylesheet()
    assert style.load_stylesheet() != dark_sheet
    assert icons.icon("check", "FG") is not dark_icon  # the tint cache was dropped


def test_listeners_run_after_a_switch(restore_theme) -> None:
    app = QApplication.instance() or QApplication([])
    seen: list[str] = []
    style.on_theme_changed(lambda: seen.append(style.current_theme()))
    style.apply_theme(app, "light")
    assert seen[-1] == "light"


def test_buttons_with_icons_are_re_tinted(restore_theme) -> None:
    from PySide6.QtWidgets import QPushButton

    import src.rc_resources  # noqa: F401
    from src.gui.icons import set_button_icon

    app = QApplication.instance() or QApplication([])
    style.apply_theme(app, "dark")
    btn = QPushButton()
    set_button_icon(btn, "check", "FG")
    before = btn.icon().cacheKey()
    style.apply_theme(app, "light")
    assert btn.icon().cacheKey() != before


def test_saved_choice_is_applied_at_startup(restore_theme, tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/theme", "light")
    assert style.install_theme(app, settings) == "light"
    assert style.current_mode() == "light"
