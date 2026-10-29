from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QObject,
    QPointF,
    QRunnable,
    QSize,
    QStandardPaths,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizePolicy,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.bg_worker import BackgroundWorker
from ..core.dir_search import (
    MAX_SEARCH_DEPTH,
    MAX_SEARCH_RESULTS,
    SEARCH_BATCH_SIZE,
    run_dir_search_process,
)
from ..core.fs_skip import (
    SEARCH_SKIP_ABSPATHS,
    SEARCH_SKIP_DIRS,
    SEARCH_SKIP_FILES,
    SEARCH_SKIP_SUFFIXES,
    VIDEO_EXTS,
)
from ..core.local_fs import can_probe_media, path_is_dir
from .browser_path import folder_path_for_copy
from .browser_state import (
    VIEW_GRID,
    VIEW_LIST,
    VIEW_PREVIEW,
    load_favorite_paths,
    save_favorite_paths,
)
from .browser_tree import BrowserTreeModel, VolumePoller, background_pool
from .browser_volumes import VolumeInfo
from .style import _PALETTE, STATUS_DIM

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared places sidebar for browser dialogs
# ---------------------------------------------------------------------------

_OS_PLACES: list[tuple[str, str, QStandardPaths.StandardLocation]] = [
    ("\U0001f3e0", "Home", QStandardPaths.StandardLocation.HomeLocation),
    ("\U0001f5a5\ufe0f", "Desktop", QStandardPaths.StandardLocation.DesktopLocation),
    ("\U0001f4c4", "Documents", QStandardPaths.StandardLocation.DocumentsLocation),
    ("\u2b07\ufe0f", "Downloads", QStandardPaths.StandardLocation.DownloadLocation),
    ("\U0001f3ac", "Movies", QStandardPaths.StandardLocation.MoviesLocation),
]


def _copy_to_clipboard(text: str) -> None:
    if text:
        QGuiApplication.clipboard().setText(text)


def _folder_path_for_copy(text: str) -> str:
    """Directory to copy for a file, folder, or ``name.####.ext`` path string."""
    return folder_path_for_copy(text)


def _add_copy_path_actions(menu: QMenu, *, file_path: str = "", folder_path: str = "") -> None:
    """Append **Copy File Path** / **Copy Folder Path** (disabled when empty)."""
    # QAction.triggered(bool) — never bind the bool as the path string.
    file_path = (file_path or "").strip()
    folder_path = (folder_path or "").strip()
    if not folder_path and file_path:
        folder_path = _folder_path_for_copy(file_path)

    copy_file = QAction("Copy File Path", menu)
    copy_file.setEnabled(bool(file_path))
    copy_file.triggered.connect(lambda _=False, p=file_path: _copy_to_clipboard(p))
    menu.addAction(copy_file)

    copy_folder = QAction("Copy Folder Path", menu)
    copy_folder.setEnabled(bool(folder_path))
    copy_folder.triggered.connect(lambda _=False, p=folder_path: _copy_to_clipboard(p))
    menu.addAction(copy_folder)


# SizeGrip lives in :mod:`src.gui.size_grip` (imported by window / slate_widgets)
# so the slate package does not create an import cycle with this module.


