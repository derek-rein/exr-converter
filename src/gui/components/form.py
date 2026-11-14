"""FormGrid: labelled rows that share one label column."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QWidget

# One label column width for every form in the app, so inputs line up within a
# card and from one card to the next.
LABEL_WIDTH = 124

# Width presets for controls.
# NARROW is a short fixed box (a number, a second choice on the row).
# WIDE grows from a floor to the trailing edge, so fields fill the card.
# FILL is the same growth with no floor (paths, payload).
# max == 0 on a preset means "expand"; NATURAL is handled separately.
NARROW = (150, 150)
WIDE_MIN = 180
WIDE = (WIDE_MIN, 0)
MEDIUM = WIDE
FILL = None
NATURAL = (0, 0)
# Wrapped hint labels that used to match a capped WIDE field.
FIELD_WIDTH = 340
# Qt QWIDGETSIZE_MAX; PySide6 6.9 does not export the name.
_MAX_WIDTH = 16_777_215


def sized(widget: QWidget, width: tuple[int, int]) -> QWidget:
    """Give *widget* a width preset (for a second control sharing a row)."""
    _apply_width(widget, width)
    return widget


def _expands(width: tuple[int, int] | None) -> bool:
    return width is None or width[1] == 0


def _is_fixed(width: tuple[int, int] | None) -> bool:
    return width is not None and width[1] > 0 and width[0] == width[1]


def _apply_width(widget: QWidget, width: tuple[int, int]) -> None:
    low, high = width
    if high == 0:
        widget.setMinimumWidth(low)
        widget.setMaximumWidth(_MAX_WIDTH)
        policy = widget.sizePolicy()
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, policy.verticalPolicy())
        return
    if low == high:
        # setFixedWidth, not min + max: a stylesheet min-width (spin boxes
        # have one) otherwise wins over setMinimumWidth.
        widget.setFixedWidth(low)
        return
    widget.setMinimumWidth(low)
    widget.setMaximumWidth(high)


def inline_label(text: str, buddy: QWidget | None = None) -> QLabel:
    """Label for a second field on the same row, set off by a little space."""
    label = QLabel(text)
    label.setContentsMargins(12, 0, 0, 0)
    if buddy is not None:
        label.setBuddy(buddy)
    return label


class FormGrid(QGridLayout):
    """A two-column grid: right-hand controls, left-hand labels of one width.

    ``add_row(label, *widgets)`` adds a label and its controls. The first
    control gets the *width* preset; the rest follow at their natural size
    (an icon button, a note). ``WIDE`` and ``FILL`` grow to the trailing
    edge (or up to the next control on the row). ``NARROW`` stays a short
    box, with leftover space empty at the end.

    ``add_wide(widget)`` spans both columns, for content that is not a field.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setHorizontalSpacing(12)
        self.setVerticalSpacing(6)
        # Min and stretch-0: a wide label must wrap, not grow this column, so
        # every FormGrid (every card) keeps the same control x.
        self.setColumnMinimumWidth(0, LABEL_WIDTH)
        self.setColumnStretch(0, 0)
        self.setColumnStretch(1, 1)
        self._labels: dict[int, QLabel] = {}

    def add_row(
        self,
        label: str,
        *widgets: QWidget,
        width: tuple[int, int] | None = WIDE,
        spacing: int = 8,
    ) -> QLabel:
        """Add a labelled row and return its label (to hide or retitle later)."""
        row = self.rowCount()
        text = QLabel(label)
        text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        text.setWordWrap(True)
        text.setFixedWidth(LABEL_WIDTH)
        self.addWidget(text, row, 0)
        self._labels[row] = text

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(spacing)
        for index, widget in enumerate(widgets):
            if index == 0:
                text.setBuddy(widget)
                if width == NATURAL:
                    line.addWidget(widget)
                    continue
                if _expands(width):
                    if width is not None:
                        _apply_width(widget, width)
                    line.addWidget(widget, 1)
                    continue
                _apply_width(widget, width)
                if width is not None and width[0] != width[1]:
                    policy = widget.sizePolicy()
                    widget.setSizePolicy(QSizePolicy.Policy.Preferred, policy.verticalPolicy())
                    line.addWidget(widget, 1)
                else:
                    line.addWidget(widget)
            else:
                line.addWidget(widget)
        if _is_fixed(width) or width == NATURAL:
            # Absorbs leftover space so a short/natural row stays left-aligned.
            line.addStretch(1000)
        self.addLayout(line, row, 1)
        return text

    def add_wide(self, widget: QWidget) -> None:
        """Add *widget* across both columns."""
        self.addWidget(widget, self.rowCount(), 0, 1, 2)
