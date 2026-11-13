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
PALETTE: dict[str, str] = {
    # Surfaces, darkest to lightest.
    "BG_INPUT": "#1a1a1a",  # fields, lists, the log: things you type or read in
    "BG": "#252525",  # window
    "BG_ALT": "#2d2d2d",  # menus, headers, raised strips
    "BG_BTN": "#383838",
    "BG_BTN_HOVER": "#434343",
    "BG_BTN_PRESSED": "#4e4e4e",
    # Text. FG_DIM is 5.6:1 on BG, so 11 px hints stay readable.
    "FG": "#dcdcdc",
    "FG_DIM": "#9a9a9a",
    "FG_DISABLED": "#6a6a6a",
    # Lines.
    "BORDER": "#3a3a3a",
    "BORDER_STRONG": "#555555",  # hover edge on a control
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
    # Viewer surround stays pure black.
    "VIEWER_BG": "#000000",
}

# Kept for modules that still import the old private name.
_PALETTE = PALETTE


def color(name: str) -> str:
    """Hex value of a palette token, e.g. ``color("FG_DIM")``."""
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

# ---------------------------------------------------------------------------
# Inline style snippets for dynamic status labels (not in the .qss)
# ---------------------------------------------------------------------------
STATUS_OK = f"color: {_PALETTE['SUCCESS']}; font-size: 11px; padding: 2px 0;"
STATUS_ERR = f"color: {_PALETTE['ERROR']}; font-size: 11px; padding: 2px 0;"
STATUS_DIM = f"font-size: 11px; padding: 2px 0; color: {_PALETTE['FG_DIM']};"
HINT_STYLE = f"font-size: 10px; color: {_PALETTE['FG_DIM']};"
DESC_STYLE = f"color: {_PALETTE['FG_DIM']}; font-size: 11px; padding: 4px 0;"


def apply_app_palette(app) -> None:
    """Colours a stylesheet cannot reach: links in rich-text labels and browsers."""
    from PySide6.QtGui import QColor, QPalette

    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Link, QColor(PALETTE["ACCENT_HOVER"]))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(PALETTE["ACCENT"]))
    app.setPalette(palette)


_cache: str | None = None


def load_stylesheet() -> str:
    """Read :/style.qss, substitute ``@VAR`` tokens, and return CSS text.

    The result is cached after the first call.
    """
    global _cache  # noqa: PLW0603
    if _cache is not None:
        return _cache
    f = QFile(":/style.qss")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
        return ""
    # QByteArray → bytes: use .data() for a clean buffer view on all PySide versions.
    raw = bytes(f.readAll().data()).decode("utf-8")
    f.close()
    _cache = _VAR_RE.sub(lambda m: _PALETTE.get(m.group(1), m.group(0)), raw)
    return _cache
