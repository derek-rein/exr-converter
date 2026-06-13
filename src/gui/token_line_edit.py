"""A ``QLineEdit`` whose context menu can insert burn-in / watermark variables.

Right-clicking the field shows an "Insert Variable" submenu (grouped by
category) above the usual Cut / Copy / Paste actions.  Selecting an entry
inserts the literal token (e.g. ``<shot>``) at the cursor; the token is
resolved later, at render time, by :mod:`src.render.tokens`.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QContextMenuEvent
from PySide6.QtWidgets import QLineEdit, QMenu, QWidget

from ..render.tokens import TOKEN_GROUPS


class TokenLineEdit(QLineEdit):
    """Line edit with a custom right-click menu for inserting ``<token>`` variables."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setToolTip(
            "Right-click to insert variables like <shot>, <version> or <frame>.\n"
            "They are filled in per frame when the overlay is rendered."
        )

    def _insert_token(self, token: str) -> None:
        # QLineEdit.insert replaces the current selection (or inserts at the
        # caret), which is exactly the behaviour we want for a token.
        self.insert(token)

    def _build_variable_menu(self, parent: QMenu) -> QMenu:
        menu = QMenu("Insert Variable", parent)
        for group_label, tokens in TOKEN_GROUPS:
            menu.addSection(group_label)
            for _name, display, description in tokens:
                action = QAction(display, menu)
                action.setToolTip(description)
                action.setStatusTip(description)
                action.triggered.connect(
                    lambda _checked=False, tok=display: self._insert_token(tok)
                )
                menu.addAction(action)
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 (Qt override)
        menu = self.createStandardContextMenu()
        menu.setToolTipsVisible(True)

        variable_menu = self._build_variable_menu(menu)
        first = menu.actions()[0] if menu.actions() else None
        menu.insertMenu(first, variable_menu)
        if first is not None:
            menu.insertSeparator(first)

        menu.exec(event.globalPos())
        menu.deleteLater()


__all__ = ["TokenLineEdit"]
