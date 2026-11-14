"""One running copy of the app: its viewer windows, its main window, and how files reach it.

Files arrive three ways (a second launch through the handoff socket, the macOS
open-file event, the command line), and all of them end in :meth:`AppSession.view`.
The main window is built only when something needs it, so a launch that just
views a frame never pays for it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QSettings

from ..services.single_instance import InstanceServer
from .app import ExrApplication
from .viewer import ViewerHost, ViewerWindow

if TYPE_CHECKING:
    from .window import MainWindow

log = logging.getLogger(__name__)


class AppSession(QObject):
    def __init__(self, app: ExrApplication, settings: QSettings) -> None:
        super().__init__(app)
        self._app = app
        self._settings = settings
        self._host = ViewerHost(settings, step_up=self.step_up, parent=self)
        self._server: InstanceServer | None = None
        self._main: MainWindow | None = None

    # -- files in ----------------------------------------------------------

    def start_server(self) -> bool:
        """Listen for later launches and for the OS's open-file events."""
        self._app.files_opened.connect(self.view)
        server = InstanceServer(self)
        if not server.start():
            return False
        server.paths_received.connect(self.view)
        self._server = server
        return True

    def view(self, paths: list[str]) -> None:
        """Open each viewable file in the quick viewer."""
        self._host.open_paths(list(paths))

    def viewer_windows(self) -> list[ViewerWindow]:
        return self._host.windows()

    # -- the full app ------------------------------------------------------

    def main_window(self) -> MainWindow:
        """The main window, built on first use."""
        if self._main is None:
            from .window import MainWindow

            self._main = MainWindow()
        return self._main

    def step_up(self, path: str) -> None:
        """Open *path* in the full converter, keeping the viewer open."""
        win = self.main_window()
        win.apply_startup(open_path=path, mode="auto")
        win.show()
        win.raise_()
        win.activateWindow()
