"""Keep characters that can't be in a file name out of text fields.

A field that ends up in a file name strips what it can't hold as you type or
paste, so what you see is what gets written. The core also cleans these values
(``core.path_tokens``), which covers the command line.
"""

from __future__ import annotations

import re
import sys

from PySide6.QtWidgets import QLineEdit

from ..core.path_tokens import BAD_NAME_CHARS

# What a *path* field (an output folder, a full output path) must not hold. These
# are existing or user-chosen locations, so only what the host OS rejects goes:
# control characters everywhere, and on Windows also ``* " |``. ``/``, ``\\``, ``:``,
# ``<`` and ``>`` have a meaning in a path (folders, drives, name tokens) and are
# never blocked; ``?`` stays because Windows long paths start ``\\?\``. A real
# POSIX folder like ``/Volumes/Show/A|B`` is left as it is.
BAD_PATH_CHARS = (
    re.compile(r'[*"|\x00-\x1f]') if sys.platform == "win32" else re.compile(r"[\x00-\x1f]")
)
# A name pattern builds new file names that may travel to any system, so it drops
# the characters invalid on Windows too (``/`` stays: it makes sub-folders).
BAD_PATTERN_CHARS = re.compile(r'[*?"|\x00-\x1f]')


def install_filename_filter(edit: QLineEdit, bad: re.Pattern[str] = BAD_NAME_CHARS) -> None:
    """Remove characters matching *bad* from *edit* now and whenever its text changes.

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

    # textChanged, not textEdited: also catches setText (a saved value, Browse…).
    edit.textChanged.connect(_filter)
    _filter(edit.text())
