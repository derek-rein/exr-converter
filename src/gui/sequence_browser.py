from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import fileseq
from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import (
    QAction,
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
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import IMAGE_SEQUENCE_EXTS, is_image_sequence_ext
from ..core.local_fs import can_probe_media, path_is_dir, path_is_file
from ..core.sequence import (
    list_image_sequences,
    looks_like_sequence_pattern,
    probe_exr_metadata,
    probe_sequence_header,
    sequence_pattern_stem,
)
from .browser_chrome import (
    _SEQ_BROWSER_VIEW_GRID,
    _SEQ_BROWSER_VIEW_LIST,
    _SEQ_BROWSER_VIEW_PREVIEW,
    _SEQ_THUMB_ICON,
    _add_copy_path_actions,
    _BrowserJobs,
    _configure_path_line_edit,
    _ElidingLabel,
    _PlacesSidebar,
    _SearchableTree,
    _SearchField,
    _setup_dir_tree,
    _ThumbJob,
    _ThumbSignals,
    _tree_click_toggle_expand,
    _wire_volume_refresh,
)
from .browser_path import clean_path_string, resolve_sequence_browser_path
from .browser_state import (
    SEQ_BROWSER_KEYS,
    VIEW_GRID,
    VIEW_LIST,
    VIEW_PREVIEW,
    BrowserPreviewContext,
    browser_qsettings,
    coerce_view_mode,
    collect_expanded_dirs,
    dirs_equal,
    expand_path_chain,
    load_shared_geometry,
    normalize_dir,
    parse_int_list,
    parse_str_list,
    restore_tree_expanded,
    save_shared_geometry,
    set_tree_vscroll,
    settings_bool,
    tree_vscroll_value,
)
from .browser_tree import FILE_MODE_SEQUENCES, KIND_SEQUENCE, BrowserTreeModel
from .player.sequence_player import SequencePlayer
from .segmented_control import SegmentedControl
from .sequence_search import SEARCH_DEBOUNCE_MS, SequenceHit, SequenceIndex
from .style import STATUS_DIM

log = logging.getLogger(__name__)


def _same_dir(a: str, b: str) -> bool:
    """Path equality without touching the disk (no resolve / stat on the GUI thread)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


# ---------------------------------------------------------------------------
# Image sequence browser dialog (list / grid + metadata inspector)
# ---------------------------------------------------------------------------


class SequenceBrowserDialog(QDialog):
    """Directory browser + image sequence list/grid, with in-dialog playback.

    Preview mode replaces the browse body with :class:`SequencePlayer` (Space /
    Preview / context menu). Escape or **Back** returns to browsing.
    """

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
        self.setWindowTitle("Browse Image Sequences")
        self.resize(1120, 560)
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
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Folder:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Navigate in the tree or paste a path here")
        _configure_path_line_edit(self._path_edit)
        path_row.addWidget(self._path_edit, 1)

        self._view_seg = SegmentedControl(
            [
                ("List", _SEQ_BROWSER_VIEW_LIST),
                ("Grid", _SEQ_BROWSER_VIEW_GRID),
                ("Preview", _SEQ_BROWSER_VIEW_PREVIEW),
            ],
            parent=self,
        )
        self._view_seg.setSegmentToolTip(0, "List view (table)")
        self._view_seg.setSegmentToolTip(1, "Grid view with thumbnails")
        self._view_seg.setSegmentToolTip(
            2, "Playback of the first sequence in this folder (VFX: usually one per folder)"
        )
        self._view_seg.setToolTip("Sequence browser layout")
        path_row.addWidget(self._view_seg)

        self._inspect_cb = QCheckBox("Metadata")
        self._inspect_cb.setToolTip("Show image metadata for the selected / previewed sequence")
        path_row.addWidget(self._inspect_cb)
        layout.addLayout(path_row)

        # -- left: places sidebar + dir tree (stays visible in preview) --
        self._places = _PlacesSidebar()
        self._places.navigate_requested.connect(self._navigate_to)

        # Multi-root: each mounted volume is a top-level row (macOS /Volumes is
        # hidden from QFileSystemModel under ``/``; Windows other drives too).
        # Folders + image sequences (one row per sequence), listed in the background.
        self._fs_model = BrowserTreeModel(self, file_mode=FILE_MODE_SEQUENCES)
        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setHeaderHidden(True)
        # Every row is one line: lets the view skip per-row size queries.
        self._tree.setUniformRowHeights(True)
        self._tree.setMinimumWidth(200)
        tree_header = self._tree.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._volume_timer = _wire_volume_refresh(self._places, self._fs_model, self)

        self._searchable_tree = _SearchableTree(self._tree, dirs_only=True)
        self._searchable_tree.result_navigated.connect(self._navigate_to)
        _setup_dir_tree(self._tree, self._fs_model, self._places)

        left_panel = QWidget()
        left_layout = QHBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self._places)
        left_layout.addWidget(self._searchable_tree, 1)

        # -- center: list table OR thumbnail grid --
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)

        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumWidth(280)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        th = self._table.horizontalHeader()
        # Name (col 0) is Stretch — it owns leftover width and is the flexible
        # column when the container shrinks. Other columns stay Interactive so
        # the user can size them; they keep width until the Name floor is hit.
        th.setSectionsMovable(False)
        th.setStretchLastSection(False)
        th.setMinimumSectionSize(48)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self._COLUMNS)):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        # Sensible first-open defaults for non-name columns (QSettings can override).
        th.resizeSection(1, 64)
        th.resizeSection(2, 100)
        th.resizeSection(3, 100)
        th.resizeSection(4, 72)
        th.resizeSection(5, 100)
        th.resizeSection(6, 120)
        # Keep Name readable under squeeze (global min is 48; Name wants more).
        self._name_col_min = 140
        th.sectionResized.connect(self._on_table_section_resized)
        # Debounced layout save so column tweaks persist without waiting for close.
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(400)
        self._layout_save_timer.timeout.connect(self._save_browser_layout)
        th.sectionResized.connect(lambda *_: self._schedule_layout_save())

        self._grid = QListWidget()
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setIconSize(_SEQ_THUMB_ICON)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setUniformItemSizes(True)
        self._grid.setSpacing(10)
        self._grid.setWordWrap(True)
        self._grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._grid.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Grid is the primary content: claim all extra space when the dialog grows.
        self._grid.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid.setMinimumWidth(360)

        # List | Grid | Preview share one stack (Inspect stays beside them).
        self._preview_page = QWidget()
        self._preview_host = QVBoxLayout(self._preview_page)
        self._preview_host.setContentsMargins(0, 0, 0, 0)
        self._preview_host.setSpacing(0)

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
        self._seq_search_edit.setPlaceholderText(
            "\U0001f50d  Find sequence (fuzzy, e.g. shot_010_comp)\u2026"
        )
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
        self._search_empty.setStyleSheet(STATUS_DIM)
        search_layout.addWidget(self._search_empty, 1)
        self._search_info = QLabel()
        self._search_info.setStyleSheet(STATUS_DIM)
        search_layout.addWidget(self._search_info)
        self._view_stack.addWidget(self._search_page)  # 3 = search results
        center_layout.addWidget(self._view_stack, 1)

        # -- Metadata panel (available in list, grid, and preview) --
        self._meta_panel = QWidget()
        self._meta_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        meta_layout = QVBoxLayout(self._meta_panel)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        meta_layout.addWidget(QLabel("<b>Image Metadata</b>"))
        self._meta_text = QPlainTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setMinimumWidth(160)
        self._meta_text.setObjectName("metaPane")
        meta_layout.addWidget(self._meta_text, 1)
        self._meta_panel.setVisible(False)
        self._meta_panel.setMinimumWidth(160)

        # content: list/grid/player + optional metadata
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(center)
        self._content_splitter.addWidget(self._meta_panel)
        self._content_splitter.setStretchFactor(0, 1)
        self._content_splitter.setStretchFactor(1, 0)
        self._content_splitter.setCollapsible(0, False)
        self._content_splitter.setCollapsible(1, False)

        # outer: folder tree | content
        left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        left_panel.setMinimumWidth(160)
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.addWidget(left_panel)
        self._outer_splitter.addWidget(self._content_splitter)
        self._outer_splitter.setStretchFactor(0, 0)
        self._outer_splitter.setStretchFactor(1, 1)
        self._outer_splitter.setCollapsible(0, False)
        self._outer_splitter.setCollapsible(1, False)
        layout.addWidget(self._outer_splitter, 1)

        # Footer: status + Open/Cancel (mode is the List|Grid|Preview segment)
        bottom_row = QHBoxLayout()
        self._status = _ElidingLabel()
        self._status.setStyleSheet(STATUS_DIM)
        bottom_row.addWidget(self._status, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._ok_btn.setEnabled(False)
        # Fixed-size button row so long status cannot push buttons off-screen.
        buttons.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        bottom_row.addWidget(buttons)
        layout.addLayout(bottom_row)

        # Default proportions (overridden by QSettings when present).
        self._outer_splitter.setSizes([240, 880])
        self._outer_splitter.splitterMoved.connect(lambda *_: self._schedule_layout_save())
        self._content_splitter.splitterMoved.connect(lambda *_: self._schedule_layout_save())
        self._tree.clicked.connect(self._on_tree_clicked)
        self._tree.doubleClicked.connect(self._on_tree_double_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellDoubleClicked.connect(self._on_table_double_clicked)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._grid.itemSelectionChanged.connect(self._on_grid_selection)
        self._grid.itemDoubleClicked.connect(self._on_grid_double_clicked)
        self._grid.customContextMenuRequested.connect(self._on_grid_context_menu)
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._path_edit.installEventFilter(self)
        # Space → preview (table/grid eat key presses; filter their viewports).
        self._table.installEventFilter(self)
        self._table.viewport().installEventFilter(self)
        self._grid.installEventFilter(self)
        self._grid.viewport().installEventFilter(self)
        self.installEventFilter(self)

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
        saved_view = coerce_view_mode(settings.value(keys.view, VIEW_LIST))
        # Prefer list/grid for the initial stack; Preview applies after scan.
        self._pending_preview = saved_view == VIEW_PREVIEW
        last_browse = coerce_view_mode(
            settings.value(keys.last_browse, saved_view), allow_preview=False
        )
        if saved_view == VIEW_GRID:
            last_browse = VIEW_GRID
        elif saved_view == VIEW_LIST:
            last_browse = VIEW_LIST
        self._last_browse_mode = last_browse if last_browse in (VIEW_LIST, VIEW_GRID) else VIEW_LIST
        if self._last_browse_mode == VIEW_GRID:
            self._view_seg.setCurrentData(VIEW_GRID)
            self._view_stack.setCurrentIndex(1)
        else:
            self._view_seg.setCurrentData(VIEW_LIST)
            self._view_stack.setCurrentIndex(0)
        self._view_seg.currentIndexChanged.connect(self._on_view_changed)

        # Metadata pane from last session (default on for sequences).
        inspect_on = settings_bool(settings, keys.inspect, True)
        self._inspect_cb.blockSignals(True)
        self._inspect_cb.setChecked(inspect_on)
        self._inspect_cb.blockSignals(False)
        self._toggle_inspect(inspect_on)
        self._inspect_cb.toggled.connect(self._toggle_inspect)

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

        if self._pending_preview and self._scanning:
            # Saved Preview mode: show the segment now; the player loads when
            # the background scan has found something to play.
            self._view_seg.blockSignals(True)
            self._view_seg.setCurrentData(_SEQ_BROWSER_VIEW_PREVIEW)
            self._view_seg.blockSignals(False)
        else:
            # Nothing to scan: a saved Preview mode has nothing to play.
            self._drop_pending_preview()

    def _schedule_layout_save(self) -> None:
        """Debounce layout persistence while the user drags splitters/columns."""
        timer = getattr(self, "_layout_save_timer", None)
        if timer is not None:
            timer.start()

    def _restore_browser_layout(self) -> None:
        """Restore dialog geometry (shared), splitter sizes, and list header state."""
        settings = browser_qsettings()
        keys = self._keys
        geo = load_shared_geometry(settings)
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass

        outer = parse_int_list(settings.value(keys.outer_split))
        if outer is not None and len(outer) == 2 and all(s > 0 for s in outer):
            self._outer_splitter.setSizes(outer)

        content = parse_int_list(settings.value(keys.content_split))
        if content is not None and self._meta_panel.isVisible():
            if len(content) == 2 and content[0] > 0 and content[1] >= 0:
                self._content_splitter.setSizes(content)

        th = self._table.horizontalHeader()
        # Prefer Qt native header state (widths + interactive modes).
        header_state = settings.value(keys.header_state)
        if header_state is not None:
            try:
                th.restoreState(header_state)
                # Re-assert Name stretch priority after restore (saveState can
                # re-apply Interactive on every section).
                th.setStretchLastSection(False)
                th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
                for col in range(1, self._table.columnCount()):
                    th.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            except Exception:
                header_state = None
        if header_state is None:
            # Legacy fallback: plain width list.
            widths = parse_int_list(settings.value(keys.col_widths))
            if widths is not None:
                for i, w in enumerate(widths):
                    if i == 0:
                        continue
                    if i < self._table.columnCount() and w >= 48:
                        th.resizeSection(i, w)

    def _save_browser_layout(self) -> None:
        """Persist shared geometry + per-mode layout and session state."""
        settings = browser_qsettings()
        keys = self._keys
        save_shared_geometry(self.saveGeometry(), settings)
        settings.setValue(keys.outer_split, self._outer_splitter.sizes())
        # Only store content split when Metadata is open (hidden pane is 0-width).
        if self._meta_panel.isVisible():
            settings.setValue(keys.content_split, self._content_splitter.sizes())
        th = self._table.horizontalHeader()
        settings.setValue(keys.header_state, th.saveState())
        # Also keep a simple width list for debugging / older readers.
        widths = [th.sectionSize(i) for i in range(self._table.columnCount())]
        settings.setValue(keys.col_widths, widths)
        settings.setValue(keys.inspect, self._inspect_cb.isChecked())
        mode = self._view_seg.currentData() or VIEW_LIST
        settings.setValue(keys.view, str(mode))
        settings.setValue(
            keys.last_browse,
            str(self._last_browse_mode or VIEW_LIST),
        )
        directory = self._path_edit.text().strip() or self._selected_dir
        if directory:
            settings.setValue(keys.last_dir, normalize_dir(directory) or directory)
        if self._selected_name:
            settings.setValue(keys.selected, self._selected_name)
        settings.setValue(keys.tree_expanded, collect_expanded_dirs(self._tree, self._fs_model))
        settings.setValue(keys.tree_vscroll, tree_vscroll_value(self._tree))
        settings.sync()

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

    def _navigate_to(self, directory: str, *, restore_tree: bool = False) -> None:
        # Stay on Preview if active — scan reloads the first sequence of the new folder.
        # The tree lists missing levels in the background, then selects the folder.
        expand_path_chain(self._tree, self._fs_model, directory, select=True)
        self._fs_model.refresh(directory)
        if restore_tree:
            self._restore_tree_session(directory)
        self._path_edit.setText(directory)
        self._places.set_current_dir(directory)
        self._searchable_tree.set_search_root(directory)
        self._seq_index.ensure_indexed(directory)
        self._scan_directory(directory)

    def _restore_tree_session(self, directory: str) -> None:
        """Re-expand folders + scroll from last session (same-path reopen)."""
        settings = browser_qsettings()
        keys = self._keys
        paths = parse_str_list(settings.value(keys.tree_expanded))
        restore_tree_expanded(self._tree, self._fs_model, paths, focus_path=directory)
        vscroll = settings.value(keys.tree_vscroll)
        try:
            scroll_val = int(vscroll) if vscroll is not None else -1
        except (TypeError, ValueError):
            scroll_val = -1
        if scroll_val >= 0:
            # Once the focused folder has been listed and laid out.
            self._fs_model.reveal(
                directory,
                lambda _i, v=scroll_val: QTimer.singleShot(0, lambda: self._restore_vscroll(v)),
            )

    def _restore_vscroll(self, value: int) -> None:
        """Last session's tree scroll, but never hiding the current folder."""
        set_tree_vscroll(self._tree, value)
        cur = self._tree.currentIndex()
        if cur.isValid():
            self._tree.scrollTo(cur, QAbstractItemView.ScrollHint.EnsureVisible)

    def _on_tree_clicked(self, index) -> None:
        path = self._fs_model.filePath(index)
        if not path:
            return
        if self._fs_model.kind(index) == KIND_SEQUENCE:
            self._select_tree_sequence(index)
            return
        _tree_click_toggle_expand(self._tree, self._fs_model, index)
        self._fs_model.refresh(path)
        self._path_edit.setText(path)
        self._places.set_current_dir(path)
        self._searchable_tree.set_search_root(path)
        self._seq_index.ensure_indexed(path)
        self._scan_directory(path)

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
        if _same_dir(directory, self._selected_dir) and self._seq_data and not self._scanning:
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
        self._path_edit.setText(directory)
        self._places.set_current_dir(directory)
        self._searchable_tree.set_search_root(directory)
        self._seq_index.ensure_indexed(directory)
        self._scan_directory(directory)
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

    def _on_path_entered(self) -> None:
        """Navigate to a pasted/typed path (folder, frame file, or Nuke #### pattern)."""
        raw = clean_path_string(self._path_edit.text())
        if not raw:
            return
        # Reflect cleaned paste (file:// / quotes) back into the field.
        if raw != self._path_edit.text().strip():
            self._path_edit.setText(raw)
        self._navigate_to_path_string(raw)

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

    def _on_view_changed(self, _index: int) -> None:
        # Picking List / Grid / Preview leaves search results.
        self._end_seq_search(restore_view=False)
        mode = self._view_seg.currentData()
        settings = browser_qsettings()
        if mode == _SEQ_BROWSER_VIEW_PREVIEW:
            settings.setValue(self._keys.view, _SEQ_BROWSER_VIEW_PREVIEW)
            settings.setValue(self._keys.last_browse, str(self._last_browse_mode or VIEW_LIST))
            self._view_stack.setCurrentIndex(2)
            self._load_preview_sequence()
            return
        # Leaving preview
        if self._previewing:
            self._stop_preview_playback()
        if mode == _SEQ_BROWSER_VIEW_GRID:
            self._last_browse_mode = _SEQ_BROWSER_VIEW_GRID
            self._view_stack.setCurrentIndex(1)
            settings.setValue(self._keys.view, _SEQ_BROWSER_VIEW_GRID)
            settings.setValue(self._keys.last_browse, _SEQ_BROWSER_VIEW_GRID)
            self._queue_thumbnails()
        else:
            self._last_browse_mode = _SEQ_BROWSER_VIEW_LIST
            self._view_stack.setCurrentIndex(0)
            settings.setValue(self._keys.view, _SEQ_BROWSER_VIEW_LIST)
            settings.setValue(self._keys.last_browse, _SEQ_BROWSER_VIEW_LIST)

    def _drop_pending_preview(self) -> None:
        """A saved / pasted Preview found nothing to play: back to the browse view."""
        if not self._pending_preview:
            return
        self._pending_preview = False
        if self._view_seg.currentData() == _SEQ_BROWSER_VIEW_PREVIEW:
            self._view_seg.blockSignals(True)
            self._view_seg.setCurrentData(self._last_browse_mode)
            self._view_seg.blockSignals(False)
            self._view_stack.setCurrentIndex(
                1 if self._last_browse_mode == _SEQ_BROWSER_VIEW_GRID else 0
            )

    def is_scanning(self) -> bool:
        """True while the current folder is still being read in the background."""
        return self._scanning

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
            self._table.scrollToItem(
                self._table.item(select_row, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
            if 0 <= select_row < self._grid.count():
                self._grid.scrollToItem(
                    self._grid.item(select_row),
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )

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
        todo = [
            (row, str(s.get("first_frame") or "")) for row, s in enumerate(seqs) if s.get("probe")
        ]
        if not todo:
            return
        post = self._jobs.poster(lambda v, g=gen: self._on_header_probed(g, v))

        def job() -> None:
            for row, path in todo:
                if gen != self._scan_gen:
                    return  # folder changed: stop opening files for it
                post((row, path, probe_sequence_header(path)))

        self._jobs.run(job, lambda _r: None)

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
        canvas = QPixmap(_SEQ_THUMB_ICON)
        canvas.fill(QColor(28, 28, 32))
        scaled = pm.scaled(
            _SEQ_THUMB_ICON,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter = QPainter(canvas)
        x = (_SEQ_THUMB_ICON.width() - scaled.width()) // 2
        y = (_SEQ_THUMB_ICON.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()

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
        row = int(item.data(Qt.ItemDataRole.UserRole) or self._grid.row(item))
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

    def _on_table_context_menu(self, pos: QPoint) -> None:
        index = self._table.indexAt(pos)
        if index.isValid():
            self._apply_selection(index.row())
        self._show_sequence_context_menu(self._table.viewport().mapToGlobal(pos))

    def _on_grid_context_menu(self, pos: QPoint) -> None:
        item = self._grid.itemAt(pos)
        if item is not None:
            row = int(item.data(Qt.ItemDataRole.UserRole) or self._grid.row(item))
            self._apply_selection(row)
        self._show_sequence_context_menu(self._grid.viewport().mapToGlobal(pos))

    def _show_sequence_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        preview_act = QAction("Preview", self)
        preview_act.setShortcut(Qt.Key.Key_Space)
        preview_act.triggered.connect(
            lambda: self._view_seg.setCurrentData(_SEQ_BROWSER_VIEW_PREVIEW)
        )
        menu.addAction(preview_act)
        open_act = QAction("Open", self)
        open_act.setEnabled(self._ok_btn.isEnabled())
        open_act.triggered.connect(self.accept)
        menu.addAction(open_act)
        menu.addSeparator()
        # File = first frame of the selected sequence (what Open commits).
        _add_copy_path_actions(
            menu,
            file_path=self._selected_frame_path,
            folder_path=self._selected_dir,
        )
        menu.exec(global_pos)

    def _ensure_player(self):
        """Return the embedded :class:`SequencePlayer` (created in ``__init__``)."""
        if self._player is not None:
            return self._player
        # Same GPU OCIO player as the slate editor. Must exist before the dialog
        # is shown so the first QOpenGLWidget is not added post-show (Qt 6.4+
        # native-window recreate crash). Cache budget strip stays in Preferences.
        self._player = SequencePlayer(
            settings=browser_qsettings(),
            show_cache_ui=False,
            prefer_gpu=True,
            show_in_out=True,
            parent=self._preview_page,
        )
        self._player.in_out_changed.connect(self._on_in_out_changed)
        self._preview_host.addWidget(self._player, 1)
        return self._player

    def selected_frame_range(self) -> str:
        """``"first-last"`` from the Preview in/out points, or ``""``.

        Only for the sequence being opened: in/out set on another sequence
        does not carry over.
        """
        player = self._player
        if player is None or not self._in_out_media:
            return ""
        if os.path.normpath(self._in_out_media) != os.path.normpath(
            self._selected_frame_path or ""
        ):
            return ""
        rng = player.in_out_range()
        return f"{rng[0]}-{rng[1]}" if rng else ""

    def _on_in_out_changed(self, in_point: object, out_point: object) -> None:
        player = self._player
        rng = player.in_out_range() if player is not None else None
        if rng is None:
            return
        self._status.setText(
            f"In {rng[0]} \u00b7 Out {rng[1]} ({rng[1] - rng[0] + 1} frames) \u2014 "
            "Open uses this range"
        )

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
                    row = int(item.data(Qt.ItemDataRole.UserRole) or self._grid.row(item))
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
        self.setWindowTitle(f"Browse Image Sequences — {label}")
        player.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, player.fit_in_view)

    def _stop_preview_playback(self, *, clear_only: bool = False) -> None:
        """Stop the player without changing the List/Grid/Preview segment.

        Keeps the player + GPU plane + RAM cache warm so re-entering Preview
        is instant (only stops transport and background prefetch).
        """
        self._previewing = False
        if self._player is not None:
            try:
                self._player.set_playing(False)
            except RuntimeError:
                pass
            try:
                self._player.shutdown_prefetch_only()
            except RuntimeError:
                pass
        if not clear_only:
            self.setWindowTitle("Browse Image Sequences")

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
        # Read in/out before the player is shut down.
        self._accepted_frame_range = self.selected_frame_range()
        self._shutdown_browser_workers()
        self._save_browser_layout()
        super().accept()

    def accepted_frame_range(self) -> str:
        """Frame range from the Preview in/out points at Open, or ``""``."""
        return str(getattr(self, "_accepted_frame_range", "") or "")

    def reject(self) -> None:
        # Always close the dialog. Escape already leaves Preview via keyPressEvent /
        # eventFilter; window chrome (X) and Cancel must not be trapped in Preview.
        self._shutdown_browser_workers()
        self._save_browser_layout()
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._shutdown_browser_workers()
        self._save_browser_layout()
        super().closeEvent(event)

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
        self._thumb_gen += 1
        self._scan_gen += 1
        self._jobs.shutdown()
        self._fs_model.shutdown()
        self._searchable_tree.shutdown()
        self._shutdown_seq_index()
        player = self._player
        # Keep self._player set until after shutdown so slots still resolve if a
        # queued event races us; then clear the Python ref only (parent still
        # owns the C++ object).
        if player is not None:
            try:
                player.set_playing(False)
            except RuntimeError:
                pass
            try:
                player.shutdown()
            except RuntimeError:
                pass
            try:
                player.hide()
            except RuntimeError:
                pass
        self._player = None

    def _toggle_inspect(self, checked: bool) -> None:
        self._meta_panel.setVisible(checked)
        if checked:
            # Prefer last Metadata width from QSettings; otherwise a modest default.
            total = sum(self._content_splitter.sizes())
            if total <= 0:
                total = max(self._content_splitter.width(), 640)
            meta_w = 260
            saved = parse_int_list(browser_qsettings().value(self._keys.content_split))
            if saved is not None and len(saved) == 2 and saved[1] >= 120:
                meta_w = saved[1]
            self._content_splitter.setSizes([max(total - meta_w, 400), meta_w])
            row = self._current_selected_row()
            if row >= 0:
                self._show_metadata(row)
        else:
            # Give the full content width back to the sequence view.
            total = sum(self._content_splitter.sizes()) or max(self._content_splitter.width(), 640)
            self._content_splitter.setSizes([total, 0])
        self._schedule_layout_save()

    def _current_selected_row(self) -> int:
        if self._view_stack.currentIndex() == 1:
            item = self._grid.currentItem()
            if item is not None:
                return int(item.data(Qt.ItemDataRole.UserRole) or self._grid.row(item))
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

    def _on_metadata(self, gen: int, first_path: str, meta: object) -> None:
        if gen != self._meta_gen:
            return
        if meta is None:
            self._meta_text.setPlainText(
                "This file is not fully local (cloud placeholder / online-only).\n"
                "Download it in Dropbox, iCloud, or OneDrive before Metadata or Preview.\n"
                f"Path: {first_path}"
            )
            return
        if isinstance(meta, BaseException) or not isinstance(meta, dict):
            self._meta_text.setPlainText(f"File: {Path(first_path).name}\n\nerror: {meta}")
            return
        lines = [f"File: {Path(first_path).name}", ""]
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
        self._meta_text.setPlainText("\n".join(lines))

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