class _FavoritesDropList(QListWidget):
    """List widget that accepts folders dropped from the directory tree or OS."""

    dirs_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _dropped_dirs(event) -> list[str]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        return [p for url in md.urls() if (p := url.toLocalFile()) and path_is_dir(p)]

    def dragEnterEvent(self, event) -> None:
        if self._dropped_dirs(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        dirs = self._dropped_dirs(event)
        if dirs:
            self.dirs_dropped.emit(dirs)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


def _places_divider_item(list_widget: QListWidget) -> None:
    """Insert a thin horizontal rule row into a places list."""
    divider = QListWidgetItem()
    divider.setFlags(Qt.ItemFlag.NoItemFlags)
    divider.setSizeHint(divider.sizeHint().expandedTo(QWidget().sizeHint()))
    list_widget.addItem(divider)

    frame = QWidget()
    frame_layout = QVBoxLayout(frame)
    frame_layout.setContentsMargins(4, 6, 4, 4)
    frame_layout.setSpacing(0)
    line = QWidget()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {_PALETTE['BORDER']};")
    frame_layout.addWidget(line)
    list_widget.setItemWidget(divider, frame)


class _PlacesSidebar(QWidget):
    """Sidebar listing volumes, OS locations, and user-defined favorites."""

    navigate_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(150)
        self._current_dir = ""
        # Row ranges for rebuildable sections (volumes + static places stay fixed
        # after first build except the volumes block which is refreshed).
        self._vol_header_row = -1
        self._vol_start = 0
        self._vol_end = 0  # exclusive
        self._fav_start = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list = _FavoritesDropList()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._ctx_menu)
        self._list.dirs_dropped.connect(self._on_dirs_dropped)

        # -- Volumes (drives / external media) — rebuilt by refresh_volumes --
        vol_header = QListWidgetItem("Volumes")
        vol_header.setFlags(Qt.ItemFlag.NoItemFlags)
        font = vol_header.font()
        font.setBold(True)
        vol_header.setFont(font)
        self._list.addItem(vol_header)
        self._vol_header_row = 0
        self._vol_start = 1
        self._vol_end = 1
        # Filled by set_volumes() when the background volume poll returns.
        self._volumes: list[VolumeInfo] | None = None

        _places_divider_item(self._list)

        for icon, name, location in _OS_PLACES:
            path = QStandardPaths.writableLocation(location)
            if not path or not path_is_dir(path):
                continue
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)

        _places_divider_item(self._list)

        header = QListWidgetItem("\u2605  Favorites")
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        self._list.addItem(header)
        self._fav_start = self._list.count()

        # No stat here: a favorite on an offline network share would block
        # opening the dialog. Clicking a missing one just shows the error.
        for fav_path in load_favorite_paths():
            if fav_path:
                self._add_fav_item(fav_path)

        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(2, 2, 2, 2)
        btn_row.setSpacing(2)
        add_btn = QToolButton()
        add_btn.setText("+")
        add_btn.setToolTip("Add current folder to favorites")
        add_btn.setAutoRaise(True)
        add_btn.clicked.connect(self._add_current)
        rm_btn = QToolButton()
        rm_btn.setText("\u2212")
        rm_btn.setToolTip("Remove selected favorite")
        rm_btn.setAutoRaise(True)
        rm_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._list.itemClicked.connect(self._on_clicked)

    def set_volumes(self, volumes: list[VolumeInfo]) -> None:
        """Show *volumes* under the Volumes header.

        Rows are rebuilt only when the list changed. Rebuilding on every poll
        re-laid out the sidebar every few seconds, which showed as a flicker
        across the dialog (most visibly over the Preview player).
        """
        if self._volumes is not None and list(volumes) == self._volumes:
            return
        self._volumes = list(volumes)
        self._rebuild_volume_items()

    def refresh_volumes(self) -> None:
        """Kept for callers of the old API; volumes now arrive via :meth:`set_volumes`."""

    def _rebuild_volume_items(self) -> None:
        """Replace volume rows under the Volumes header without touching favorites."""
        self._list.setUpdatesEnabled(False)
        try:
            while self._vol_end > self._vol_start:
                self._list.takeItem(self._vol_start)
                self._vol_end -= 1
                self._fav_start = max(self._vol_start, self._fav_start - 1)

            insert_at = self._vol_start
            for vol in self._volumes or []:
                # Prefer a disk glyph; system root gets a computer glyph.
                icon = "\U0001f5a5" if vol.is_system_root else "\U0001f4be"
                item = QListWidgetItem(f"{icon}  {vol.name}")
                item.setData(Qt.ItemDataRole.UserRole, vol.path)
                item.setToolTip(vol.path)
                self._list.insertItem(insert_at, item)
                insert_at += 1
                self._fav_start += 1
            self._vol_end = insert_at
        finally:
            self._list.setUpdatesEnabled(True)

    def set_current_dir(self, path: str) -> None:
        self._current_dir = path

    def _add_fav_item(self, path: str) -> None:
        name = Path(path).name or path
        item = QListWidgetItem(f"\U0001f4c1  {name}")
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self._list.addItem(item)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            # The dialog lists the folder in the background; no stat here.
            self.navigate_requested.emit(path)

    def _add_current(self) -> None:
        self.add_favorite(self._current_dir)

    def add_favorite(self, path: str) -> None:
        if not path or not path_is_dir(path):
            return
        for i in range(self._fav_start, self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == path:
                return
        self._add_fav_item(path)
        self._save_favorites()

    def _on_dirs_dropped(self, paths: list[str]) -> None:
        for path in paths:
            self.add_favorite(path)

    def _remove_selected(self) -> None:
        row = self._list.currentRow()
        if row >= self._fav_start:
            self._list.takeItem(row)
            self._list.setCurrentRow(-1)
            self._save_favorites()

    def _ctx_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        row = self._list.row(item)
        menu = QMenu(self)
        menu.addAction("Copy Full Path", lambda: _copy_to_clipboard(path))
        if row >= self._fav_start:
            menu.addAction("Remove from Favorites", lambda: self._remove_row(row))
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _remove_row(self, row: int) -> None:
        if row >= self._fav_start:
            self._list.takeItem(row)
            self._list.setCurrentRow(-1)
            self._save_favorites()

    def _save_favorites(self) -> None:
        favs = []
        for i in range(self._fav_start, self._list.count()):
            path = self._list.item(i).data(Qt.ItemDataRole.UserRole)
            if path:
                favs.append(path)
        save_favorite_paths(favs)


def _setup_dir_tree(tree: QTreeView, fs_model: BrowserTreeModel, places: _PlacesSidebar) -> None:
    """Enable dragging folders to favorites + a right-click menu on a dir tree."""
    tree.setDragEnabled(True)
    tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    # Expand/collapse is handled on single-click of the row (see
    # :func:`_tree_click_toggle_expand`); double-click should not also toggle.
    tree.setExpandsOnDoubleClick(False)

    def _menu(pos) -> None:
        idx = tree.indexAt(pos)
        if not idx.isValid():
            return
        path = fs_model.filePath(idx)
        if not path:
            return
        menu = QMenu(tree)
        if fs_model.isDir(idx):
            menu.addAction("Add to Favorites", lambda: places.add_favorite(path))
        menu.addAction("Copy Full Path", lambda: _copy_to_clipboard(path))
        menu.exec(tree.viewport().mapToGlobal(pos))

    tree.customContextMenuRequested.connect(_menu)


def _tree_click_toggle_expand(tree: QTreeView, fs_model: BrowserTreeModel, index) -> None:
    """Single-click folder: expand if collapsed, collapse if already expanded.

    Branch-indicator clicks are handled by QTreeView itself (and do not emit
    ``clicked``), so this only runs for row/label clicks — first click opens
    the folder in the tree, second click closes it.
    """
    if not index.isValid() or not fs_model.isDir(index):
        return
    if tree.isExpanded(index):
        tree.collapse(index)
    else:
        tree.expand(index)


def _wire_volume_refresh(
    places: _PlacesSidebar,
    model: BrowserTreeModel,
    parent: QObject,  # noqa: ARG001 — kept for the old call signature
) -> VolumePoller:
    """Feed the places list from the tree model's background volume poller."""
    poller = model.poller()
    poller.volumes_changed.connect(places.set_volumes)
    known = poller.volumes()
    if known is not None:
        places.set_volumes(known)
    return poller


# ---------------------------------------------------------------------------
# Directory search: background worker + searchable tree panel
# ---------------------------------------------------------------------------

_SEARCH_SKIP_DIRS = SEARCH_SKIP_DIRS
_SEARCH_SKIP_ABSPATHS = SEARCH_SKIP_ABSPATHS
_SEARCH_SKIP_FILES = SEARCH_SKIP_FILES
_SEARCH_SKIP_SUFFIXES = SEARCH_SKIP_SUFFIXES
_VIDEO_EXTS = VIDEO_EXTS

_MAX_SEARCH_DEPTH = MAX_SEARCH_DEPTH
_SEARCH_BATCH_SIZE = SEARCH_BATCH_SIZE
_MAX_SEARCH_RESULTS = MAX_SEARCH_RESULTS
_SEARCH_DEBOUNCE_MS = 200


class _DirSearchWorker(QObject):
    """Recursive folder / file name search, in a child process.

    The walk itself is :func:`src.core.dir_search.run_dir_search_process`;
    see :class:`~src.core.bg_worker.BackgroundWorker` for why it is not a
    thread. ``start_search`` replaces any running query. Results stream back
    via ``batch_ready`` in chunks, on the GUI thread.
    """

    batch_ready = Signal(list)
    search_finished = Signal(int)
    _event = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        ext_filter: frozenset[str] | None = None,
        dirs_only: bool = False,
        *,
        use_process: bool = True,
    ):
        super().__init__(parent)
        self._ext_filter = ext_filter
        self._dirs_only = dirs_only
        self._use_process = use_process
        self._token = 0
        self._worker: BackgroundWorker | None = None
        self._event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self.destroyed.connect(lambda *_: self.shutdown())

    def start_search(self, root: str, query: str) -> None:
        self._token += 1
        exts = sorted(self._ext_filter) if self._ext_filter is not None else None
        self._ensure_worker().post(("search", self._token, root, query, exts, self._dirs_only))

    def cancel(self) -> None:
        self._token += 1
        if self._worker is not None:
            self._worker.post(("cancel",))

    def shutdown(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.stop(timeout=0.5)

    def _ensure_worker(self) -> BackgroundWorker:
        if self._worker is not None and self._worker.crashed:
            log.warning("Folder search process exited; continuing on a thread")
            self._use_process = False
            self._worker = None
        if self._worker is None or not self._worker.is_alive():
            signal = self._event

            def deliver(event: tuple) -> None:
                try:
                    signal.emit(event)
                except RuntimeError:
                    pass  # QObject gone (dialog closed)

            self._worker = BackgroundWorker(
                run_dir_search_process, deliver, name="FolderSearch", use_process=self._use_process
            )
        return self._worker

    def _on_event(self, event: object) -> None:
        if not isinstance(event, tuple) or len(event) < 3 or event[1] != self._token:
            return  # stale: an older query
        if event[0] == "batch":
            self.batch_ready.emit(list(event[2]))
        elif event[0] == "done":
            self.search_finished.emit(int(event[2]))


class _BrowserJobs(QObject):
    """Run a dialog's disk work on the shared browser pool.

    ``run(fn, on_done)`` calls ``fn()`` on a worker thread and ``on_done(result)``
    on the GUI thread (an exception is passed as the result). ``poster(cb)``
    returns a thread-safe callable that queues ``cb(value)`` on the GUI thread,
    for jobs that report row by row. Nothing is delivered after ``shutdown``.
    """

    _result = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._alive = True
        self._result.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    def run(self, fn: Callable[[], object], on_done: Callable[[object], None]) -> None:
        post = self.poster(on_done)

        def job() -> None:
            try:
                res: object = fn()
            except Exception as e:  # handed to on_done; logged for debugging
                log.debug("Browser job failed", exc_info=True)
                res = e
            post(res)

        background_pool().submit(job)

    def poster(self, callback: Callable[[object], None]) -> Callable[[object], None]:
        signal = self._result

        def post(value: object) -> None:
            try:
                signal.emit(callback, value)
            except RuntimeError:
                pass  # dialog deleted

        return post

    def shutdown(self) -> None:
        self._alive = False

    def _dispatch(self, callback: object, value: object) -> None:
        if self._alive and callable(callback):
            callback(value)


def _clear_icon() -> QIcon:
    """Round grey "x" for search fields (drawn, so it centres the same everywhere)."""
    icon = QIcon()
    for edge in (14, 28):
        pm = QPixmap(edge, edge)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_PALETTE.get("FG_DIM", "#8a8a8a")))
        p.drawEllipse(0, 0, edge, edge)
        pen = QPen(QColor(_PALETTE.get("BG_INPUT", "#1e1e1e")))
        pen.setWidthF(edge / 9.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        m = edge * 0.32
        p.drawLine(QPointF(m, m), QPointF(edge - m, edge - m))
        p.drawLine(QPointF(edge - m, m), QPointF(m, edge - m))
        p.end()
        icon.addPixmap(pm)
    return icon


class _SearchField(QLineEdit):
    """Search box with its own clear button and busy spinner.

    ``QLineEdit``'s built-in clear button is placed by the platform style and
    sat low in the field on macOS once the app style sheet set padding and a
    minimum height. These children are positioned in ``resizeEvent`` at the
    exact vertical centre instead.
    """

    _SPINNER = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clear_btn = QToolButton(self)
        self._clear_btn.setIcon(_clear_icon())
        self._clear_btn.setIconSize(QSize(14, 14))
        self._clear_btn.setFixedSize(18, 18)
        self._clear_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_btn.setToolTip("Clear")
        self._clear_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0; margin: 0; }"
        )
        self._clear_btn.clicked.connect(self._on_clear)
        self._clear_btn.hide()

        self._spinner = QLabel(self)
        self._spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner.setFixedSize(18, 18)
        self._spinner.setStyleSheet("background: transparent; border: none; padding: 0;")
        self._spinner.hide()
        self._spin_idx = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(80)
        self._spin_timer.timeout.connect(self._advance_spinner)

        self.textChanged.connect(self._sync_buttons)
        self._sync_buttons()

    def clear_button(self) -> QToolButton:
        return self._clear_btn

    def set_busy(self, busy: bool) -> None:
        if busy:
            self._spin_idx = 0
            self._advance_spinner()
            self._spin_timer.start()
        else:
            self._spin_timer.stop()
        self._spinner.setVisible(busy)
        self._layout_children()

    def is_busy(self) -> bool:
        return self._spin_timer.isActive()

    def _advance_spinner(self) -> None:
        self._spinner.setText(self._SPINNER[self._spin_idx % len(self._SPINNER)])
        self._spin_idx += 1

    def _on_clear(self) -> None:
        self.clear()
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def _sync_buttons(self, *_args) -> None:
        self._clear_btn.setVisible(bool(self.text()))
        self._layout_children()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self) -> None:
        right = 4
        reserved = 4
        for w in (self._clear_btn, self._spinner):
            if w.isHidden():
                continue
            x = self.width() - right - w.width()
            w.move(x, (self.height() - w.height()) // 2)
            right += w.width() + 2
            reserved += w.width() + 2
        self.setTextMargins(0, 0, reserved, 0)


class _SearchableTree(QWidget):
    """Search bar + directory tree, with inline search results that replace
    the tree while a query is active.

    Typing in the search field triggers a debounced background scan.
    Clicking a result emits ``result_navigated`` with the directory path.
    Clearing the field (or pressing Escape) restores the tree.
    """

    result_navigated = Signal(str)

    def __init__(
        self,
        tree: QTreeView,
        parent: QWidget | None = None,
        ext_filter: frozenset[str] | None = None,
        dirs_only: bool = False,
    ):
        super().__init__(parent)
        self._tree = tree
        self._search_root = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._search_edit = _SearchField()
        self._search_edit.setPlaceholderText("\U0001f50d  Search folders\u2026")
        layout.addWidget(self._search_edit)

        layout.addWidget(tree, 1)

        self._results = QListWidget()
        self._results.setUniformItemSizes(True)
        self._results.setVisible(False)
        layout.addWidget(self._results, 1)

        self._search_status = QLabel()
        self._search_status.setVisible(False)
        self._search_status.setStyleSheet(STATUS_DIM)
        layout.addWidget(self._search_status)

        self._worker = _DirSearchWorker(self, ext_filter=ext_filter, dirs_only=dirs_only)
        self._worker.batch_ready.connect(self._on_batch)
        self._worker.search_finished.connect(self._on_search_done)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._fire_search)

        self._search_edit.textChanged.connect(self._on_text_changed)
        self._results.itemClicked.connect(self._on_result_clicked)
        self._results.itemDoubleClicked.connect(self._on_result_clicked)

    def set_search_root(self, path: str) -> None:
        self._search_root = path

    def shutdown(self) -> None:
        """Stop the search process (dialog closing)."""
        self._debounce.stop()
        self._search_edit.set_busy(False)
        self._worker.shutdown()

    def _on_text_changed(self, text: str) -> None:
        if not text.strip():
            self._worker.cancel()
            self._debounce.stop()
            self._search_edit.set_busy(False)
            self._results.setVisible(False)
            self._tree.setVisible(True)
            self._search_status.setVisible(False)
            return
        self._debounce.start()

    def _fire_search(self) -> None:
        query = self._search_edit.text().strip()
        if not query:
            return
        self._results.clear()
        self._results.setVisible(True)
        self._tree.setVisible(False)
        self._search_status.setVisible(True)
        self._search_status.setText("Searching\u2026")
        self._search_edit.set_busy(True)
        root = self._search_root or QDir.homePath()
        self._worker.start_search(root, query)

    def _on_batch(self, items: list) -> None:
        self._results.setUpdatesEnabled(False)
        try:
            for name, full_path, rel_path, is_dir in items:
                icon = "\U0001f4c1" if is_dir else "\U0001f4c4"
                parent = os.path.dirname(rel_path)
                display = f"{icon}  {name}    {parent}" if parent else f"{icon}  {name}"
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, full_path)
                item.setData(Qt.ItemDataRole.UserRole + 1, bool(is_dir))
                item.setToolTip(full_path)
                self._results.addItem(item)
        finally:
            self._results.setUpdatesEnabled(True)

    def _on_search_done(self, total: int) -> None:
        self._search_edit.set_busy(False)
        suffix = "s" if total != 1 else ""
        cap = " (limit reached)" if total >= _MAX_SEARCH_RESULTS else ""
        self._search_status.setText(f"{total} result{suffix}{cap}")

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        # The walk already knows whether this is a folder: no stat here.
        is_dir = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        target = path if is_dir else os.path.dirname(path)
        self.result_navigated.emit(target)
        self._search_edit.clear()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._search_edit.text():
            self._search_edit.clear()
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Image sequence browser dialog (list / grid + metadata inspector)
# ---------------------------------------------------------------------------

