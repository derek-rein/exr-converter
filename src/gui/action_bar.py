"""Widgets of the main window's action bar: progress line, status, primary button."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QEnterEvent, QFont, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QSizePolicy, QWidget

from . import motion
from .style import color, set_style_property

PROGRESS_LINE_HEIGHT = 3


class ProgressLine(QProgressBar):
    """A thin accent line that glides to each new value.

    It is still a ``QProgressBar`` (range, value, ``setRange(0, 0)`` for busy),
    so callers and tests keep the same API. Only the drawing differs: no box
    and no percentage, since the status text beside it carries the numbers.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextVisible(False)
        self.setFixedHeight(PROGRESS_LINE_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._shown = motion.Animator(self, lambda _v: self.update(), duration_ms=motion.BASE)
        self._busy_phase = 0.0
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(16)
        self._busy_timer.timeout.connect(self._advance_busy)
        self.valueChanged.connect(self._on_value_changed)

    def _fraction(self) -> float:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return 0.0
        return (self.value() - self.minimum()) / span

    def _is_busy(self) -> bool:
        return self.maximum() == self.minimum()

    def _on_value_changed(self, _value: int) -> None:
        target = self._fraction()
        # Going backwards is a reset, not progress: snap.
        if target < self._shown.value:
            self._shown.set(target)
        else:
            self._shown.to(target)

    def _advance_busy(self) -> None:
        self._busy_phase = (self._busy_phase + 0.012) % 1.0
        self.update()

    def _sync_busy_timer(self) -> None:
        want = self._is_busy() and self.isVisible() and not motion.reduced()
        if want and not self._busy_timer.isActive():
            self._busy_timer.start()
        elif not want and self._busy_timer.isActive():
            self._busy_timer.stop()

    def setRange(self, minimum: int, maximum: int) -> None:
        super().setRange(minimum, maximum)
        self._sync_busy_timer()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_busy_timer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sync_busy_timer()

    def sizeHint(self) -> QSize:
        return QSize(120, PROGRESS_LINE_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, PROGRESS_LINE_HEIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width, height = float(self.width()), float(self.height())
        radius = height / 2
        painter.setBrush(QColor(color("BORDER")))
        painter.drawRoundedRect(QRectF(0, 0, width, height), radius, radius)
        painter.setBrush(QColor(color("ACCENT")))
        if self._is_busy():
            if motion.reduced():
                start, end = 0.0, 1.0
            else:
                start, end = motion.indeterminate_segment(self._busy_phase)
            painter.drawRoundedRect(
                QRectF(start * width, 0, (end - start) * width, height), radius, radius
            )
            return
        filled = self._shown.value * width
        if filled > 0:
            painter.drawRoundedRect(QRectF(0, 0, max(filled, height), height), radius, radius)


class StatusLine(QLabel):
    """One line of status text with ``QStatusBar``-style ``showMessage``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        set_style_property(self, "role", "muted")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clearMessage)

    def showMessage(self, text: str, timeout: int = 0) -> None:  # noqa: N802 - Qt naming
        self._timer.stop()
        self._full = text
        self._elide()
        if timeout > 0:
            self._timer.start(timeout)

    def clearMessage(self) -> None:  # noqa: N802 - Qt naming
        self._timer.stop()
        self._full = ""
        self._elide()

    def currentMessage(self) -> str:  # noqa: N802 - Qt naming
        return self._full

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        self.setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width()))
        self.setToolTip(self._full if self.text() != self._full else "")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()


class PrimaryButton(QPushButton):
    """The accent button, painted here so hover and press can animate.

    Hover blends the fill, press darkens it and shrinks the face a touch.
    Both run on :class:`motion.Animator`, so leaving mid-hover reverses from
    the colour on screen.
    """

    _RADIUS = 5.0
    _PRESS_SCALE = 0.97

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = motion.Animator(self, lambda _v: self.update(), duration_ms=motion.FAST)
        self._press = motion.Animator(self, lambda _v: self.update(), duration_ms=motion.INSTANT)

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        if self.isEnabled():
            self._hover.to(1.0)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._hover.to(0.0)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)
        if self.isDown():
            self._press.to(1.0)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        self._press.to(0.0)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if self.isEnabled()
                else Qt.CursorShape.ArrowCursor
            )
            if not self.isEnabled():
                self._hover.set(0.0)
                self._press.set(0.0)
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        if self.isEnabled():
            fill = motion.mix_hex(color("ACCENT"), color("ACCENT_HOVER"), self._hover.value)
            fill = motion.mix_hex(fill, color("ACCENT_DARK"), self._press.value)
            text = color("ACCENT_FG")
            edge = color("ACCENT_DARK")
        else:
            fill, text, edge = color("BG_BTN"), color("FG_DISABLED"), color("BORDER")

        scale = motion.lerp(1.0, self._PRESS_SCALE, self._press.value)
        if scale != 1.0:
            centre = rect.center()
            painter.translate(centre)
            painter.scale(scale, scale)
            painter.translate(-centre)

        painter.setPen(QColor(edge))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

        if self.hasFocus() and self.isEnabled():
            ring = QColor(color("SEL_FG"))
            ring.setAlpha(200)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), self._RADIUS - 2, self._RADIUS - 2)

        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(text))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
