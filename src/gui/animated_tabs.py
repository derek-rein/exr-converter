"""Tab bar whose selection indicator slides between tabs."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import QTabBar, QWidget

from . import motion
from .style import color

_INDICATOR_HEIGHT = 2
_INDICATOR_INSET = 10  # shorter than the tab, so it reads as an underline for the label


class SlidingTabBar(QTabBar):
    """``QTabBar`` that draws one accent underline and animates it on change.

    The stylesheet styles the tabs themselves; only the indicator is painted
    here. It moves from the tab it was under, in a straight line, which tells
    the eye which way the content went.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._from = QRectF()
        self._to = QRectF()
        self._progress = motion.Animator(
            self, lambda _t: self.update(), value=1.0, duration_ms=motion.BASE
        )
        self.currentChanged.connect(self._on_current_changed)

    def _target_rect(self) -> QRectF:
        index = self.currentIndex()
        if index < 0:
            return QRectF()
        rect = QRectF(self.tabRect(index))
        return QRectF(
            rect.left() + _INDICATOR_INSET,
            rect.bottom() - _INDICATOR_HEIGHT + 1,
            max(0.0, rect.width() - 2 * _INDICATOR_INSET),
            _INDICATOR_HEIGHT,
        )

    def _shown_rect(self) -> QRectF:
        t = self._progress.value
        if self._from.isNull() or t >= 1.0:
            return self._to
        return QRectF(
            motion.lerp(self._from.left(), self._to.left(), t),
            self._to.top(),
            motion.lerp(self._from.width(), self._to.width(), t),
            self._to.height(),
        )

    def _on_current_changed(self, _index: int) -> None:
        # Start from where the indicator is now, so a second click mid-slide
        # redirects it without a jump.
        self._from = self._shown_rect()
        self._to = self._target_rect()
        if self._from.isNull() or not self.isVisible():
            self._progress.set(1.0)
            return
        self._progress.set(0.0)
        self._progress.to(1.0)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._to = self._target_rect()
        self._progress.set(1.0)

    def tabLayoutChange(self) -> None:
        super().tabLayoutChange()
        self._to = self._target_rect()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        rect = self._shown_rect()
        if rect.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("ACCENT")))
        painter.drawRoundedRect(rect, 1.0, 1.0)
