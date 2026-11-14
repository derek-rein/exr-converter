"""Dialog footer: the standard button box on a bar with a line above it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QHBoxLayout, QWidget


class DialogFooter(QWidget):
    """Hosts a ``QDialogButtonBox`` (OK / Cancel and friends) at the foot of a dialog.

    The box keeps its roles and signals. This adds the full-width bar and top
    border that the main window's action bar has, and puts the buttons in that
    bar's order: Cancel, then the primary action at the right edge. Qt's own
    order on Windows and Linux is the reverse, which made Cancel and OK swap
    places from the main window to Preferences.

    Give the dialog's root layout no margins so the bar reaches both edges.
    """

    def __init__(self, buttons: QDialogButtonBox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dialogFooter")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.buttons = buttons
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addStretch(1)
        layout.addWidget(buttons)
        # A right-to-left box lays its buttons out in the opposite order. Each
        # button keeps normal text direction.
        buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        for button in buttons.buttons():
            button.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
