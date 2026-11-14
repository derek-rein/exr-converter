"""The application object: a QApplication that also receives files from the OS.

On macOS, Finder does not pass a double-clicked file on the command line. It
sends an Apple Event that Qt delivers as ``QFileOpenEvent``, possibly before any
window exists, so paths are queued until something takes them.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication


class ExrApplication(QApplication):
    """``QApplication`` plus ``files_opened`` for paths the operating system hands us."""

    files_opened = Signal(list)

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self._pending: list[str] = []
        self._listening = False

    def event(self, e: QEvent) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.FileOpen and isinstance(e, QFileOpenEvent):
            path = e.file()
            if path:
                self._deliver([path])
            return True
        return super().event(e)

    def take_pending_files(self) -> list[str]:
        """Paths opened before anyone was listening; clears them. Call once at start-up."""
        pending, self._pending = self._pending, []
        self._listening = True
        return pending

    def _deliver(self, paths: list[str]) -> None:
        if self._listening:
            self.files_opened.emit(paths)
        else:
            self._pending.extend(paths)
