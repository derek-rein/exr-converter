"""The quick viewer: a double-clicked frame, clip or RAW file in the sequence player.

Opens on the frame that was clicked, uses the same OCIO config as the app, and
steps up to the full converter with one button. This module avoids the main
window and the convert pipeline so a launch from Finder, Explorer or a file
manager stays light; see ``main.py`` for the launch path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ..core.constants import APP_NAME, APP_ORG, is_image_sequence_ext
from ..core.fs_skip import VIDEO_EXTS
from .player.player_window import SequencePlayerWindow
from .style import set_style_property

log = logging.getLogger(__name__)


def media_kind_for(path: str) -> str:
    """``"video"`` for clips and camera RAW, ``"sequence"`` for image frames, else ``""``."""
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if is_image_sequence_ext(suffix):
        return "sequence"
    return ""


def load_saved_ocio_config(settings: QSettings) -> object | None:
    """The OCIO config the main window would load, or the bundled default; None if none loads."""
    from ..core.ocio_utils import resolve_ocio_config, resolve_ocio_for_cli

    source = str(settings.value("ocio/source", "") or "")
    file_path = str(settings.value("ocio/file_path", "") or "")
    if source:
        try:
            return resolve_ocio_config(source, file_path=file_path)
        except Exception:
            log.info("Saved OCIO source %r did not load; using the default", source)
    try:
        return resolve_ocio_for_cli(None)
    except Exception:
        log.exception("No OCIO config could be loaded for the viewer")
        return None


class ViewerWindow(SequencePlayerWindow):
    """A player window for a double-clicked file, with viewer keys and a step-up button."""

    open_in_app = Signal(str)  # the path to open in the full app

    def __init__(
        self,
        path: str,
        *,
        settings: QSettings | None = None,
        ocio_cfg: object | None = None,
    ) -> None:
        self._path = path
        kind = media_kind_for(path) or "sequence"
        super().__init__(
            path,
            settings=settings,
            ocio_cfg=ocio_cfg,
            fps=24.0,
            media_kind=kind,
            # Camera files and EXR plates are scene-referred: the studio view, not
            # the Rec.709 monitoring view that suits a converted result.
            prefer_video_monitoring=False,
        )
        self._build_footer()
        self._bind_keys()
        self._go_to_clicked_frame(path, kind)

    # -- loading -----------------------------------------------------------

    def _go_to_clicked_frame(self, path: str, kind: str) -> None:
        if kind != "sequence":
            return
        try:
            from ..core.sequence_scan import parse_frame_name

            parsed = parse_frame_name(Path(path).name)
            if parsed is not None:
                self.player().set_frame(parsed.frame)
        except Exception:
            log.debug("Could not start on the clicked frame", exc_info=True)

    def open_path(self, path: str) -> None:
        """Show another file in this window."""
        self._path = path
        kind = media_kind_for(path) or "sequence"
        self._load(
            path,
            ocio_cfg=self.player()._ocio_cfg,
            src_colorspace="",
            fps=24.0,
            title=None,
            media_kind=kind,
            prefer_video_monitoring=False,
        )
        self._go_to_clicked_frame(path, kind)

    @property
    def path(self) -> str:
        return self._path

    # -- chrome ------------------------------------------------------------

    def _build_footer(self) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(8, 6, 8, 6)
        row.addStretch(1)
        button = QPushButton("Open in EXR Converter")
        button.setObjectName("viewerOpenInApp")
        button.setToolTip("Open this in the converter (Ctrl+E)")
        button.setAutoDefault(False)
        set_style_property(button, "variant", "ghost")
        button.clicked.connect(self._step_up)
        row.addWidget(button)
        layout = self.layout()
        if layout is not None:
            layout.addLayout(row)  # type: ignore[attr-defined]

    def _bind_keys(self) -> None:
        def bind(sequence: str | QKeySequence, slot: Callable[[], None]) -> None:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

        bind("Esc", self.close)
        bind(QKeySequence.StandardKey.Close, self.close)
        bind("Ctrl+E", self._step_up)

    def _step_up(self) -> None:
        self.open_in_app.emit(self._path)


class ViewerHost(QObject):
    """Opens a viewer window for each path it is given, and can step up to the full app.

    ``step_up`` is told the path and builds the main window on demand, so the
    viewer itself never imports it.
    """

    def __init__(
        self,
        settings: QSettings | None = None,
        *,
        step_up: Callable[[str], None] | None = None,
        reuse_window: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings(APP_ORG, APP_NAME)
        self._step_up = step_up
        self._reuse = reuse_window
        self._ocio_cfg: object | None = None
        self._ocio_loaded = False
        self._windows: list[ViewerWindow] = []

    def open_paths(self, paths: list[str]) -> list[ViewerWindow]:
        """Open each viewable path. Several files from one selection get a window each."""
        opened: list[ViewerWindow] = []
        for path in paths:
            if not media_kind_for(path):
                log.info("Viewer: not a media file, skipped: %s", path)
                continue
            window = self._show(path, reuse=self._reuse and len(paths) == 1)
            if window is not None:
                opened.append(window)
        return opened

    def windows(self) -> list[ViewerWindow]:
        return [w for w in self._windows if w.isVisible()]

    def _config(self) -> object | None:
        if not self._ocio_loaded:
            self._ocio_cfg = load_saved_ocio_config(self._settings)
            self._ocio_loaded = True
        return self._ocio_cfg

    def _show(self, path: str, *, reuse: bool) -> ViewerWindow | None:
        self._windows = [w for w in self._windows if _alive(w)]
        if reuse and self._windows:
            window = self._windows[-1]
            window.open_path(path)
        else:
            window = ViewerWindow(path, settings=self._settings, ocio_cfg=self._config())
            window.open_in_app.connect(self._on_step_up)
            window.destroyed.connect(lambda *_: None)
            self._windows.append(window)
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def _on_step_up(self, path: str) -> None:
        if self._step_up is not None:
            self._step_up(path)


def _alive(widget: QWidget) -> bool:
    try:
        widget.isVisible()
    except RuntimeError:  # the C++ object is gone: WA_DeleteOnClose ran
        return False
    return True
