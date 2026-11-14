from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import fileseq
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import IMAGE_SEQUENCE_EXTS, is_image_sequence_ext
from ..core.local_fs import can_probe_media, path_is_dir, path_is_file
from ..core.sdk_support import OptionalSdkError
from ..core.sequence import (
    list_image_sequences,
    looks_like_sequence_pattern,
    probe_exr_metadata,
    probe_sequence_header,
    sequence_pattern_stem,
)
from .browser_chrome import (
    _SEQ_BROWSER_VIEW_GRID,
    _SEQ_BROWSER_VIEW_PREVIEW,
    _SEQ_THUMB_ICON,
    _BrowserJobs,
    _SearchField,
    _ThumbJob,
    _ThumbSignals,
)
from .browser_path import resolve_sequence_browser_path, same_dir
from .browser_state import (
    SEQ_BROWSER_KEYS,
    BrowserPreviewContext,
    browser_qsettings,
    dirs_equal,
    normalize_dir,
)
from .browser_tree import FILE_MODE_SEQUENCES, KIND_SEQUENCE, BrowserTreeModel
from .media_browser import _letterbox_thumb, _MediaBrowserDialog
from .player.sequence_player import SequencePlayer
from .sequence_search import SEARCH_DEBOUNCE_MS, SequenceHit, SequenceIndex
from .style import set_style_property

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Image sequence browser dialog (list / grid + metadata inspector)
# ---------------------------------------------------------------------------


