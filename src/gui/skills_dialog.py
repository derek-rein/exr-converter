"""Help > Agent Skills: the skills.md text for this install, with a copy button."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.agent_skill import detect_install, render_skill
from .components import Card
from .style import set_style_property


class SkillsDialog(QDialog):
    """Shows the agent skill text and copies it to the clipboard."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Agent Skills")
        self.setMinimumSize(560, 420)
        self.resize(760, 640)

        install = detect_install()
        self._text = render_skill(install)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        card = Card(
            "Agent Skills",
            "Give this to a coding agent (Claude Code, Codex, Cursor) so it can run the "
            "command line. Save it as SKILL.md in a skills folder, or paste it into the chat.",
        )
        card._description.setWordWrap(True)
        layout.addWidget(card, 1)

        where = QLabel(f"Detected install: {install.location}")
        where.setObjectName("skillsInstall")
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_style_property(where, "role", "hint")
        card.body.addWidget(where)

        self.text_view = QPlainTextEdit()
        self.text_view.setObjectName("skillsText")
        self.text_view.setReadOnly(True)
        self.text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(11)
        self.text_view.setFont(mono)
        self.text_view.setPlainText(self._text)
        card.body.addWidget(self.text_view, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._copy_btn = QPushButton("Copy to clipboard")
        self._copy_btn.setObjectName("skillsCopy")
        set_style_property(self._copy_btn, "variant", "primary")
        self._copy_btn.clicked.connect(self.copy_to_clipboard)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(self._copy_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def skill_text(self) -> str:
        return self._text

    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self._text)
        self._copy_btn.setText("Copied")
        QTimer.singleShot(1500, lambda: self._copy_btn.setText("Copy to clipboard"))
