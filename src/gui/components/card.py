"""Card: a titled surface that groups one job's controls."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QWidget


class Card(QGroupBox):
    """A section: a thin outline with its title sitting on the top edge.

    Props
        title        Short noun for what the section holds ("Input"); the
                     ``QGroupBox`` title, drawn on the border.
        description  Optional one line saying what the section is for. Shown as
                     the section's tooltip, not as text, to save space.

    Slots
        body                  ``QVBoxLayout``; add the section's content here.
        add_header_widget()   Widgets at the trailing edge of the first row
                              (a switch, a small action button).

    The look is ``QGroupBox`` in ``style.qss``. The class keeps the name and the
    ``card`` object name so callers and tests need not change.
    """

    def __init__(self, title: str = "", description: str = "", parent: QWidget | None = None):
        super().__init__(title, parent)
        self.setObjectName("card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # A row above the body for header widgets at the trailing edge. Hidden
        # (no space) until one is added.
        self._header_row = QWidget()
        self._header = QHBoxLayout(self._header_row)
        self._header.setContentsMargins(0, 0, 0, 0)
        self._header.setSpacing(8)
        self._header.addStretch(1)
        self._header_row.hide()
        outer.addWidget(self._header_row)
        self._description = ""
        self.set_description(description)
        self.setAccessibleName(title)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    # -- Props ---------------------------------------------------------------

    def setTitle(self, title: str) -> None:  # noqa: N802 - QGroupBox API
        super().setTitle(title)
        self.setAccessibleName(title)

    def description(self) -> str:
        return self._description

    def set_description(self, text: str) -> None:
        self._description = text
        self.setToolTip(text)

    # -- Slots ---------------------------------------------------------------

    def add_header_widget(self, widget: QWidget) -> None:
        """Place *widget* at the trailing edge of the first row."""
        self._header.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._header_row.show()