class SequenceBrowserDialog(_MediaBrowserDialog):
    """Directory browser + image sequence list/grid, with in-dialog playback.

    Preview mode replaces the browse body with :class:`SequencePlayer` (Space /
    Preview / context menu). Escape or **Back** returns to browsing.
    """

    _WINDOW_TITLE = "Browse Image Sequences"
    _COLUMNS = [
        "Name",
        "Frames",
        "Range",
        "Resolution",
        "Type",
        "Compression",
        "Color Space",
    ]

    def __init__(
        self,
        start_dir: str = "",
        select_name: str = "",
        parent: QWidget | None = None,
        *,
        preview: BrowserPreviewContext | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self._WINDOW_TITLE)
        self.resize(1360, 760)
        self._keys = SEQ_BROWSER_KEYS
        self._preview_ctx = preview or BrowserPreviewContext()
        self._selected_dir: str = ""
        self._selected_name: str = ""
        # First-frame path of the selected sequence (preferred for set_input so
        # multi-sequence folders resolve to the chosen basename, not sorted[0]).
        self._selected_frame_path: str = ""
        self._seq_data: list[dict] = []
        self._auto_select_name = select_name
        # First-frame path to select after the next scan (fuzzy search jump);
        # disambiguates same-stem sequences with different extensions.
        self._auto_select_frame = ""
        self._syncing_selection = False
        self._same_path_session = False
        self._pending_preview = False
        self._thumb_gen = 0
        self._thumb_cache: dict[str, QPixmap] = {}
        self._placeholder_icon = self._make_placeholder_icon()
        self._thumb_signals = _ThumbSignals(self)
        self._thumb_signals.ready.connect(self._on_thumb_ready)
        # Use the *global* pool — never own a QThreadPool on this dialog.
        # A dialog-owned pool waits for OIIO workers in its destructor and freezes
        # the app after Browse → Open.
        self._thumb_pool = QThreadPool.globalInstance()
        self._player = None  # lazy SequencePlayer for in-dialog preview
        self._previewing = False
        # Disk work (folder scan, header probes, metadata, preview prep) runs
        # on the shared browser pool; generations drop stale replies.
        self._jobs = _BrowserJobs(self)
        self._scan_gen = 0
        self._scanning = False
        self._meta_gen = 0
        self._preview_gen = 0
        # In/out chosen in Preview, and the first frame of the sequence they belong to.
        self._in_out_media = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- Top bar: folder path + List|Grid|Preview + Metadata ----
        path_row = self._build_path_row(
            grid_tip="Grid view with thumbnails",
            preview_tip=(
                "Playback of the first sequence in this folder (VFX: usually one per folder)"
            ),
            seg_tip="Sequence browser layout",
            inspect_tip="Show image metadata for the selected / previewed sequence",
        )
        layout.addLayout(path_row)

        # -- left: places sidebar + dir tree (stays visible in preview) --
        # Multi-root: each mounted volume is a top-level row (macOS /Volumes is
        # hidden from QFileSystemModel under ``/``; Windows other drives too).
        # Folders + image sequences (one row per sequence), listed in the background.
        left_panel = self._build_folder_panel(
            lambda: BrowserTreeModel(self, file_mode=FILE_MODE_SEQUENCES), dirs_only=True
        )

        # -- center: list table OR thumbnail grid --
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)

        self._build_table(min_width=280)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        th = self._table.horizontalHeader()
        th.setSectionsMovable(False)
        th.setMinimumSectionSize(48)
        # Sensible first-open defaults for non-name columns (QSettings can override).
        th.resizeSection(1, 64)
        th.resizeSection(2, 92)
        th.resizeSection(3, 92)
        th.resizeSection(4, 72)
        # Leave the stretch to Name: the sequence name is what people read.
        th.resizeSection(5, 96)
        th.resizeSection(6, 104)
        th.resizeSection(5, 100)
        th.resizeSection(6, 120)
        # Keep Name readable under squeeze (global min is 48; Name wants more).
        self._name_col_min = 140
        th.sectionResized.connect(self._on_table_section_resized)

        self._build_grid(min_width=360)
        self._grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._grid.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # List | Grid | Preview share one stack (Inspect stays beside them).
        self._build_preview_page()

        self._view_stack = QStackedWidget()
        self._view_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._view_stack.addWidget(self._table)  # 0 = list
        self._view_stack.addWidget(self._grid)  # 1 = grid
        self._view_stack.addWidget(self._preview_page)  # 2 = preview
        center.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center.setMinimumWidth(360)

        # Fuzzy sequence search over an in-memory index of folders visited in
        # this dialog. Results replace the list/grid while the field has text.
        self._seq_search_edit = _SearchField()
        self._seq_search_edit.setPlaceholderText("Find sequence (fuzzy, e.g. shot_010_comp)\u2026")
        self._seq_search_edit.set_leading_icon("search")
        self._seq_search_edit.setToolTip(
            "Typo-tolerant search over image sequences in folders opened in this "
            "browser and their subfolders. Enter or click a result to jump to it."
        )
        center_layout.addWidget(self._seq_search_edit)

        self._search_page = QWidget()
        search_layout = QVBoxLayout(self._search_page)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(4)
        self._search_results = QListWidget()
        self._search_results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._search_results.setUniformItemSizes(True)
        search_layout.addWidget(self._search_results, 1)
        self._search_empty = QLabel()
        self._search_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._search_empty.setWordWrap(True)
        set_style_property(self._search_empty, "role", "status")
        search_layout.addWidget(self._search_empty, 1)
        self._search_info = QLabel()
        set_style_property(self._search_info, "role", "status")
        search_layout.addWidget(self._search_info)
        self._view_stack.addWidget(self._search_page)  # 3 = search results
        center_layout.addWidget(self._view_stack, 1)

        # -- Metadata panel (available in list, grid, and preview) --
        self._build_meta_panel("Image Metadata")
        self._meta_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        # content: list/grid/player + optional metadata
        self._build_content_splitter(center)

        # outer: folder tree | content
        layout.addWidget(self._build_outer_splitter(left_panel, self._content_splitter), 1)

        # Footer: status + Open/Cancel, full dialog width (mode is the segment).
        layout.addLayout(self._build_footer())

        # Default proportions (overridden by QSettings when present).
        # Places sidebar is 150 px fixed, so the folder tree gets the rest:
        # 240 left it about 90 px, too narrow to read a folder name. The dialog is
        # wide enough that the Name column still gets room beside Metadata.
        self._outer_splitter.setSizes([330, 1030])
        self._wire_layout_save()
        self._wire_browse_signals()
        self._table.cellDoubleClicked.connect(self._on_table_double_clicked)

        # Fuzzy search: index thread + debounced queries (never on the GUI thread).
        self._seq_index = SequenceIndex()
        self._seq_index.signals.index_changed.connect(
            self._on_seq_index_changed, Qt.ConnectionType.QueuedConnection
        )
        self._seq_index.signals.search_ready.connect(
            self._on_seq_search_ready, Qt.ConnectionType.QueuedConnection
        )
        # Safety net if the dialog is destroyed without accept/reject/close.
        _index = self._seq_index
        self.destroyed.connect(lambda *_: _index.stop(timeout=0.5))
        self._search_gen = 0
        self._index_count = 0
        self._index_busy = False
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self._run_seq_search)
        self._seq_search_edit.textChanged.connect(self._on_seq_search_text)
        self._seq_search_edit.installEventFilter(self)
        self._search_results.itemActivated.connect(self._on_search_item_activated)
        self._search_results.itemClicked.connect(self._on_search_item_activated)
        self._fs_model.volumes_changed.connect(self._on_volumes_changed_index)

        # Session restore: layout + metadata pane always; tree/selection when same dir.
        settings = browser_qsettings()
        keys = self._keys
        saved_dir = str(settings.value(keys.last_dir, "") or "")
        start_norm = normalize_dir(start_dir) if start_dir else ""
        self._same_path_session = bool(start_norm) and dirs_equal(start_dir, saved_dir)
        self._restore_view_and_metadata(settings)

        # When reopening the same folder, prefer last in-dialog selection if the
        # caller did not already pin a sequence name from the convert tab.
        if self._same_path_session and not self._auto_select_name:
            self._auto_select_name = str(settings.value(keys.selected, "") or "")

        # Geometry / splitters / column widths from last session.
        self._restore_browser_layout()

        # Create the sequence player (and its QOpenGLWidget) *before* the dialog
        # is shown. Qt 6.4+: first QOpenGLWidget in an already-shown top-level
        # recreates the native window and can SIGSEGV on macOS. Slate is fine
        # because its player is built in the dialog constructor; match that.
        try:
            self._ensure_player()
        except Exception:
            log.exception("Could not pre-create sequence player for browser")

        start_folder = ""
        if start_dir:
            d = Path(start_dir)
            if path_is_file(d):
                # First-frame path from convert tab — open its parent folder.
                if not self._auto_select_name:
                    stem = sequence_pattern_stem(d.name)
                    if stem is None:
                        m = re.match(
                            r"^(?P<head>.+)\.(?P<frame>\d+)\.(?P<ext>[A-Za-z0-9]+)$",
                            d.name,
                            re.I,
                        )
                        self._auto_select_name = m.group("head") if m else d.stem
                    else:
                        self._auto_select_name = stem
                d = d.parent
            elif looks_like_sequence_pattern(start_dir):
                if not self._auto_select_name:
                    self._auto_select_name = sequence_pattern_stem(d.name) or ""
                d = d.parent
            if path_is_dir(d):
                start_folder = str(d)
        if start_folder:
            self._navigate_to(start_folder, restore_tree=self._same_path_session)
        elif self._same_path_session and saved_dir and path_is_dir(saved_dir):
            self._navigate_to(saved_dir, restore_tree=True)

        self._settle_pending_preview()

    # -- hooks for _MediaBrowserDialog -------------------------------------

    _TREE_ITEM_KIND = KIND_SEQUENCE

    def _on_tree_item_clicked(self, index) -> None:
        self._select_tree_sequence(index)

    def _index_folder(self, directory: str) -> None:
        self._seq_index.ensure_indexed(directory)

    def _before_view_change(self) -> None:
        # Picking List / Grid / Preview leaves search results.
        self._end_seq_search(restore_view=False)

    def _load_preview(self) -> None:
        self._load_preview_sequence()

    def _queue_grid_thumbnails(self) -> None:
        self._queue_thumbnails()

    def _layout_last_dir(self) -> str:
        return self._path_edit.text().strip() or self._selected_dir

    def _layout_selected(self) -> str:
        return self._selected_name

    def _on_table_section_resized(self, logical_index: int, _old: int, new_size: int) -> None:
        """If a side column grows so much that Name would starve, clamp it."""
        if logical_index == 0:
            return
        th = self._table.horizontalHeader()
        name_w = th.sectionSize(0)
        floor = int(getattr(self, "_name_col_min", 140))
        if name_w >= floor:
            return
        # Shrink the resized column just enough to restore the Name floor.
        deficit = floor - name_w
        th.blockSignals(True)
        try:
            th.resizeSection(logical_index, max(48, new_size - deficit))
        finally:
            th.blockSignals(False)

    @staticmethod
    def _make_placeholder_icon() -> QIcon:
        pm = QPixmap(_SEQ_THUMB_ICON)
        pm.fill(QColor(36, 36, 40))
        painter = QPainter(pm)
        painter.setPen(QColor(90, 90, 98))
        painter.drawRect(0, 0, pm.width() - 1, pm.height() - 1)
        painter.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), "\u2026")
        painter.end()
        return QIcon(pm)

    def selected_directory(self) -> str:
        """Directory that was scanned (parent of sequences)."""
        return self._selected_dir

    def selected_name(self) -> str:
        return self._selected_name

    def selected_path(self) -> str:
        """Filesystem path to open as input: first frame of the selected sequence.

        Prefer this over :meth:`selected_directory` — a directory alone always
        resolves to the first sequence on disk, which is wrong when the folder
        has several sequences.
        """
        if self._selected_frame_path:
            return self._selected_frame_path
        return self._selected_dir

    def _select_tree_sequence(self, index) -> bool:
        """Show a sequence row from the tree: open its folder and select it.

        Returns True when the sequence is already selected in the list.
        """
        first = self._fs_model.first_frame(index)
        directory = (
            os.path.dirname(first) if first else os.path.dirname(self._fs_model.filePath(index))
        )
        if not directory:
            return False
        if same_dir(directory, self._selected_dir) and self._seq_data and not self._scanning:
            want = os.path.normpath(first)
            for row, s in enumerate(self._seq_data):
                if os.path.normpath(str(s.get("first_frame") or "")) == want:
                    self._apply_selection(row)
                    self._table.scrollToItem(
                        self._table.item(row, 0), QAbstractItemView.ScrollHint.EnsureVisible
                    )
                    if self._view_seg.currentData() == _SEQ_BROWSER_VIEW_PREVIEW:
                        self._load_preview_sequence()
                    return True
        self._auto_select_frame = first
        self._enter_folder(directory)
        return False

    def _on_tree_double_clicked(self, index) -> None:
        """Double-click a sequence in the tree to open it."""
        if self._fs_model.kind(index) != KIND_SEQUENCE:
            return
        if self._select_tree_sequence(index) and self._ok_btn.isEnabled():
            self.accept()
        else:
            # Folder still scanning: open as soon as the row is selected.
            self._accept_after_scan = True

    def _navigate_to_path_string(self, raw: str, *, prefer_preview: bool = True) -> None:
        """Resolve *raw* to a folder + optional sequence selection and open it."""
        resolved = resolve_sequence_browser_path(raw)
        if resolved is None:
            return
        directory, select_name = resolved
        self._auto_select_name = select_name
        if prefer_preview:
            # Paste from Nuke should land in Preview with the sequence selected.
            self._pending_preview = True
            self._view_seg.blockSignals(True)
            self._view_seg.setCurrentData(_SEQ_BROWSER_VIEW_PREVIEW)
            self._view_seg.blockSignals(False)
        self._navigate_to(directory)

    def _scan_directory(self, directory: str) -> None:
        """Clear the list and read *directory* on a worker thread.

        Listing is metadata only (fast even on Dropbox); header columns
        (resolution, type, compression, colour space) fill in afterwards from
        a second background pass, so a slow folder never freezes the dialog.
        """
        # Invalidate in-flight thumbnail / header / scan jobs for the previous folder.
        self._thumb_gen += 1
        self._scan_gen += 1
        gen = self._scan_gen
        self._table.setRowCount(0)
        self._grid.clear()
        self._selected_dir = directory
        self._selected_name = ""
        self._selected_frame_path = ""
        self._seq_data = []
        self._ok_btn.setEnabled(False)
        self._meta_gen += 1
        self._meta_text.clear()
        self._scanning = True
        self._status.setText(f"Reading {Path(directory).name or directory}\u2026")
        self._jobs.run(
            lambda d=directory: list_image_sequences(d),
            lambda result, g=gen, d=directory: self._on_scan_listed(g, d, result),
        )

    def _on_scan_listed(self, gen: int, directory: str, result: object) -> None:
        if gen != self._scan_gen:
            return  # the user moved on to another folder
        self._scanning = False
        accept_after = bool(getattr(self, "_accept_after_scan", False))
        self._accept_after_scan = False
        if isinstance(result, BaseException):
            self._drop_pending_preview()
            self._status.setText(f"Error: {result}")
            if self._view_seg.currentData() == _SEQ_BROWSER_VIEW_PREVIEW:
                self._stop_preview_playback()
            return
        seqs = list(result) if isinstance(result, list) else []

        if not seqs:
            self._drop_pending_preview()
            self._status.setText(
                f"No image sequences in this folder ({', '.join(sorted(IMAGE_SEQUENCE_EXTS))})."
            )
            if self._view_seg.currentData() == _SEQ_BROWSER_VIEW_PREVIEW:
                self._stop_preview_playback()
            return

        self._seq_data = seqs
        self._table.setUpdatesEnabled(False)
        self._grid.setUpdatesEnabled(False)
        try:
            self._fill_rows(seqs)
        finally:
            self._table.setUpdatesEnabled(True)
            self._grid.setUpdatesEnabled(True)

        select_row = -1
        if self._auto_select_frame:
            want = os.path.normpath(self._auto_select_frame)
            self._auto_select_frame = ""
            for row, s in enumerate(seqs):
                if os.path.normpath(str(s.get("first_frame") or "")) == want:
                    select_row = row
                    break
        if select_row < 0 and self._auto_select_name:
            for row, s in enumerate(seqs):
                if s.get("name") == self._auto_select_name:
                    select_row = row
                    break
        if select_row < 0 and seqs:
            # Always pick a row so Open is enabled without an extra click / Metadata dance.
            # Preview mode always uses the first sequence (VFX: one seq per folder).
            select_row = 0
        if select_row >= 0:
            self._apply_selection(select_row)
            self._center_on_row(select_row)

        n = len(seqs)
        if n > 1:
            self._status.setText(f"{n} sequence(s) found — Preview uses the first")
        else:
            self._status.setText(f"{n} sequence(s) found")

        self._probe_headers(gen, seqs)

        if accept_after and self._ok_btn.isEnabled():
            QTimer.singleShot(0, self.accept)
            return
        was_pending = self._pending_preview
        self._pending_preview = False
        mode = self._view_seg.currentData()
        if was_pending and mode == _SEQ_BROWSER_VIEW_PREVIEW:
            self._on_view_changed(0)  # switches the stack and loads the player
            return
        if mode == _SEQ_BROWSER_VIEW_GRID:
            self._queue_thumbnails()
        elif mode == _SEQ_BROWSER_VIEW_PREVIEW:
            self._load_preview_sequence()

    def _fill_rows(self, seqs: list[dict]) -> None:
        self._table.setRowCount(len(seqs))
        center = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        for row, s in enumerate(seqs):
            display_name = s.get("pattern", s["name"])
            name_item = QTableWidgetItem(display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, s["name"])

            frames_item = QTableWidgetItem(str(s["frames"]))
            frames_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            range_item = QTableWidgetItem(s["range"])
            range_item.setTextAlignment(center)

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, frames_item)
            self._table.setItem(row, 2, range_item)
            for col, key in (
                (3, "resolution"),
                (4, "pixel_type"),
                (5, "compression"),
                (6, "colorspace"),
            ):
                item = QTableWidgetItem(str(s.get(key, "") or ""))
                item.setTextAlignment(center)
                self._table.setItem(row, col, item)

            # Grid shell immediately (placeholder icon); thumbnails fill async.
            gitem = QListWidgetItem(self._placeholder_icon, self._grid_caption(s))
            gitem.setData(Qt.ItemDataRole.UserRole, row)
            gitem.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            gitem.setSizeHint(QSize(_SEQ_THUMB_ICON.width() + 24, _SEQ_THUMB_ICON.height() + 48))
            self._grid.addItem(gitem)

    @staticmethod
    def _grid_caption(s: dict) -> str:
        caption = f"{s.get('pattern', s['name'])}\n{s['frames']}f"
        res = s.get("resolution") or ""
        if res:
            caption += f"  {res}"
        return caption

    def _probe_headers(self, gen: int, seqs: list[dict]) -> None:
        """Fill resolution / type / compression / colour space off the GUI thread."""
        todo = []
        for row, s in enumerate(seqs):
            if s.get("probe"):
                path = str(s.get("first_frame") or "")
                todo.append((row, path, lambda p=path: probe_sequence_header(p)))
        self._run_row_probes(gen, todo, self._on_header_probed)

    def _on_header_probed(self, gen: int, value: object) -> None:
        if gen != self._scan_gen or not isinstance(value, tuple):
            return
        row, path, info = value
        if not (0 <= row < len(self._seq_data)):
            return
        s = self._seq_data[row]
        if str(s.get("first_frame") or "") != path:
            return
        s.update(info)
        s["probe"] = ""
        for col, key in (
            (3, "resolution"),
            (4, "pixel_type"),
            (5, "compression"),
            (6, "colorspace"),
        ):
            item = self._table.item(row, col)
            if item is not None:
                item.setText(str(info.get(key, "") or ""))
        gitem = self._grid.item(row)
        if gitem is not None:
            gitem.setText(self._grid_caption(s))

    def _queue_thumbnails(self) -> None:
        """Dispatch background thumbnail jobs for the current folder (grid only)."""
        if not self._seq_data:
            return
        gen = self._thumb_gen
        for row, s in enumerate(self._seq_data):
            path = str(s.get("first_frame") or "")
            if not path:
                path = self._first_frame_for_sequence(
                    str(s.get("name") or ""),
                    str(s.get("path") or self._selected_dir),
                )
            if not path:
                continue
            cached = self._thumb_cache.get(path)
            if cached is not None:
                self._set_grid_icon(row, cached)
                continue
            self._thumb_pool.start(_ThumbJob(gen, row, path, self._thumb_signals))

    @Slot(int, int, object)
    def _on_thumb_ready(self, gen: int, row: int, qimg: object) -> None:
        if gen != self._thumb_gen:
            return
        if row < 0 or row >= self._grid.count():
            return
        if not isinstance(qimg, QImage) or qimg.isNull():
            return
        pm = QPixmap.fromImage(qimg)
        if pm.isNull():
            return
        # Letterbox into the fixed icon slot for a uniform grid.
        canvas = _letterbox_thumb(pm, QColor(28, 28, 32))

        path = ""
        if 0 <= row < len(self._seq_data):
            path = str(self._seq_data[row].get("first_frame") or "")
        if path:
            self._thumb_cache[path] = canvas
            # Bound cache so long browse sessions do not grow forever.
            if len(self._thumb_cache) > 256:
                # Drop arbitrary oldest-ish entries (dict order = insertion).
                for key in list(self._thumb_cache.keys())[:64]:
                    self._thumb_cache.pop(key, None)

        self._set_grid_icon(row, canvas)

    def _set_grid_icon(self, row: int, pixmap: QPixmap) -> None:
        item = self._grid.item(row)
        if item is not None:
            item.setIcon(QIcon(pixmap))

    def _first_frame_for_sequence(self, name: str, directory: str, *, cached: str = "") -> str:
        """Resolve the first frame path for sequence *name* in *directory*.

        The background listing always records the first frame; trust it rather
        than stat it on the GUI thread. The folder re-scan is a last resort.
        """
        if cached:
            return cached
        try:
            seqs = fileseq.findSequencesOnDisk(directory)
        except OSError:
            return ""
        for sq in seqs:
            if (
                sq.basename().rstrip("._") == name
                and is_image_sequence_ext(sq.extension())
                and sq.frameSet()
            ):
                return str(sq.frame(sorted(sq.frameSet())[0]))
        return ""

    def _apply_selection(self, row: int) -> None:
        """Commit sequence index *row* and keep list/grid selection in sync."""
        if row < 0 or row >= len(self._seq_data):
            self._selected_name = ""
            self._selected_frame_path = ""
            self._ok_btn.setEnabled(False)
            return
        s = self._seq_data[row]
        self._selected_name = str(s.get("name") or "")
        self._selected_dir = str(s.get("path") or self._selected_dir)
        self._selected_frame_path = self._first_frame_for_sequence(
            self._selected_name,
            self._selected_dir,
            cached=str(s.get("first_frame") or ""),
        )
        can_open = bool(self._selected_name and self._selected_dir)
        self._ok_btn.setEnabled(can_open)

        self._syncing_selection = True
        try:
            self._table.selectRow(row)
            if 0 <= row < self._grid.count():
                self._grid.setCurrentRow(row)
        finally:
            self._syncing_selection = False

        if self._meta_panel.isVisible():
            self._show_metadata(row)

    def _select_row(self, row: int) -> None:
        self._apply_selection(row)

    def _copy_path_targets(self) -> tuple[str, str]:
        # File = first frame of the selected sequence (what Open commits).
        return self._selected_frame_path, self._selected_dir

    def _on_table_selection(self) -> None:
        if self._syncing_selection:
            return
        rows = self._table.selectionModel().selectedRows()
        if rows:
            self._apply_selection(rows[0].row())
        else:
            self._selected_name = ""
            self._selected_frame_path = ""
            self._ok_btn.setEnabled(False)

    def _on_grid_selection(self) -> None:
        if self._syncing_selection:
            return
        item = self._grid.currentItem()
        if item is None:
            self._selected_name = ""
            self._selected_frame_path = ""
            self._ok_btn.setEnabled(False)
            return
        row = self._grid_item_row(item)
        self._apply_selection(row)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._seq_search_edit:
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                return self._on_seq_search_key(event)
            return False
        # Paste into Folder path: navigate + select + preview after text updates.
        if obj is self._path_edit and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent) and event.matches(QKeySequence.StandardKey.Paste):
                QTimer.singleShot(0, self._on_path_entered)
                return False  # let the paste apply, then navigate
        if event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent) and not event.isAutoRepeat():
                # Don't steal Space while typing in the path field.
                if obj is self._path_edit:
                    return super().eventFilter(obj, event)
                if event.key() == Qt.Key.Key_Space:
                    # Toggle Preview segment
                    if self._view_seg.currentData() == _SEQ_BROWSER_VIEW_PREVIEW:
                        QTimer.singleShot(
                            0,
                            lambda: self._view_seg.setCurrentData(self._last_browse_mode),
                        )
                    else:
                        QTimer.singleShot(
                            0,
                            lambda: self._view_seg.setCurrentData(_SEQ_BROWSER_VIEW_PREVIEW),
                        )
                    return True
                if (
                    event.key() == Qt.Key.Key_Escape
                    and self._view_seg.currentData() == _SEQ_BROWSER_VIEW_PREVIEW
                ):
                    self._view_seg.setCurrentData(self._last_browse_mode)
                    return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if (
            event.key() == Qt.Key.Key_Escape
            and self._view_seg.currentData() == _SEQ_BROWSER_VIEW_PREVIEW
        ):
            self._view_seg.setCurrentData(self._last_browse_mode)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            if self._view_seg.currentData() == _SEQ_BROWSER_VIEW_PREVIEW:
                self._view_seg.setCurrentData(self._last_browse_mode)
            else:
                self._view_seg.setCurrentData(_SEQ_BROWSER_VIEW_PREVIEW)
            event.accept()
            return
        super().keyPressEvent(event)

    def _load_preview_sequence(self) -> None:
        """Load the first sequence in the current folder into the player.

        VFX folders usually hold one sequence; if several are present, the first
        (EXR-preferred sort from :func:`scan_exr_sequences`) is used.
        """
        self._previewing = True
        self._view_stack.setCurrentIndex(2)
        if not self._seq_data:
            self._status.setText("No sequences to preview in this folder")
            self._stop_preview_playback(clear_only=True)
            return

        # Preview the current selection (table/grid), not always sorted[0].
        row = 0
        try:
            if self._view_stack.currentIndex() == 1 and self._grid.currentRow() >= 0:
                item = self._grid.currentItem()
                if item is not None:
                    row = self._grid_item_row(item)
            else:
                rows = self._table.selectionModel().selectedRows()
                if rows:
                    row = rows[0].row()
                elif self._table.currentRow() >= 0:
                    row = self._table.currentRow()
        except Exception:
            row = 0
        row = max(0, min(row, len(self._seq_data) - 1))
        self._apply_selection(row)
        s = self._seq_data[row]
        path = str(s.get("first_frame") or "")
        if not path:
            path = self._first_frame_for_sequence(
                str(s.get("name") or ""),
                str(s.get("path") or self._selected_dir),
            )
        if not path:
            self._status.setText("Could not resolve first frame for preview")
            return

        player = self._player
        if (
            player is not None
            and self._in_out_media
            and os.path.normpath(path) == os.path.normpath(self._in_out_media)
            and player.restart_prefetch()
        ):
            # Same sequence as last time (Space / Esc round trip): keep the
            # cache and in/out instead of loading it again.
            player.setFocus(Qt.FocusReason.OtherFocusReason)
            self._status.setText(f"Preview · {s.get('pattern') or s.get('name')}")
            return

        ctx = self._preview_ctx
        label = str(s.get("pattern") or s.get("name") or Path(path).name)
        self._status.setText(f"Loading preview \u00b7 {label}\u2026")
        # Listing the frames and probing colour attributes touch the disk:
        # do it on a worker, then hand the result to the player.
        self._preview_gen += 1
        gen = self._preview_gen
        cfg, src = ctx.ocio_cfg, ctx.src_colorspace or ""
        self._jobs.run(
            lambda p=path: SequencePlayer.prepare_sequence(p, ocio_cfg=cfg, src_colorspace=src),
            lambda result, g=gen, p=path, lb=label: self._on_preview_prepared(g, p, lb, result),
        )

    def _on_preview_prepared(self, gen: int, path: str, label: str, result: object) -> None:
        if gen != self._preview_gen or not self._previewing:
            return
        ctx = self._preview_ctx
        fps = float(ctx.fps) if ctx.fps and ctx.fps > 0 else 24.0
        self._in_out_media = ""
        if isinstance(result, BaseException):
            if isinstance(result, OptionalSdkError):
                log.debug("Preview skipped for %s: %s", path, result)
                self._status.setText(str(result))
                return
            log.warning("Preview load failed for %s: %s", path, result)
            self._status.setText(f"Preview failed: {result}")
            return

        try:
            player = self._ensure_player()
        except Exception as e:
            log.exception("Failed to create sequence player for browser preview")
            self._status.setText(f"Preview unavailable: {e}")
            return

        try:
            ok = player.load_sequence(
                path,
                fps=fps,
                ocio_cfg=ctx.ocio_cfg,
                src_colorspace=ctx.src_colorspace or "",
                prepared=result if isinstance(result, dict) else None,
            )
        except Exception as e:
            log.exception("Preview load failed for %s", path)
            self._status.setText(f"Preview failed: {e}")
            return

        self._in_out_media = path if ok else ""
        n = len(self._seq_data)
        if ok:
            extra = f" · {n} sequences (using first)" if n > 1 else ""
            self._status.setText(f"Preview · {label}{extra}")
        else:
            self._status.setText(f"No frames found for {label}")
        self.setWindowTitle(f"{self._WINDOW_TITLE} — {label}")
        player.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, player.fit_in_view)

    def _on_table_double_clicked(self, row: int, _col: int) -> None:
        self._apply_selection(row)
        if self._ok_btn.isEnabled():
            self.accept()

    def _on_grid_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        row = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        self._apply_selection(row)
        if self._ok_btn.isEnabled():
            self.accept()

    def accept(self) -> None:
        # Re-sync selection when browsing; Preview already selected first seq.
        if self._view_seg.currentData() != _SEQ_BROWSER_VIEW_PREVIEW:
            if self._view_stack.currentIndex() == 1:
                item = self._grid.currentItem()
                if item is not None:
                    row = int(item.data(Qt.ItemDataRole.UserRole) or 0)
                    self._apply_selection(row)
            else:
                rows = self._table.selectionModel().selectedRows()
                if rows:
                    self._apply_selection(rows[0].row())
        if not self._selected_name:
            return
        self._finish_accept()

    def _teardown(self) -> None:
        self._shutdown_browser_workers()

    def _in_out_matches_selection(self) -> bool:
        return os.path.normpath(self._in_out_media) == os.path.normpath(
            self._selected_frame_path or ""
        )

    def _shutdown_browser_workers(self) -> None:
        """Stop player + invalidate thumbs without blocking the GUI thread.

        Prefetch uses ``wait=False``. The player stays parented to this dialog
        so Qt destroys it with the dialog tree — we must **not**
        ``setParent(None)`` + ``deleteLater()`` while dropping the last Python
        ref (that double-frees the C++ QObject and SIGSEGVs in
        ``QObject::~QObject``).
        """
        self._previewing = False
        # Invalidate in-flight thumb / scan / probe jobs.
        self._shutdown_workers()
        self._shutdown_seq_index()
        player = self._player
        # Keep self._player set until after shutdown so slots still resolve if a
        # queued event races us; then clear the Python ref only (parent still
        # owns the C++ object).
        if player is not None:
            self._shutdown_player_widget(player)
        self._player = None

    def _metadata_row(self) -> int:
        if self._view_stack.currentIndex() == 1:
            item = self._grid.currentItem()
            if item is not None:
                return self._grid_item_row(item)
        rows = self._table.selectionModel().selectedRows()
        if rows:
            return rows[0].row()
        return -1

    def _show_metadata(self, row: int) -> None:
        """Fill the Metadata pane for *row*; the header is read on a worker thread."""
        self._meta_gen += 1
        gen = self._meta_gen
        if row < 0 or row >= len(self._seq_data):
            self._meta_text.setPlainText("")
            return
        s = self._seq_data[row]
        first_path = str(s.get("first_frame") or "")
        if not first_path:
            self._meta_text.setPlainText("Could not locate first frame.")
            return
        if not s.get("probe") and not any(
            s.get(k) for k in ("resolution", "pixel_type", "compression", "colorspace")
        ):
            # Listing found it online-only: never open (that downloads it).
            self._meta_text.setPlainText(
                "This file is not fully local (cloud placeholder / online-only).\n"
                "Download it in Dropbox, iCloud, or OneDrive before Metadata or Preview.\n"
                f"Path: {first_path}"
            )
            return
        self._meta_text.setPlainText(f"File: {Path(first_path).name}\n\nReading metadata\u2026")

        def job(p: str = first_path) -> object:
            if not can_probe_media(p):
                return None
            return probe_exr_metadata(p)

        self._jobs.run(job, lambda meta, g=gen, p=first_path: self._on_metadata(g, p, meta))

    # -- fuzzy sequence search ---------------------------------------------

    def _shutdown_seq_index(self) -> None:
        """Stop + join the index thread and discard the in-memory index."""
        self._search_debounce.stop()
        self._search_gen += 1
        self._seq_index.stop()

    def _on_volumes_changed_index(self) -> None:
        """Root set changed (mount / unmount): rebuild from the current folder."""
        self._seq_index.clear()
        if self._selected_dir:
            self._seq_index.ensure_indexed(self._selected_dir)
        if self._seq_search_edit.text().strip():
            self._search_debounce.start()

    def _on_seq_search_text(self, text: str) -> None:
        if not text.strip():
            self._end_seq_search()
            return
        if self._view_stack.currentIndex() != 3:
            if self._previewing:
                self._stop_preview_playback()
            self._view_stack.setCurrentIndex(3)
        self._search_debounce.start()

    def _run_seq_search(self) -> None:
        self._search_debounce.stop()
        query = self._seq_search_edit.text().strip()
        if not query:
            return
        self._search_gen += 1
        if self._search_results.count() == 0:
            self._show_search_empty("Searching\u2026")
        self._seq_index.search(query, self._search_gen)

    @Slot(int, bool)
    def _on_seq_index_changed(self, count: int, busy: bool) -> None:
        self._index_count = count
        self._index_busy = busy
        # Refresh visible results as the index grows (debounced like typing).
        if self._view_stack.currentIndex() == 3 and self._seq_search_edit.text().strip():
            self._search_debounce.start()

    @Slot(int, str, object, int, bool)
    def _on_seq_search_ready(
        self, gen: int, query: str, hits: object, count: int, busy: bool
    ) -> None:
        if gen != self._search_gen or query != self._seq_search_edit.text().strip():
            return  # stale: the query changed while this one was scored
        self._index_count = count
        self._index_busy = busy
        results = [h for h in hits if isinstance(h, SequenceHit)] if isinstance(hits, list) else []
        prev = self._search_results.currentItem()
        prev_hit = prev.data(Qt.ItemDataRole.UserRole) if prev is not None else None
        prev_key = prev_hit.first_frame if isinstance(prev_hit, SequenceHit) else ""
        indexing = " (still indexing\u2026)" if busy else ""
        if not results:
            if count == 0:
                msg = (
                    "Indexing sequences\u2026"
                    if busy
                    else "No image sequences found in the folders opened in this browser."
                )
            else:
                msg = f"No sequences match \u201c{query}\u201d.{indexing}"
            self._show_search_empty(msg)
            self._search_info.setText(f"{count} sequence(s) indexed{indexing}")
            return
        self._search_results.clear()
        select_row = 0
        for row, hit in enumerate(results):
            item = QListWidgetItem(
                f"{hit.pattern}   \u00b7  {hit.frame_count}f  {hit.frame_range}\n{hit.parent}"
            )
            item.setData(Qt.ItemDataRole.UserRole, hit)
            item.setToolTip(f"{hit.first_frame}\nScore {hit.score:.0f}")
            self._search_results.addItem(item)
            if prev_key and hit.first_frame == prev_key:
                select_row = row
        self._search_results.setVisible(True)
        self._search_empty.setVisible(False)
        self._search_results.setCurrentRow(select_row)
        self._search_info.setText(
            f"{len(results)} match(es) \u00b7 {count} sequence(s) indexed{indexing}"
        )

    def _show_search_empty(self, text: str) -> None:
        self._search_results.clear()
        self._search_results.setVisible(False)
        self._search_empty.setText(text)
        self._search_empty.setVisible(True)

    def _on_seq_search_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._seq_search_edit.clear()  # textChanged ends the search
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Never let Enter reach the dialog's default (Open) button.
            if self._search_debounce.isActive() or self._search_results.count() == 0:
                self._run_seq_search()
            else:
                item = self._search_results.currentItem() or self._search_results.item(0)
                if item is not None:
                    self._on_search_item_activated(item)
            return True
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._search_results.count():
            step = 1 if key == Qt.Key.Key_Down else -1
            row = self._search_results.currentRow() + step
            row = max(0, min(row, self._search_results.count() - 1))
            self._search_results.setCurrentRow(row)
            return True
        return False

    def _on_search_item_activated(self, item: QListWidgetItem) -> None:
        hit = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(hit, SequenceHit):
            self._jump_to_search_hit(hit)

    def _jump_to_search_hit(self, hit: SequenceHit) -> None:
        """Navigate the tree to *hit* and select it via the normal scan path."""
        self._end_seq_search(reload_preview=False)
        self._auto_select_name = hit.stem
        self._auto_select_frame = hit.first_frame
        self._navigate_to(hit.parent)

    def _end_seq_search(self, *, restore_view: bool = True, reload_preview: bool = True) -> None:
        """Leave search results and show the List / Grid / Preview page again."""
        self._search_debounce.stop()
        self._search_gen += 1  # drop in-flight results
        if self._seq_search_edit.text():
            self._seq_search_edit.blockSignals(True)
            self._seq_search_edit.clear()
            self._seq_search_edit.blockSignals(False)
        self._search_results.clear()
        if not restore_view or self._view_stack.currentIndex() != 3:
            return
        mode = self._view_seg.currentData()
        if mode == _SEQ_BROWSER_VIEW_PREVIEW:
            self._view_stack.setCurrentIndex(2)
            if reload_preview:
                self._load_preview_sequence()
        elif mode == _SEQ_BROWSER_VIEW_GRID:
            self._view_stack.setCurrentIndex(1)
        else:
            self._view_stack.setCurrentIndex(0)
