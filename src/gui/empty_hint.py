"""An "empty state" drawn over an item view while it has no rows."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractItemView, QWidget

from .icons import icon
from .style import color


class EmptyHint(QWidget):
    """Icon, a line saying what belongs here, and how to add it.

    A child of the view's viewport that ignores the mouse, so drops, clicks
    and context menus still reach the view. It hides itself as soon as the
    model has a row.
    """

    def __init__(
        self, view: QAbstractItemView, title: str, hint: str = "", icon_name: str = "film"
    ) -> None:
        super().__init__(view.viewport())
        self._view = view
        self._title = title
        self._hint = hint
        self._icon = icon_name
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        view.viewport().installEventFilter(self)
        model = view.model()
        model.rowsInserted.connect(self._sync)
        model.rowsRemoved.connect(self._sync)
        model.modelReset.connect(self._sync)
        self._sync()

    def _sync(self, *_args: object) -> None:
        self.setGeometry(self._view.viewport().rect())
        self.setVisible(self._view.model().rowCount() == 0)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(self._view.viewport().rect())
        return super().eventFilter(watched, event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        title_font = QFont(self.font())
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        title_w = painter.fontMetrics().horizontalAdvance(self._title)
        size, gap = 20, 10
        left = rect.center().x() - (size + gap + title_w) / 2
        mid = rect.center().y()
        # One line when the view is short, two when there is room.
        two_lines = bool(self._hint) and rect.height() >= 56
        title_y = mid - 18 if two_lines else mid - 10
        icon(self._icon, "FG_DIM").paint(painter, int(left), int(mid - size / 2), size, size)
        painter.setPen(QColor(color("FG_DIM")))
        painter.drawText(
            QRectF(left + size + gap, title_y, rect.width(), 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )
        if two_lines:
            hint_font = QFont(self.font())
            hint_font.setPixelSize(11)
            painter.setFont(hint_font)
            painter.setPen(QColor(color("FG_DISABLED")))
            painter.drawText(
                QRectF(left + size + gap, mid + 1, rect.width(), 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._hint,
            )
