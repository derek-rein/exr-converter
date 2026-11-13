"""Base for menu pickers (color space, codec) that look like a combo box."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPaintEvent
from PySide6.QtWidgets import QStyle, QStyleOptionToolButton, QStylePainter, QToolButton

from .style import color

# Left inset matches QComboBox in style.qss (1 px border + 8 px padding);
# the right inset leaves room for the chevron the stylesheet draws.
_TEXT_LEFT = 9
_TEXT_RIGHT = 28


class PickerButton(QToolButton):
    """A tool button whose label is left-aligned, like the combos beside it.

    Qt centres a text-only tool button and ignores ``text-align`` from a
    stylesheet, so the frame comes from the style and the label is drawn here.
    """

    def _label_color(self) -> QColor:
        if not self.isEnabled():
            return QColor(color("FG_DISABLED"))
        state = self.property("state")
        if state == "invalid":
            return QColor(color("ERROR"))
        if state == "auto":
            return QColor(color("WARNING"))
        return QColor(color("FG"))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QStylePainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        text = option.text
        option.text = ""
        option.icon = QIcon()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        rect = self.rect().adjusted(_TEXT_LEFT, 0, -_TEXT_RIGHT, 0)
        painter.setPen(self._label_color())
        elided = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
