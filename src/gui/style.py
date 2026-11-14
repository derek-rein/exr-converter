"""Load the compiled QSS stylesheet from Qt resources with variable substitution.

The .qss file uses ``@VARNAME`` placeholders that are replaced at load time
from the palette dict below.  This is the standard workaround for QSS not
supporting CSS custom properties.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# Design tokens  (edit colours here — they propagate everywhere)
#
# Neutral greys on purpose: people judge colour next to this window, so the
# chrome carries no tint. Orange is the only hue and it means one of three
# things: the primary action, the current selection, or keyboard focus.
# ---------------------------------------------------------------------------
DARK: dict[str, str] = {
    # Surfaces, darkest to lightest.
    "BG_INPUT": "#171717",  # fields, lists, the log: things you type or read in
    "BG": "#1e1e1e",  # window
    "BG_CARD": "#262626",  # cards: one step up from the window
    "BG_ALT": "#2d2d2d",  # menus, headers, raised strips
    "BG_BTN": "#353535",
    "BG_BTN_HOVER": "#404040",
    "BG_BTN_PRESSED": "#4b4b4b",
    # Text. FG_DIM is 5.6:1 on BG, so 11 px hints stay readable.
    "FG": "#dcdcdc",
    "FG_DIM": "#9a9a9a",
    "FG_DISABLED": "#6a6a6a",
    # Lines.
    "BORDER": "#363636",
    "BORDER_STRONG": "#525252",  # hover edge on a control
    # Accent (Nuke orange). ACCENT_FG is the text colour on an ACCENT fill.
    "ACCENT": "#e08a2e",
    "ACCENT_HOVER": "#eb9a45",
    "ACCENT_DARK": "#b86c1c",
    "ACCENT_FG": "#1a1206",
    # Selected rows: a quiet tint, so a selection does not out-shout the image.
    "SEL_BG": "#5a3d18",
    "SEL_BG_HOVER": "#6b4a1f",
    "SEL_FG": "#ffffff",
    # Status.
    "ERROR": "#e06c6c",
    "ERROR_BG": "#3a2020",
    "ERROR_BORDER": "#a04040",
    "WARNING": "#e8c060",
    "WARNING_BG": "#3a3020",
    "WARNING_BORDER": "#8a6a30",
    "SUCCESS": "#6cc070",
    "SCROLLBAR": "#4a4a4a",
    "SCROLLBAR_HOVER": "#606060",
    "LINK": "#eb9a45",  # links in rich text
    # Viewer surround stays pure black, in both themes.
    "VIEWER_BG": "#000000",
}

# The light theme: the same roles, one step per surface, the same single hue.
# Surfaces run lightest (fields) to darkest (menus); FG_DIM is 5.6:1 on BG.
LIGHT: dict[str, str] = {
    "BG_INPUT": "#ffffff",
    "BG": "#ececec",
    "BG_CARD": "#f7f7f7",
    "BG_ALT": "#e0e0e0",
    "BG_BTN": "#e4e4e4",
    "BG_BTN_HOVER": "#d8d8d8",
    "BG_BTN_PRESSED": "#cccccc",
    "FG": "#1f1f1f",
    "FG_DIM": "#5c5c5c",
    "FG_DISABLED": "#9a9a9a",
    "BORDER": "#d2d2d2",
    "BORDER_STRONG": "#a6a6a6",
    "ACCENT": "#e08a2e",
    "ACCENT_HOVER": "#eb9a45",
    "ACCENT_DARK": "#b86c1c",
    "ACCENT_FG": "#1a1206",
    "SEL_BG": "#f5d7ae",
    "SEL_BG_HOVER": "#efc78d",
    "SEL_FG": "#1f1f1f",
    "ERROR": "#b83232",
    "ERROR_BG": "#f8e0e0",
    "ERROR_BORDER": "#d18a8a",
    "WARNING": "#8a5a00",
    "WARNING_BG": "#faefd0",
    "WARNING_BORDER": "#d4b060",
    "SUCCESS": "#25702a",
    "SCROLLBAR": "#bcbcbc",
    "SCROLLBAR_HOVER": "#9c9c9c",
    "LINK": "#a85200",
    "VIEWER_BG": "#000000",
}

THEMES: dict[str, dict[str, str]] = {"dark": DARK, "light": LIGHT}
THEME_MODES = ("system", "light", "dark")
DEFAULT_THEME_MODE = "system"
FALLBACK_THEME = "dark"  # when the system will not say

# The live palette. One dict, updated in place by set_theme(), so modules that
# imported it by name see the change.
PALETTE: dict[str, str] = dict(DARK)

# Kept for modules that still import the old private name.
_PALETTE = PALETTE


def color(name: str) -> str:
    """Hex value of a palette token in the current theme, e.g. ``color("FG_DIM")``."""
    return PALETTE[name]


def set_style_property(widget: QWidget, name: str, value: str | bool) -> None:
    """Set a dynamic property the stylesheet selects on, and re-polish.

    Use this in place of an inline ``setStyleSheet``: the look stays in
    ``style.qss`` (``QLabel[role="hint"]``, ``QToolButton[state="invalid"]``).
    """
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


_VAR_RE = re.compile(r"@([A-Z_]+)")


def apply_app_palette(app) -> None:
    """Colours a stylesheet cannot reach: links in rich-text labels and browsers."""
    from PySide6.QtGui import QColor, QPalette

    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Link, QColor(PALETTE["LINK"]))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(PALETTE["ACCENT_DARK"]))
    app.setPalette(palette)


_cache: dict[str, str] = {}
_theme = FALLBACK_THEME
_mode = DEFAULT_THEME_MODE
_listeners: list = []
_applying = False


def current_theme() -> str:
    """``"dark"`` or ``"light"``: the theme in use now."""
    return _theme


def current_mode() -> str:
    """``"system"``, ``"light"`` or ``"dark"``: what the user chose."""
    return _mode


def normalize_mode(mode: object) -> str:
    text = str(mode or "").strip().lower()
    return text if text in THEME_MODES else DEFAULT_THEME_MODE


def resolve_theme(mode: str, system: str | None) -> str:
    """The theme to use for *mode* when the system reports *system*.

    ``system`` is ``"dark"``, ``"light"`` or None (the platform cannot tell).
    An explicit choice wins; "system" follows the platform and falls back to dark.
    """
    mode = normalize_mode(mode)
    if mode in ("light", "dark"):
        return mode
    return system if system in THEMES else FALLBACK_THEME


def system_theme() -> str | None:
    """The platform's colour scheme as ``"dark"`` / ``"light"``, or None if unknown."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    hints = QGuiApplication.styleHints()
    if hints is None:
        return None
    scheme = hints.colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    if scheme == Qt.ColorScheme.Light:
        return "light"
    return None