# View mode string constants (aliases for browser_state; keep local names for
# call-site readability inside the dialog classes).
_SEQ_BROWSER_VIEW_LIST = VIEW_LIST
_SEQ_BROWSER_VIEW_GRID = VIEW_GRID
_SEQ_BROWSER_VIEW_PREVIEW = VIEW_PREVIEW
_VID_BROWSER_VIEW_LIST = VIEW_LIST
_VID_BROWSER_VIEW_GRID = VIEW_GRID
_VID_BROWSER_VIEW_PREVIEW = VIEW_PREVIEW


def _configure_path_line_edit(edit: QLineEdit) -> None:
    """Path field must not grow the dialog when the text is very long.

    ``QLineEdit.sizeHint()`` tracks content width; with a normal Expanding
    policy that becomes the layout minimum. Horizontal *Ignored* lets the
    layout assign width (stretch=1) without following path length.
    """
    edit.setMinimumWidth(80)
    edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)


class _ElidingLabel(QLabel):
    """Status/footer label that elides long text instead of forcing layout width.

    Plain ``QLabel`` uses the full text for ``sizeHint``, which expands dialogs
    when the status line contains a long path. This widget ignores content
    width and shows an elided string (middle ellipsis) for the current width.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setWordWrap(False)

    def setText(self, text: str | None) -> None:  # type: ignore[override]
        self._full_text = str(text or "")
        # Full text on hover when truncated.
        self.setToolTip(self._full_text if len(self._full_text) > 24 else "")
        self._apply_elide()

    def text(self) -> str:  # type: ignore[override]
        return self._full_text

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        fm = self.fontMetrics()
        w = max(1, self.width() - 2)
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, w)
        # Bypass our setText to avoid recursion / clearing tooltip.
        QLabel.setText(self, elided)


_SEQ_THUMB_EDGE = 160
_SEQ_THUMB_ICON = QSize(160, 100)


class _ThumbSignals(QObject):
    """Signals for :class:`_ThumbJob` (QRunnable cannot define Qt signals)."""

    ready = Signal(int, int, object)  # gen, row, QImage | None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class _ThumbJob(QRunnable):
    """Background first-frame thumbnail decode for one sequence row."""

    def __init__(self, gen: int, row: int, path: str, signals: _ThumbSignals) -> None:
        super().__init__()
        self._gen = gen
        self._row = row
        self._path = path
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        # Lazy import: this runs on a QThreadPool worker thread, not the GUI
        # thread — avoid pulling numpy / OIIO onto the import path for callers
        # that never open a browser dialog.
        import numpy as np

        from .browser_thumbs import load_browser_thumbnail_rgb, load_video_thumbnail_rgb

        qimg: QImage | None = None
        try:
            if not can_probe_media(self._path):
                self._signals.ready.emit(self._gen, self._row, None)
                return
            ext = Path(self._path).suffix.lower()
            if ext in _VIDEO_EXTS:
                rgb = load_video_thumbnail_rgb(self._path, max_edge=_SEQ_THUMB_EDGE)
            else:
                rgb = load_browser_thumbnail_rgb(self._path, max_edge=_SEQ_THUMB_EDGE)
            if rgb is not None and rgb.ndim == 3 and rgb.shape[2] >= 3:
                h, w = int(rgb.shape[0]), int(rgb.shape[1])
                # Copy so QImage owns a stable buffer after the numpy array frees.
                buf = np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8).copy()
                qimg = QImage(buf.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        except Exception:
            qimg = None
        self._signals.ready.emit(self._gen, self._row, qimg)
