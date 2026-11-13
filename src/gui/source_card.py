"""The input as an object: a drop target when empty, a clip card when loaded.

A path in a text field says where a file is. The card says what it is: a
frame from it, its name, its size and length. Clicking it browses, dropping
on it loads, and it never changes height, so the form below does not jump.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRectF, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import motion
from .browser_chrome import _ThumbJob, _ThumbSignals
from .icons import icon
from .style import color

CARD_HEIGHT = 84
_RADIUS = 7.0
_PAD = 10
_THUMB_W = 114
_THUMB_H = 64
_SEPARATOR = "  ·  "


class SourceCard(QWidget):
    """Shows the current source, or invites one."""

    clicked = Signal()

    def __init__(
        self,
        empty_title: str,
        empty_hint: str = "or click to browse",
        empty_icon: str = "film",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._empty_title = empty_title
        self._empty_hint = empty_hint
        self._empty_icon = empty_icon
        self._title = ""
        self._details: list[str] = []
        self._thumb: QImage | None = None
        self._thumb_path = ""
        self._thumb_gen = 0
        self._signals = _ThumbSignals(self)
        self._signals.ready.connect(self._on_thumb_ready)

        self.setFixedHeight(CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(empty_title)

        repaint = lambda _v: self.update()  # noqa: E731
        self._hover = motion.Animator(self, repaint, duration_ms=motion.FAST)
        self._drop = motion.Animator(self, repaint, duration_ms=motion.FAST)
        self._thumb_fade = motion.Animator(self, repaint, duration_ms=motion.BASE)
        self._content_fade = motion.Animator(self, repaint, value=1.0, duration_ms=motion.BASE)

    # -- State ---------------------------------------------------------------

    def is_empty(self) -> bool:
        return not self._title

    def title(self) -> str:
        return self._title

    def details(self) -> list[str]:
        return list(self._details)

    def set_empty(self) -> None:
        if self.is_empty():
            return
        self._title = ""
        self._details = []
        self._thumb = None
        self._thumb_path = ""
        self._thumb_gen += 1
        self.setAccessibleName(self._empty_title)
        self.setToolTip("")
        self._content_fade.set(0.0)
        self._content_fade.to(1.0)

    def set_source(self, title: str, details: Sequence[str], thumb_path: str = "") -> None:
        """Show *title* and *details*; decode a thumbnail from *thumb_path* off-thread."""
        was_empty = self.is_empty()
        changed_media = thumb_path != self._thumb_path
        self._title = title
        self._details = [d for d in details if d]
        self.setAccessibleName(f"Source: {title}")
        self.setAccessibleDescription(", ".join(self._details))
        self.setToolTip(f"{title}\nClick to choose another source")
        if changed_media:
            self._thumb = None
            self._thumb_path = thumb_path
            self._thumb_gen += 1
            self._thumb_fade.set(0.0)
            if thumb_path:
                job = _ThumbJob(self._thumb_gen, 0, thumb_path, self._signals)
                QThreadPool.globalInstance().start(job)
        if was_empty:
            self._content_fade.set(0.0)
            self._content_fade.to(1.0)
        self.update()

    def set_drop_active(self, active: bool) -> None:
        """Highlight while a file is dragged over the window."""
        self._drop.to(1.0 if active else 0.0)

    def _on_thumb_ready(self, gen: int, _row: int, image: object) -> None:
        if gen != self._thumb_gen or not isinstance(image, QImage) or image.isNull():
            return
        self._thumb = image
        self._thumb_fade.set(0.0)
        self._thumb_fade.to(1.0)

    # -- Input ---------------------------------------------------------------

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover.to(0.0)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(360, CARD_HEIGHT)

    # -- Painting ------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        drop = self._drop.value
        hover = self._hover.value

        fill = motion.mix_hex(color("BG_INPUT"), color("BG_ALT"), 0.35 * hover)
        fill = motion.mix_hex(fill, color("SEL_BG"), 0.55 * drop)
        edge = motion.mix_hex(color("BORDER"), color("BORDER_STRONG"), hover)
        edge = motion.mix_hex(edge, color("ACCENT"), drop)
        if self.hasFocus():
            edge = color("ACCENT")

        pen = QPen(QColor(edge))
        pen.setWidthF(1.0 + drop * 0.5)
        if self.is_empty() and drop < 0.5 and not self.hasFocus():
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

        painter.setOpacity(self._content_fade.value)
        if self.is_empty():
            self._paint_empty(painter, rect, drop)
        else:
            self._paint_source(painter, rect)

    def _paint_empty(self, painter: QPainter, rect: QRectF, drop: float) -> None:
        title_font = QFont(self.font())
        title_font.setWeight(QFont.Weight.DemiBold)
        hint_font = QFont(self.font())
        hint_font.setPixelSize(11)

        painter.setFont(title_font)
        title_w = painter.fontMetrics().horizontalAdvance(self._empty_title)
        icon_size = 22
        gap = 12
        total = icon_size + gap + title_w
        left = rect.center().x() - total / 2
        mid = rect.center().y()

        token = "ACCENT" if drop > 0.5 else "FG_DIM"
        icon(self._empty_icon, token).paint(
            painter, int(left), int(mid - icon_size / 2), icon_size, icon_size
        )
        text_left = left + icon_size + gap
        painter.setPen(QColor(color("FG")))
        painter.drawText(
            QRectF(text_left, mid - 19, rect.width(), 19),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._empty_title,
        )
        painter.setFont(hint_font)
        painter.setPen(QColor(color("FG_DIM")))
        painter.drawText(
            QRectF(text_left, mid + 1, rect.width(), 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Release to load" if drop > 0.5 else self._empty_hint,
        )

    def _paint_source(self, painter: QPainter, rect: QRectF) -> None:
        thumb = QRectF(rect.left() + _PAD, rect.center().y() - _THUMB_H / 2, _THUMB_W, _THUMB_H)
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 4.0, 4.0)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(thumb, QColor(color("VIEWER_BG")))
        if self._thumb is not None:
            image = self._thumb
            # Cover the box, centred: letterboxing a thumbnail wastes it.
            scale = max(thumb.width() / image.width(), thumb.height() / image.height())
            w, h = image.width() * scale, image.height() * scale
            target = QRectF(thumb.center().x() - w / 2, thumb.center().y() - h / 2, w, h)
            base = painter.opacity()
            painter.setOpacity(base * self._thumb_fade.value)
            painter.drawImage(target, image)
            painter.setOpacity(base)
        else:
            icon("film", "FG_DISABLED").paint(
                painter, int(thumb.center().x() - 9), int(thumb.center().y() - 9), 18, 18
            )
        painter.restore()

        text_left = thumb.right() + 14
        text_w = rect.right() - _PAD - text_left
        title_font = QFont(self.font())
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(color("SEL_FG")))
        title = painter.fontMetrics().elidedText(
            self._title, Qt.TextElideMode.ElideMiddle, int(text_w)
        )
        painter.drawText(
            QRectF(text_left, rect.center().y() - 22, text_w, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )
        detail_font = QFont(self.font())
        detail_font.setPixelSize(12)
        painter.setFont(detail_font)
        painter.setPen(QColor(color("FG_DIM")))
        details = painter.fontMetrics().elidedText(
            _SEPARATOR.join(self._details), Qt.TextElideMode.ElideRight, int(text_w)
        )
        painter.drawText(
            QRectF(text_left, rect.center().y() + 2, text_w, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            details,
        )
