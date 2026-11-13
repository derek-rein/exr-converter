"""Card: a titled surface that groups one job's controls."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..style import set_style_property


class Card(QFrame):
    """A raised, bordered container with a header and a body.

    Props
        title        Short noun for what the card holds ("Input").
        description  Optional one line under the title saying what it is for.

    Slots
        body                  ``QVBoxLayout``; add the card's content here.
        add_header_widget()   Widgets at the trailing edge of the header
                              (a status chip, a small action button).

    The look (surface, border, radius, padding) is ``QFrame#card`` in
    ``style.qss``. ``title()`` / ``setTitle()`` mirror ``QGroupBox`` so a card
    can replace one without touching callers.
    """

    def __init__(self, title: str = "", description: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 14)
        outer.setSpacing(10)

        self._header = QHBoxLayout()
        self._header.setContentsMargins(0, 0, 0, 0)
        self._header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        self._title = QLabel(title)
        set_style_property(self._title, "role", "card-title")
        titles.addWidget(self._title)
        self._description = QLabel(description)
        set_style_property(self._description, "role", "hint")
        self._description.setVisible(bool(description))
        titles.addWidget(self._description)
        self._header.addLayout(titles, 1)
        outer.addLayout(self._header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body)

        self.setAccessibleName(title)

    # -- Props ---------------------------------------------------------------

    def title(self) -> str:
        return self._title.text()

    def setTitle(self, title: str) -> None:  # noqa: N802 - mirrors QGroupBox
        self._title.setText(title)
        self.setAccessibleName(title)

    def description(self) -> str:
        return self._description.text()

    def set_description(self, text: str) -> None:
        self._description.setText(text)
        self._description.setVisible(bool(text))

    # -- Slots ---------------------------------------------------------------

    def add_header_widget(self, widget: QWidget) -> None:
        """Place *widget* at the trailing edge of the header row."""
        self._header.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
