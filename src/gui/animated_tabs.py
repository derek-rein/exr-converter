"""Tab bar whose selection indicator slides between tabs."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt
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
        self._instant = False
        self._last_instant = False
        # Selected-state widths by label, and the probe bar that measures them.
        self._selected_widths: dict[str, int] = {}
        self._probe: QTabBar | None = None
        self.currentChanged.connect(self._on_current_changed)

    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802
        """Every tab is as wide as it is when selected (bold), so selecting never shifts.

        The stylesheet makes the selected label bold, which is wider. Qt sizes
        each tab for its own state, so an unselected tab would grow by the
        difference the moment it is picked, and push its neighbours along. The
        selected width comes from the style itself (a probe bar with that tab
        selected), not from a font estimate, so it matches to the pixel.
        """
        hint = super().tabSizeHint(index)
        selected = self._selected_width(self.tabText(index))
        if selected > hint.width():
            hint.setWidth(selected)
        return hint

    def _selected_width(self, text: str) -> int:
        """Width the style gives a tab with *text* when it is the selected one."""
        cached = self._selected_widths.get(text)
        if cached is not None:
            return cached
        # The probe takes the application's style and stylesheet, as this bar does.
        probe = self._probe
        if probe is None:
            probe = self._probe = QTabBar()
            probe.addTab("")
        probe.setStyleSheet(self.styleSheet())
        probe.setFont(self.font())
        probe.setTabText(0, text)
        probe.setCurrentIndex(0)
        probe.ensurePolished()
        width = probe.tabSizeHint(0).width()
        self._selected_widths[text] = width
        return width

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() in (
            QEvent.Type.StyleChange,
            QEvent.Type.FontChange,
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.PaletteChange,
        ):
            widths = self.__dict__.get("_selected_widths")  # not set yet during __init__
            if widths is not None:
                widths.clear()
        super().changeEvent(event)

    def set_current_instant(self, index: int) -> None:
        """Change tab with no slide: for keyboard shortcuts.

        An action someone repeats from the keyboard should land at once;
        motion there reads as lag.
        """
        self._instant = True
        try:
            self.setCurrentIndex(index)
        finally:
            self._instant = False

    def last_change_was_instant(self) -> bool:
        return self._last_instant

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
        self._last_instant = self._instant
        if self._instant or self._from.isNull() or not self.isVisible():
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
