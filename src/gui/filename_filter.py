"""Keep characters that can't be in a file name out of text fields.

A field that ends up in a file name strips what it can't hold as you type or
paste, so what you see is what gets written. The core also cleans these values
(``core.path_tokens``), which covers the command line.
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import QLineEdit

from ..core.path_tokens import BAD_NAME_CHARS

# Never valid in a file name, on any system. Used where ``/``, ``\\``, ``:``, ``<``
# and ``>`` have a meaning (folders, drives, name tokens) and so can't be blocked.
# ``?`` is left out of the path set: Windows long paths start ``\\?\``.
BAD_PATH_CHARS = re.compile(r'[*"|\x00-\x1f]')
BAD_PATTERN_CHARS = re.compile(r'[*?"|\x00-\x1f]')


def install_filename_filter(edit: QLineEdit, bad: re.Pattern[str] = BAD_NAME_CHARS) -> None:
    """Remove characters matching *bad* from *edit* whenever the user changes it.

    Typing an illegal character does nothing; pasting text drops just those
    characters. The cursor stays where it was.
    """

    def _filter(text: str) -> None:
        cleaned = bad.sub("", text)
        if cleaned == text:
            return
        # Characters removed before the cursor shift it left by that many.
        cursor = edit.cursorPosition()
        removed_before = len(text[:cursor]) - len(bad.sub("", text[:cursor]))
        edit.setText(cleaned)
        edit.setCursorPosition(max(0, cursor - removed_before))

    edit.textEdited.connect(_filter)
    # A saved value from an older version may already hold bad characters.
    initial = bad.sub("", edit.text())
    if initial != edit.text():
        edit.setText(initial)
