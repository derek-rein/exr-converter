from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.local_fs import can_probe_media, path_is_dir, path_is_file
from ..core.video import list_video_files, probe_video_metadata, probe_video_row
from .browser_chrome import (
    _SEQ_THUMB_ICON,
    _VID_BROWSER_VIEW_GRID,
    _VID_BROWSER_VIEW_LIST,
    _VID_BROWSER_VIEW_PREVIEW,
    _VIDEO_EXTS,
    _BrowserJobs,
    _ThumbJob,
    _ThumbSignals,
)
from .browser_path import resolve_video_browser_path, same_dir
from .browser_state import (
    VID_BROWSER_KEYS,
    BrowserPreviewContext,
    browser_qsettings,
    dirs_equal,
)
from .browser_tree import FILE_MODE_VIDEOS, KIND_FILE, BrowserTreeModel
from .media_browser import _letterbox_thumb, _MediaBrowserDialog
from .player.sequence_player import SequencePlayer

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Video file browser dialog (with metadata inspector)
# ---------------------------------------------------------------------------


class VideoBrowserDialog(_MediaBrowserDialog):
    """Directory browser + video file table + in-dialog playback preview."""

    _WINDOW_TITLE = "Browse Video Files"
    _COLUMNS = ["Name", "Resolution", "Codec", "FPS", "Frames", "Duration"]
    _META_CONTENT_MIN = 200
    _META_SIZES_BEFORE_TOGGLE = True

    def __init__(
        self,
        start_dir: str = "",
        parent: QWidget | None = None,
        *,
        preview: BrowserPreviewContext | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self._WINDOW_TITLE)
        self.resize(1360, 760)
        self._keys = VID_BROWSER_KEYS
        self._preview_ctx = preview or BrowserPreviewContext()
        self._selected_path: str = ""
        self._file_data: list[dict[str, str]] = []
        self._auto_select_path: str = ""
        self._player = None
        self._previewing = False
        self._last_browse_mode = _VID_BROWSER_VIEW_LIST
        self._same_path_session = False
        self._pending_preview = False
        self._thumb_gen = 0
        self._thumb_cache: dict[str, QPixmap] = {}
        self._placeholder_icon = self._make_vid_placeholder_icon()
        self._thumb_signals = _ThumbSignals(self)
        self._thumb_signals.ready.connect(self._on_vid_thumb_ready)
        self._thumb_pool = QThreadPool.globalInstance()
        # Disk work runs on the shared browser pool; generations drop stale replies.
        self._jobs = _BrowserJobs(self)
        self._scan_gen = 0
        self._scanning = False
        self._meta_gen = 0
        self._preview_gen = 0
        self._accept_after_scan = False
        # Video loaded in Preview (its in/out points apply to Open).
        self._in_out_media = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        # Don't let children force the dialog wider than the user sized it.
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetDefaultConstraint)

        path_row = self._build_path_row(
            grid_tip="Grid view with first-frame thumbnails",
            preview_tip="Playback of the selected / first video",
            inspect_tip="Show video metadata for selected / previewed file",
        )
        layout.addLayout(path_row)

        # -- left: places sidebar + dir tree (full height) --
        def make_model() -> BrowserTreeModel:
            # Folders + video files, listed in the background.
            from ..core.source_plugins import plugin_suffixes

            return BrowserTreeModel(
                self,
                file_mode=FILE_MODE_VIDEOS,
                file_exts=set(_VIDEO_EXTS) | set(plugin_suffixes()),
            )

        left_panel = self._build_folder_panel(make_model, ext_filter=_VIDEO_EXTS)

        # -- center: video file table --
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)

        self._build_table(min_width=200)
        self._table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._table.setWordWrap(False)
        th = self._table.horizontalHeader()
        for col in range(1, len(self._COLUMNS)):
            th.resizeSection(col, 88)
        th.resizeSection(1, 100)
        th.resizeSection(2, 80)
        th.resizeSection(3, 56)
        th.resizeSection(4, 64)
        th.resizeSection(5, 72)
        # table alone is not the center — stack holds list/grid/preview
        self._list_page = QWidget()
        list_layout = QVBoxLayout(self._list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.addWidget(self._table, 1)

        self._build_grid(min_width=200)
        self._grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)

        # Preview page (SequencePlayer via load_video)
        self._build_preview_page()

        self._view_stack = QStackedWidget()
        self._view_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._view_stack.addWidget(self._list_page)  # 0 list
        self._view_stack.addWidget(self._grid)  # 1 grid
        self._view_stack.addWidget(self._preview_page)  # 2 preview
        center_layout.addWidget(self._view_stack, 1)
        center.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center.setMinimumWidth(200)

        # Pre-create player before dialog show (OpenGL surface rule).
        try:
            self._ensure_player()
        except Exception:
            log.exception("Could not pre-create video player")

        # -- right: metadata inspector --
        self._build_meta_panel("Video Metadata")

        # content splitter: list/grid/preview + metadata
        self._build_content_splitter(center)

        # right side: content splitter + status/buttons row (footer is under
        # the content only, not under the folder tree)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._content_splitter, 1)
        right_layout.addLayout(self._build_footer())

        # outer splitter: left panel (full height) | right side
        self._build_outer_splitter(left_panel, right_widget)
        # Places sidebar is 150 px fixed, so the folder tree gets the rest:
        # 240 left it about 90 px, too narrow to read a folder name. The dialog is
        # wide enough that the Name column still gets room beside Metadata.
        self._outer_splitter.setSizes([360, 1000])
        layout.addWidget(self._outer_splitter, 1)

        self._wire_layout_save()
        self._wire_browse_signals()
        self._table.cellDoubleClicked.connect(lambda _r, _c: self.accept())

        settings = browser_qsettings()
        keys = self._keys
        # Resolve start folder + optional file to select.
        start_folder = ""
        if start_dir:
            d = Path(start_dir)
            if path_is_file(d):
                self._auto_select_path = str(d)
                d = d.parent
            if path_is_dir(d):
                start_folder = str(d)

        saved_dir = str(settings.value(keys.last_dir, "") or "")
        self._same_path_session = bool(start_folder) and dirs_equal(start_folder, saved_dir)
        self._restore_view_and_metadata(settings)

        if self._same_path_session and not self._auto_select_path:
            self._auto_select_path = str(settings.value(keys.selected, "") or "")

        self._restore_browser_layout()

        if start_folder:
            self._navigate_to(start_folder, restore_tree=self._same_path_session)
        elif self._same_path_session and saved_dir and path_is_dir(saved_dir):
            self._navigate_to(saved_dir, restore_tree=True)

        self._settle_pending_preview()

    def selected_path(self) -> str:
        return self._selected_path

    # -- hooks for _MediaBrowserDialog -------------------------------------

    _TREE_ITEM_KIND = KIND_FILE

    def _on_tree_item_clicked(self, index) -> None:
        self._select_tree_file(self._fs_model.filePath(index))

    def _load_preview(self) -> None:
        self._load_preview_video()

    def _queue_grid_thumbnails(self) -> None:
        self._queue_video_thumbnails()

    def _layout_selected(self) -> str:
        return self._selected_path

    def _make_vid_placeholder_icon(self) -> QIcon:
        pm = QPixmap(_SEQ_THUMB_ICON)
        pm.fill(QColor(0x2A, 0x2A, 0x2A))
        return QIcon(pm)

    def _load_preview_video(self) -> None:
        self._previewing = True
        self._view_stack.setCurrentIndex(2)
        path = self._selected_path
        if not path and self._file_data:
            path = str(self._file_data[0].get("path") or "")
            if path:
                self._selected_path = path
                self._ok_btn.setEnabled(True)
                self._table.selectRow(0)
                if self._grid.count() > 0:
                    self._grid.setCurrentRow(0)
        if not path:
            self._status.setText("No video to preview in this folder")
            self._stop_preview_playback(clear_only=True)
            return

        player = self._player
        if player is not None and path == self._in_out_media and player.restart_prefetch():
            # Same clip as last time (Space / Esc round trip): keep cache and in/out.
            player.setFocus(Qt.FocusReason.OtherFocusReason)
            self._status.setText(f"Preview · {Path(path).name}")
            return

        ctx = self._preview_ctx
        label = Path(path).name
        self._status.setText(f"Loading preview \u00b7 {label}\u2026")
        # Probing the file opens it: do that on a worker, then load the player.
        self._preview_gen += 1
        gen = self._preview_gen
        cfg, src = ctx.ocio_cfg, ctx.src_colorspace or ""
        self._jobs.run(
            lambda p=path: SequencePlayer.prepare_video(p, ocio_cfg=cfg, src_colorspace=src),
            lambda result, g=gen, p=path: self._on_preview_prepared(g, p, result),
        )

    def _on_preview_prepared(self, gen: int, path: str, result: object) -> None:
        if gen != self._preview_gen or not self._previewing:
            return
        self._in_out_media = ""
        label = Path(path).name
        if isinstance(result, BaseException):
            log.warning("Video preview probe failed for %s: %s", path, result)
            self._status.setText(f"Could not open {label}")
            return
        ctx = self._preview_ctx
        try:
            player = self._ensure_player()
        except Exception as e:
            log.exception("Failed to create video player")
            self._status.setText(f"Preview unavailable: {e}")
            return

        try:
            ok = player.load_video(
                path,
                ocio_cfg=ctx.ocio_cfg,
                src_colorspace=ctx.src_colorspace or "",
                prepared=result if isinstance(result, dict) else None,
            )
        except Exception as e:
            log.exception("Video preview load failed for %s", path)
            self._status.setText(f"Preview failed: {e}")
            return

        self._in_out_media = path if ok else ""
        if ok:
            self._status.setText(f"Preview · {label}")
        else:
            self._status.setText(f"Could not open {label}")
        self.setWindowTitle(f"{self._WINDOW_TITLE} — {label}")
        player.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, player.fit_in_view)

    def _shutdown_player(self) -> None:
        self._previewing = False
        player = self._player
        self._player = None
        if player is not None:
            self._shutdown_player_widget(player)

    def accept(self) -> None:
        mode = self._view_seg.currentData()
        if mode == _VID_BROWSER_VIEW_GRID:
            item = self._grid.currentItem()
            if item is not None:
                row = int(item.data(Qt.ItemDataRole.UserRole) or 0)
                if 0 <= row < len(self._file_data):
                    self._selected_path = str(self._file_data[row].get("path") or "")
        elif mode != _VID_BROWSER_VIEW_PREVIEW:
            rows = self._table.selectionModel().selectedRows()
            if rows:
                item = self._table.item(rows[0].row(), 0)
                if item is not None:
                    self._selected_path = item.data(Qt.ItemDataRole.UserRole) or ""
        if not self._selected_path:
            return
        self._finish_accept()

    def _teardown(self) -> None:
        self._shutdown_workers()
        self._shutdown_player()

    def _in_out_matches_selection(self) -> bool:
        return self._in_out_media == self._selected_path

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._path_edit and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent) and event.matches(QKeySequence.StandardKey.Paste):
                QTimer.singleShot(0, self._on_path_entered)
                return False
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()  # type: ignore[attr-defined]
            if obj is self._path_edit:
                return super().eventFilter(obj, event)
            if key == Qt.Key.Key_Space and not event.isAutoRepeat():  # type: ignore[attr-defined]
                if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                    # Let the player handle Space for play/pause when focused.
                    if self._player is not None and self._player.hasFocus():
                        return False
                    self._view_seg.setCurrentData(self._last_browse_mode)
                else:
                    self._view_seg.setCurrentData(_VID_BROWSER_VIEW_PREVIEW)
                return True
            if key == Qt.Key.Key_Escape:
                if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                    self._view_seg.setCurrentData(self._last_browse_mode)
                    return True
        return super().eventFilter(obj, event)

    def _select_tree_file(self, path: str) -> bool:
        """Show a video from the tree: open its folder and select it.

        Returns True when the file is already selected in the list.
        """
        directory = os.path.dirname(path)
        if same_dir(directory, self._path_edit.text()) and self._file_data and not self._scanning:
            for row, f in enumerate(self._file_data):
                if f.get("path") == path:
                    self._select_video_row(row)
                    self._table.scrollToItem(
                        self._table.item(row, 0), QAbstractItemView.ScrollHint.EnsureVisible
                    )
                    if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                        self._load_preview_video()
                    return True
        self._auto_select_path = path
        self._enter_folder(directory)
        return False

    def _on_tree_double_clicked(self, index) -> None:
        """Double-click a video in the tree to open it."""
        if self._fs_model.kind(index) != KIND_FILE:
            return
        if self._select_tree_file(self._fs_model.filePath(index)) and self._selected_path:
            self.accept()
        else:
            self._accept_after_scan = True

    def _navigate_to_path_string(self, raw: str, *, prefer_preview: bool = True) -> None:
        resolved = resolve_video_browser_path(raw, _VIDEO_EXTS)
        if resolved is None:
            return
        directory, select_path = resolved
        self._auto_select_path = select_path
        if prefer_preview and select_path:
            self._pending_preview = True
            self._view_seg.blockSignals(True)
            self._view_seg.setCurrentData(_VID_BROWSER_VIEW_PREVIEW)
            self._view_seg.blockSignals(False)
        self._navigate_to(directory)

    def _scan_directory(self, directory: str) -> None:
        """Clear the list and read *directory* on a worker thread.

        Listing never opens a file; resolution / codec / fps / duration fill in
        from a second background pass that opens one file at a time.
        """
        was_preview = self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW
        if was_preview:
            self._stop_preview_playback()
        self._thumb_gen += 1
        self._scan_gen += 1
        gen = self._scan_gen
        self._table.setRowCount(0)
        self._grid.clear()
        self._selected_path = ""
        self._file_data = []
        self._ok_btn.setEnabled(False)
        self._meta_gen += 1
        self._meta_text.clear()
        self._scanning = True
        self._status.setText(f"Reading {Path(directory).name or directory}\u2026")
        self._jobs.run(
            lambda d=directory: list_video_files(d),
            lambda result, g=gen, wp=was_preview: self._on_scan_listed(g, result, wp),
        )

    def _on_scan_listed(self, gen: int, result: object, was_preview: bool) -> None:
        if gen != self._scan_gen:
            return
        self._scanning = False
        accept_after = self._accept_after_scan
        self._accept_after_scan = False
        if isinstance(result, BaseException):
            self._drop_pending_preview()
            self._status.setText(f"Error: {result}")
            return
        files = list(result) if isinstance(result, list) else []
        if not files:
            self._drop_pending_preview()
            self._status.setText("No video files in this folder.")
            return

        self._file_data = files
        self._table.setUpdatesEnabled(False)
        self._grid.setUpdatesEnabled(False)
        try:
            self._fill_rows(files)
        finally:
            self._table.setUpdatesEnabled(True)
            self._grid.setUpdatesEnabled(True)

        select_row = -1
        if self._auto_select_path:
            for row, f in enumerate(files):
                if f.get("path") == self._auto_select_path:
                    select_row = row
                    break
        if select_row < 0 and files:
            select_row = 0
        if select_row >= 0:
            self._select_video_row(select_row)
            self._center_on_row(select_row)

        self._status.setText(f"{len(files)} video file(s) found")
        self._probe_rows(gen, files)

        if accept_after and self._selected_path:
            QTimer.singleShot(0, self.accept)
            return
        was_pending = self._pending_preview
        self._pending_preview = False
        mode = self._view_seg.currentData()
        if was_pending and mode == _VID_BROWSER_VIEW_PREVIEW:
            self._on_view_changed(0)  # switches the stack and loads the player
            return
        if mode == _VID_BROWSER_VIEW_GRID:
            self._queue_video_thumbnails()
        elif was_preview or mode == _VID_BROWSER_VIEW_PREVIEW:
            self._load_preview_video()

    _ROW_FIELDS = ((1, "resolution"), (2, "codec"), (3, "fps"), (4, "frames"), (5, "duration"))

    def _fill_rows(self, files: list[dict[str, str]]) -> None:
        self._table.setRowCount(len(files))
        center_align = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        for row, f in enumerate(files):
            name_item = QTableWidgetItem(f["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, f["path"])
            name_item.setToolTip(f["path"])
            self._table.setItem(row, 0, name_item)
            for col, key in self._ROW_FIELDS:
                item = QTableWidgetItem(f.get(key, ""))
                item.setTextAlignment(center_align)
                self._table.setItem(row, col, item)

            gitem = QListWidgetItem(self._placeholder_icon, f["name"])
            gitem.setData(Qt.ItemDataRole.UserRole, row)
            gitem.setToolTip(f["path"])
            gitem.setSizeHint(QSize(_SEQ_THUMB_ICON.width() + 24, _SEQ_THUMB_ICON.height() + 48))
            self._grid.addItem(gitem)

    def _probe_rows(self, gen: int, files: list[dict[str, str]]) -> None:
        """Open each listed file on a worker thread and fill its columns."""
        todo = []
        for row, f in enumerate(files):
            if f.get("probe"):
                f = dict(f)
                todo.append((row, f["path"], lambda f=f: probe_video_row(f["path"], f)))
        self._run_row_probes(gen, todo, self._on_row_probed)

    def _on_row_probed(self, gen: int, value: object) -> None:
        if gen != self._scan_gen or not isinstance(value, tuple):
            return
        row, path, info = value
        if not (0 <= row < len(self._file_data)) or self._file_data[row].get("path") != path:
            return
        f = self._file_data[row]
        f.update(info)
        f["probe"] = ""
        for col, key in self._ROW_FIELDS:
            item = self._table.item(row, col)
            if item is not None:
                item.setText(str(info.get(key, "") or ""))

    def _queue_video_thumbnails(self) -> None:
        if not self._file_data:
            return
        gen = self._thumb_gen
        for row, f in enumerate(self._file_data):
            path = str(f.get("path") or "")
            if not path:
                continue
            cached = self._thumb_cache.get(path)
            if cached is not None:
                self._set_vid_grid_icon(row, cached)
                continue
            self._thumb_pool.start(_ThumbJob(gen, row, path, self._thumb_signals))

    @Slot(int, int, object)
    def _on_vid_thumb_ready(self, gen: int, row: int, qimg: object) -> None:
        if gen != self._thumb_gen:
            return
        if row < 0 or row >= self._grid.count():
            return
        if qimg is None or not isinstance(qimg, QImage) or qimg.isNull():
            return
        pm = QPixmap.fromImage(qimg)
        path = ""
        if 0 <= row < len(self._file_data):
            path = str(self._file_data[row].get("path") or "")
        if path:
            self._thumb_cache[path] = pm
        self._set_vid_grid_icon(row, pm)

    def _set_vid_grid_icon(self, row: int, pm: QPixmap) -> None:
        if row < 0 or row >= self._grid.count():
            return
        item = self._grid.item(row)
        if item is None:
            return
        item.setIcon(QIcon(_letterbox_thumb(pm, QColor(0x1E, 0x1E, 0x1E))))

    def _on_grid_selection(self) -> None:
        item = self._grid.currentItem()
        if item is None:
            return
        row = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if 0 <= row < len(self._file_data):
            self._selected_path = str(self._file_data[row].get("path") or "")
            self._ok_btn.setEnabled(bool(self._selected_path))
            self._table.selectRow(row)
            if self._meta_panel.isVisible():
                self._show_metadata(row)
            if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                self._load_preview_video()

    def _on_grid_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        row = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if 0 <= row < len(self._file_data):
            self._selected_path = str(self._file_data[row].get("path") or "")
            if self._selected_path:
                self.accept()

    def _select_video_row(self, row: int) -> None:
        """Commit video index *row* into selection (table + grid stay in sync)."""
        if row < 0 or row >= len(self._file_data):
            self._selected_path = ""
            self._ok_btn.setEnabled(False)
            return
        self._selected_path = str(self._file_data[row].get("path") or "")
        self._ok_btn.setEnabled(bool(self._selected_path))
        self._table.selectRow(row)
        if 0 <= row < self._grid.count():
            self._grid.setCurrentRow(row)
        if self._meta_panel.isVisible():
            self._show_metadata(row)

    def _select_row(self, row: int) -> None:
        self._select_video_row(row)

    def _copy_path_targets(self) -> tuple[str, str]:
        return self._selected_path, ""

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            row = rows[0].row()
            item = self._table.item(row, 0)
            self._selected_path = item.data(Qt.ItemDataRole.UserRole) if item else ""
            self._ok_btn.setEnabled(bool(self._selected_path))
            if 0 <= row < self._grid.count():
                self._grid.setCurrentRow(row)
            if self._meta_panel.isVisible():
                self._show_metadata(row)
            if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                self._load_preview_video()
        else:
            self._selected_path = ""
            self._ok_btn.setEnabled(False)

    def _metadata_row(self) -> int:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            return rows[0].row()
        if self._selected_path:
            for row, f in enumerate(self._file_data):
                if f.get("path") == self._selected_path:
                    return row
        return -1

    def _show_metadata(self, row: int) -> None:
        """Fill the Metadata pane for *row*; the file is probed on a worker thread."""
        self._meta_gen += 1
        gen = self._meta_gen
        if row < 0 or row >= len(self._file_data):
            self._meta_text.setPlainText("")
            return
        fpath = self._file_data[row]["path"]
        self._meta_text.setPlainText(f"File: {Path(fpath).name}\n\nReading metadata\u2026")

        def job(p: str = fpath) -> object:
            if not can_probe_media(p):
                return None
            return probe_video_metadata(p)

        self._jobs.run(job, lambda meta, g=gen, p=fpath: self._on_metadata(g, p, meta))
