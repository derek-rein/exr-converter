"""FormGrid: labelled rows that share one label column."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QWidget

# One label column width for every form in the app, so inputs line up within a
# card and from one card to the next.
LABEL_WIDTH = 124

# Width presets for controls. Two fixed widths, so right edges land on two
# lines down the card instead of one per control. Only free-text paths fill
# the row.
NARROW = (150, 150)  # a number or a short choice: scale, padding, frame rate
WIDE = (340, 340)  # a name or a range: color space, codec, frames
MEDIUM = WIDE  # kept as a name; same edge as WIDE on purpose
FILL = None  # stretch to the trailing edge
NATURAL = (0, 0)  # every widget at its own size: a row of checkboxes


def sized(widget: QWidget, width: tuple[int, int]) -> QWidget:
    """Give *widget* a width preset (for a second control sharing a row)."""
    _apply_width(widget, width)
    return widget


def _apply_width(widget: QWidget, width: tuple[int, int]) -> None:
    low, high = width
    if low == high:
        # setFixedWidth, not min + max: a stylesheet min-width (spin boxes
        # have one) otherwise wins over setMinimumWidth.
        widget.setFixedWidth(low)
    else:
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
    (an icon button, a note). Unless the row is ``FILL``, leftover space
    stays empty at the end, so a short select is not stretched across the
    card.

    ``add_wide(widget)`` spans both columns, for content that is not a field.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setHorizontalSpacing(12)
        self.setVerticalSpacing(6)
        self.setColumnMinimumWidth(0, LABEL_WIDTH)
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
        self.addWidget(text, row, 0)
        self._labels[row] = text

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(spacing)
        for index, widget in enumerate(widgets):
            if index == 0:
                text.setBuddy(widget)
                if width is None:
                    line.addWidget(widget, 1)
                    continue
                if width == NATURAL:
                    line.addWidget(widget)
                    continue
                _apply_width(widget, width)
                if width[0] != width[1]:
                    policy = widget.sizePolicy()
                    widget.setSizePolicy(QSizePolicy.Policy.Preferred, policy.verticalPolicy())
                # Stretch so a ranged control grows to its cap before space is left over.
                line.addWidget(widget, 1)
            else:
                line.addWidget(widget)
        if width is not None:
            # Absorbs what the capped control leaves, keeping the row left-aligned.
            line.addStretch(1000)
        self.addLayout(line, row, 1)
        return text

    def add_wide(self, widget: QWidget) -> None:
        """Add *widget* across both columns."""
        self.addWidget(widget, self.rowCount(), 0, 1, 2)
