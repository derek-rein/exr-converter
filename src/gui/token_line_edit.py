"""A ``QLineEdit`` whose context menu inserts burn-in / watermark variables.

Right-clicking the field shows a flat, grouped list of insertable tokens
(e.g. ``<frame>``, ``<shot>``) and *nothing else* — the default Cut / Copy /
Paste menu is replaced entirely.  Selecting an entry inserts the literal token
at the cursor; it's resolved later, at render time, by
:mod:`src.render.tokens`.  Standard clipboard shortcuts (Cmd/Ctrl+C/V/X/Z)
still work via the keyboard.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtGui import QAction, QContextMenuEvent
from PySide6.QtWidgets import QLineEdit, QMenu, QWidget

from ..render.tokens import TOKEN_GROUPS

TokenGroups = Sequence[tuple[str, Sequence[tuple[str, str, str]]]]


class TokenLineEdit(QLineEdit):
    """Line edit with a custom right-click menu for inserting ``<token>`` variables.

    *groups* defaults to the slate / burn-in variables; the Transcode tab
    passes its output path variables (:data:`src.core.path_tokens.PATH_TOKEN_GROUPS`).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        groups: TokenGroups | None = None,
        tooltip: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._groups: TokenGroups = groups if groups is not None else TOKEN_GROUPS
        self.setToolTip(
            tooltip
            if tooltip is not None
            else (
                "Right-click to insert variables like <shot>, <version> or <frame>.\n"
                "They are filled in per frame when the overlay is rendered."
            )
        )

    def token_groups(self) -> TokenGroups:
        return self._groups

    def _insert_token(self, token: str) -> None:
        # QLineEdit.insert replaces the current selection (or inserts at the
        # caret), which is exactly the behaviour we want for a token.
        self.insert(token)
        self.setFocus()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 (Qt override)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        header = menu.addAction("Insert Variable")
        header.setEnabled(False)

        for group_label, tokens in self._groups:
            menu.addSection(group_label)
            for _name, display, description in tokens:
                action = QAction(display, menu)
                action.setToolTip(description)
                action.setStatusTip(description)
                action.triggered.connect(
                    lambda _checked=False, tok=display: self._insert_token(tok)
                )
                menu.addAction(action)

        menu.exec(event.globalPos())
        menu.deleteLater()


__all__ = ["TokenLineEdit"]