def set_theme(name: str) -> None:
    """Make *name* the current palette (in place). Does not touch any widget."""
    global _theme  # noqa: PLW0603
    if name not in THEMES:
        name = FALLBACK_THEME
    PALETTE.clear()
    PALETTE.update(THEMES[name])
    _theme = name


def on_theme_changed(callback) -> None:
    """Call *callback()* after every theme switch (for widgets that cache colours)."""
    _listeners.append(callback)


def apply_theme(app, mode: object = None) -> str:
    """Switch *app* to *mode* (``system`` / ``light`` / ``dark``) and return the theme.

    Sets the palette tokens, the stylesheet, the application palette and Qt's own
    colour scheme (which also turns the Windows title bar), then refreshes icons
    and every listener. Safe to call while windows are open.
    """
    global _mode, _applying  # noqa: PLW0603
    from PySide6.QtCore import Qt

    _mode = normalize_mode(_mode if mode is None else mode)
    _applying = True
    try:
        hints = app.styleHints()
        if _mode == "system":
            hints.setColorScheme(Qt.ColorScheme.Unknown)  # follow the platform again
        else:
            hints.setColorScheme(Qt.ColorScheme.Dark if _mode == "dark" else Qt.ColorScheme.Light)
        name = resolve_theme(_mode, system_theme() if _mode == "system" else None)
        set_theme(name)
        app.setStyleSheet(load_stylesheet())
        apply_app_palette(app)
        from .icons import refresh_icons

        refresh_icons()
        for callback in list(_listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001 - one widget must not block the rest
                pass
    finally:
        _applying = False
    return name


def install_theme(app, settings) -> str:
    """Apply the saved theme at start-up and follow the system when it changes."""
    global _mode  # noqa: PLW0603
    _mode = normalize_mode(settings.value("ui/theme", DEFAULT_THEME_MODE))
    name = apply_theme(app)

    def _system_changed(*_args) -> None:
        if _mode == "system" and not _applying:
            apply_theme(app)

    app.styleHints().colorSchemeChanged.connect(_system_changed)
    return name


def load_stylesheet() -> str:
    """Read :/style.qss, substitute ``@VAR`` tokens, and return CSS text.

    Cached per theme.
    """
    cached = _cache.get(_theme)
    if cached is not None:
        return cached
    f = QFile(":/style.qss")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
        return ""
    # QByteArray → bytes: use .data() for a clean buffer view on all PySide versions.
    raw = bytes(f.readAll().data()).decode("utf-8")
    f.close()
    text = _VAR_RE.sub(lambda m: PALETTE.get(m.group(1), m.group(0)), raw)
    _cache[_theme] = text
    return text
