"""Shared helpers of the media browser base dialog."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPixmap

from src.gui.browser_chrome import _SEQ_THUMB_ICON
from src.gui.media_browser import _letterbox_thumb


def test_letterbox_thumb_centres_on_background(qapp) -> None:
    wide = QPixmap(400, 100)  # 4:1 into the 160x100 slot -> 160x40 band
    wide.fill(QColor(255, 0, 0))
    out = _letterbox_thumb(wide, QColor(28, 28, 32)).toImage()
    assert out.size() == _SEQ_THUMB_ICON
    assert out.pixelColor(80, 50) == QColor(255, 0, 0)
    assert out.pixelColor(80, 5) == QColor(28, 28, 32)
    assert out.pixelColor(80, 95) == QColor(28, 28, 32)
