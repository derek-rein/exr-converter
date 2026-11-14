"""Tinted UI icons from the bundled Lucide glyphs.

The glyphs ship as white 48 px PNGs under ``:/ui/`` (rasterised by
``scripts/build_icons.py``; release builds have no QtSvg). :func:`icon` tints
one with a palette colour and returns a ``QIcon`` that also carries a
disabled variant, so a button needs no extra code to grey out.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractButton

from .style import color

ICON_SIZE = 16

_cache: dict[tuple[str, str], QIcon] = {}


def _tinted(name: str, hex_color: str) -> QPixmap:
    src = QPixmap(f":/ui/{name}.png")
    if src.isNull():
        return src
    out = QPixmap(src.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawPixmap(0, 0, src)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(out.rect(), QColor(hex_color))
    painter.end()
    return out


def icon(name: str, token: str = "FG") -> QIcon:
    """Icon *name* (a Lucide name such as ``"rotate-ccw"``) in palette colour *token*."""
    key = (name, token)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    result = QIcon()
    normal = _tinted(name, color(token))
    if not normal.isNull():
        result.addPixmap(normal, QIcon.Mode.Normal)
        result.addPixmap(_tinted(name, color("FG_DISABLED")), QIcon.Mode.Disabled)
    _cache[key] = result
    return result


def _remember(button: QAbstractButton, name: str, token: str) -> None:
    """Record what the button shows, so a theme change can re-tint it."""
    button.setProperty("iconName", name)
    button.setProperty("iconToken", token)


def refresh_icons() -> None:
    """Drop the tinted icons and re-tint every button that shows one (theme change)."""
    from PySide6.QtWidgets import QApplication

    _cache.clear()
    for widget in QApplication.allWidgets():
        if not isinstance(widget, QAbstractButton):
            continue
        name = widget.property("iconName")
        token = widget.property("iconToken")
        if name and token:
            widget.setIcon(icon(str(name), str(token)))


def set_button_icon(
    button: QAbstractButton, name: str, token: str = "FG", size: int = ICON_SIZE
) -> None:
    """Put icon *name* on *button* and drop its glyph text."""
    button.setText("")
    button.setIcon(icon(name, token))
    button.setIconSize(QSize(size, size))
    _remember(button, name, token)


def set_button_icon_keep_text(
    button: QAbstractButton, name: str, token: str = "FG_DIM", size: int = 12
) -> None:
    """Put icon *name* beside the button's label (disclosure chevrons)."""
    button.setIcon(icon(name, token))
    button.setIconSize(QSize(size, size))
    _remember(button, name, token)
