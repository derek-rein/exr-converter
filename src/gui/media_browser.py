"""Shared base for :class:`SequenceBrowserDialog` and :class:`VideoBrowserDialog`.

Both dialogs build the same chrome (folder path, List | Grid | Preview, places +
folder tree, table / grid / preview stack, Metadata pane, footer) and persist
it the same way. The base holds that shared behaviour; subclasses fill in what
differs (what a row is, how a folder is listed, probed, thumbnailed and
previewed) through the hook methods below.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPainter, QPixmap
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
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .browser_chrome import (
    _SEQ_THUMB_ICON,
    _add_copy_path_actions,
    _BrowserJobs,
    _configure_path_line_edit,
    _ElidingLabel,
    _PlacesSidebar,
    _SearchableTree,
    _setup_dir_tree,
    _tree_click_toggle_expand,
    _wire_volume_refresh,
)
from .browser_layout import (
    content_split_ok,
    frame_range_text,
    in_out_status_text,
    initial_browse_mode,
    metadata_split,
    outer_split_ok,
)
from .browser_path import clean_path_string
from .browser_state import (
    VIEW_GRID,
    VIEW_LIST,
    VIEW_PREVIEW,
    BrowserSettingsKeys,
    browser_qsettings,
    coerce_view_mode,
    collect_expanded_dirs,
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
from .browser_tree import BrowserTreeModel
from .player.sequence_player import SequencePlayer
from .segmented_control import SegmentedControl
from .style import STATUS_DIM, set_style_property


def _letterbox_thumb(pm: QPixmap, background: QColor) -> QPixmap:
    """*pm* scaled into the fixed grid icon slot, centred on *background*."""
    canvas = QPixmap(_SEQ_THUMB_ICON)
    canvas.fill(background)
    scaled = pm.scaled(
        _SEQ_THUMB_ICON,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (_SEQ_THUMB_ICON.width() - scaled.width()) // 2
    y = (_SEQ_THUMB_ICON.height() - scaled.height()) // 2
    painter = QPainter(canvas)
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return canvas


class _MediaBrowserDialog(QDialog):
    """Folder browser dialog shell shared by the sequence and video browsers.

    Subclasses set ``self._keys`` (a :class:`~.browser_state.BrowserSettingsKeys`)
    and build the widgets the methods here use (``_outer_splitter``,
    ``_content_splitter``, ``_meta_panel``, ``_table``, ``_tree``, ``_fs_model``,
    ``_inspect_cb``, ``_view_seg``, ``_path_edit``).
    """

    # Set by the subclass constructor (annotations only: no class attributes).
    _keys: BrowserSettingsKeys
    _outer_splitter: QSplitter
    _content_splitter: QSplitter
    _meta_panel: QWidget
    _table: QTableWidget
    _tree: QTreeView
    _fs_model: BrowserTreeModel
    _inspect_cb: QCheckBox
    _view_seg: SegmentedControl
    _path_edit: QLineEdit
    _last_browse_mode: str
    _meta_text: QPlainTextEdit
    _meta_gen: int
    _grid: QListWidget
    _ok_btn: QPushButton
    _status: QLabel
    _player: SequencePlayer | None
    _previewing: bool
    _in_out_media: str
    _preview_page: QWidget
    _preview_host: QVBoxLayout
    _jobs: _BrowserJobs
    _searchable_tree: _SearchableTree
    _thumb_gen: int
    _scan_gen: int
    _scanning: bool
    _pending_preview: bool
    _view_stack: QStackedWidget
    _places: _PlacesSidebar

    # -- hooks ---------------------------------------------------------------

    def _layout_last_dir(self) -> str:
        """Folder saved as the session's last directory."""
        return self._path_edit.text().strip()

    def _layout_selected(self) -> str:
        """Selection saved for a same-folder reopen (empty: keep the old value)."""
        raise NotImplementedError

    def _metadata_row(self) -> int:
        """Row whose metadata the pane shows when it opens, or -1."""
        raise NotImplementedError

    def _show_metadata(self, row: int) -> None:
        """Fill the Metadata pane for *row* (probe on a worker thread)."""
        raise NotImplementedError

    def _select_row(self, row: int) -> None:
        """Commit *row* as the selection, keeping table and grid in sync."""
        raise NotImplementedError

    def _copy_path_targets(self) -> tuple[str, str]:
        """``(file, folder)`` for Copy File Path / Copy Folder Path.

        An empty folder means the folder of the file.
        """
        raise NotImplementedError

    def _in_out_matches_selection(self) -> bool:
        """True when the media with in/out points is the one Open would return."""
        raise NotImplementedError

    def _teardown(self) -> None:
        """Stop workers and the player before the dialog closes."""
        raise NotImplementedError

    def _before_view_change(self) -> None:
        """Called first when the user picks List / Grid / Preview."""

    def _load_preview(self) -> None:
        """Load the selected (or first) row into the Preview player."""
        raise NotImplementedError

    def _queue_grid_thumbnails(self) -> None:
        """Start thumbnail jobs for the grid."""
        raise NotImplementedError

    def _index_folder(self, directory: str) -> None:
        """Called for each folder the user opens, before it is scanned."""

    def _scan_directory(self, directory: str) -> None:
        """Clear the list and read *directory* on a worker thread."""
        raise NotImplementedError

    def _on_tree_double_clicked(self, index: QModelIndex) -> None:
        raise NotImplementedError

    def _on_table_selection(self) -> None:
        raise NotImplementedError

    def _on_grid_selection(self) -> None:
        raise NotImplementedError

    def _on_grid_double_clicked(self, item: QListWidgetItem) -> None:
        raise NotImplementedError

    # Tree row kind that is a media item (not a folder).
    _TREE_ITEM_KIND = ""

    def _on_tree_item_clicked(self, index: QModelIndex) -> None:
        """A media row (``_TREE_ITEM_KIND``) was clicked in the folder tree."""
        raise NotImplementedError

    # -- layout build --------------------------------------------------------
    # Each helper builds one block of the dialog; subclasses call them in the
    # order their constructor always used, so widget creation order (and the
    # default focus chain) is unchanged.

    _COLUMNS: list[str] = []

    def _build_path_row(
        self, *, grid_tip: str, preview_tip: str, inspect_tip: str, seg_tip: str = ""
    ) -> QHBoxLayout:
        """Top bar: folder path + List | Grid | Preview + Metadata."""
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Folder:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Navigate in the tree or paste a path here")
        _configure_path_line_edit(self._path_edit)
        path_row.addWidget(self._path_edit, 1)

        self._view_seg = SegmentedControl(
            [
                ("List", VIEW_LIST),
                ("Grid", VIEW_GRID),
                ("Preview", VIEW_PREVIEW),
            ],
            parent=self,
        )
        self._view_seg.setSegmentToolTip(0, "List view (table)")
        self._view_seg.setSegmentToolTip(1, grid_tip)
        self._view_seg.setSegmentToolTip(2, preview_tip)
        if seg_tip:
            self._view_seg.setToolTip(seg_tip)
        path_row.addWidget(self._view_seg)

        self._inspect_cb = QCheckBox("Metadata")
        self._inspect_cb.setToolTip(inspect_tip)
        path_row.addWidget(self._inspect_cb)
        return path_row

    def _build_folder_panel(
        self, make_model: Callable[[], BrowserTreeModel], **search_kwargs: object
    ) -> QWidget:
        """Left side: places sidebar + folder tree with search (full height)."""
        self._places = _PlacesSidebar()
        self._places.navigate_requested.connect(self._navigate_to)

        self._fs_model = make_model()
        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setHeaderHidden(True)
        # Every row is one line: lets the view skip per-row size queries.
        self._tree.setUniformRowHeights(True)
        # Tighter than Qt's 20 px, so deep paths keep room for their names.
        self._tree.setIndentation(14)
        self._tree.setMinimumWidth(200)
        tree_header = self._tree.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._volume_timer = _wire_volume_refresh(self._places, self._fs_model, self)

        self._searchable_tree = _SearchableTree(self._tree, **search_kwargs)  # type: ignore[arg-type]
        self._searchable_tree.result_navigated.connect(self._navigate_to)
        _setup_dir_tree(self._tree, self._fs_model, self._places)

        left_panel = QWidget()
        left_layout = QHBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self._places)
        left_layout.addWidget(self._searchable_tree, 1)
        return left_panel

    def _build_table(self, *, min_width: int) -> QTableWidget:
        """List view: one row per item; Name (column 0) stretches."""
        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.setShowGrid(False)
        self._table.setObjectName("browserTable")
        self._table.setMinimumWidth(min_width)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        th = self._table.horizontalHeader()
        # Name (col 0) is Stretch — it owns leftover width and is the flexible
        # column when the container shrinks. Other columns stay Interactive so
        # the user can size them (and so long paths never force dialog width).
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self._COLUMNS)):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        return self._table

    def _build_grid(self, *, min_width: int) -> QListWidget:
        """Grid view: thumbnail icons, filled in the background."""
        self._grid = QListWidget()
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setIconSize(_SEQ_THUMB_ICON)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setUniformItemSizes(True)
        self._grid.setSpacing(10)
        self._grid.setWordWrap(True)
        self._grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # Grid is the primary content: claim all extra space when the dialog grows.
        self._grid.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid.setMinimumWidth(min_width)
        return self._grid

    def _build_preview_page(self) -> QWidget:
        """Preview page; ``_ensure_player`` adds the player to ``_preview_host``."""
        self._preview_page = QWidget()
        self._preview_host = QVBoxLayout(self._preview_page)
        self._preview_host.setContentsMargins(0, 0, 0, 0)
        self._preview_host.setSpacing(0)
        return self._preview_page

    def _build_meta_panel(self, title: str) -> QWidget:
        """Metadata pane (hidden until the Metadata box is checked)."""
        self._meta_panel = QWidget()
        meta_layout = QVBoxLayout(self._meta_panel)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        meta_layout.addWidget(QLabel(f"<b>{title}</b>"))
        self._meta_text = QPlainTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setMinimumWidth(160)
        self._meta_text.setObjectName("metaPane")
        meta_layout.addWidget(self._meta_text, 1)
        self._meta_panel.setVisible(False)
        self._meta_panel.setMinimumWidth(160)
        return self._meta_panel

    def _build_content_splitter(self, center: QWidget) -> QSplitter:
        """List / grid / preview | Metadata."""
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(center)
        self._content_splitter.addWidget(self._meta_panel)
        self._content_splitter.setStretchFactor(0, 1)
        self._content_splitter.setStretchFactor(1, 0)
        self._content_splitter.setCollapsible(0, False)
        self._content_splitter.setCollapsible(1, False)
        return self._content_splitter

    def _build_footer(self) -> QHBoxLayout:
        """Status line + Open / Cancel."""
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
        set_style_property(self._ok_btn, "variant", "primary")
        # Fixed-size button row so long status cannot push buttons off-screen.
        buttons.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        bottom_row.addWidget(buttons)
        return bottom_row

    def _build_outer_splitter(self, left_panel: QWidget, right: QWidget) -> QSplitter:
        """Folder panel (full height) | content."""
        left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        left_panel.setMinimumWidth(160)
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.addWidget(left_panel)
        self._outer_splitter.addWidget(right)
        self._outer_splitter.setStretchFactor(0, 0)
        self._outer_splitter.setStretchFactor(1, 1)
        self._outer_splitter.setCollapsible(0, False)
        self._outer_splitter.setCollapsible(1, False)
        return self._outer_splitter

    def _wire_layout_save(self) -> None:
        """Debounced layout save on column / splitter drags (no wait for close)."""
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(400)
        self._layout_save_timer.timeout.connect(self._save_browser_layout)
        th = self._table.horizontalHeader()
        th.sectionResized.connect(lambda *_: self._schedule_layout_save())
        self._outer_splitter.splitterMoved.connect(lambda *_: self._schedule_layout_save())
        self._content_splitter.splitterMoved.connect(lambda *_: self._schedule_layout_save())

    def _wire_browse_signals(self) -> None:
        """Tree / table / grid / path signals and the Space / Escape filters.

        Table double-click differs per dialog and is connected by the subclass.
        """
        self._tree.clicked.connect(self._on_tree_clicked)
        self._tree.doubleClicked.connect(self._on_tree_double_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
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

    # -- scan helpers --------------------------------------------------------

    def _center_on_row(self, row: int) -> None:
        """Scroll table and grid so *row* sits in the middle."""
        self._table.scrollToItem(
            self._table.item(row, 0),
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        if 0 <= row < self._grid.count():
            self._grid.scrollToItem(
                self._grid.item(row),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )

    def _run_row_probes(
        self,
        gen: int,
        todo: list[tuple[int, str, Callable[[], object]]],
        on_probed: Callable[[int, object], None],
    ) -> None:
        """Run each ``(row, path, probe)`` on one worker, in order.

        Each result reaches ``on_probed(gen, (row, path, info))`` on the GUI
        thread. A new folder scan (``_scan_gen`` change) stops the loop.
        """
        if not todo:
            return
        post = self._jobs.poster(lambda v, g=gen: on_probed(g, v))

        def job() -> None:
            for row, path, probe in todo:
                if gen != self._scan_gen:
                    return  # folder changed: stop opening its files
                post((row, path, probe()))

        self._jobs.run(job, lambda _r: None)

    # -- view mode -----------------------------------------------------------

    def _restore_view_and_metadata(self, settings: QSettings) -> None:
        """Initial List / Grid and Metadata pane from the last session."""
        keys = self._keys
        saved_view = coerce_view_mode(settings.value(keys.view, VIEW_LIST))
        # Prefer list/grid for the initial stack; Preview applies after scan.
        self._pending_preview = saved_view == VIEW_PREVIEW
        last_browse = coerce_view_mode(
            settings.value(keys.last_browse, saved_view), allow_preview=False
        )
        self._last_browse_mode = initial_browse_mode(saved_view, last_browse)
        if self._last_browse_mode == VIEW_GRID:
            self._view_seg.setCurrentData(VIEW_GRID)
            self._view_stack.setCurrentIndex(1)
        else:
            self._view_seg.setCurrentData(VIEW_LIST)
            self._view_stack.setCurrentIndex(0)
        self._view_seg.currentIndexChanged.connect(self._on_view_changed)

        # Metadata pane from last session (default on).
        inspect_on = settings_bool(settings, keys.inspect, True)
        self._inspect_cb.blockSignals(True)
        self._inspect_cb.setChecked(inspect_on)
        self._inspect_cb.blockSignals(False)
        self._toggle_inspect(inspect_on)
        self._inspect_cb.toggled.connect(self._toggle_inspect)

    def _settle_pending_preview(self) -> None:
        """End of ``__init__``: show a saved Preview mode, or drop it."""
        if self._pending_preview and self._scanning:
            # Saved Preview mode: show the segment now; the player loads when
            # the background scan has found something to play.
            self._view_seg.blockSignals(True)
            self._view_seg.setCurrentData(VIEW_PREVIEW)
            self._view_seg.blockSignals(False)
        else:
            # Nothing to scan: a saved Preview mode has nothing to play.
            self._drop_pending_preview()

    def _on_view_changed(self, _index: int) -> None:
        self._before_view_change()
        mode = self._view_seg.currentData()
        settings = browser_qsettings()
        if mode == VIEW_PREVIEW:
            settings.setValue(self._keys.view, VIEW_PREVIEW)
            settings.setValue(self._keys.last_browse, str(self._last_browse_mode or VIEW_LIST))
            self._view_stack.setCurrentIndex(2)
            self._load_preview()
            return
        # Leaving preview
        if self._previewing:
            self._stop_preview_playback()
        if mode == VIEW_GRID:
            self._last_browse_mode = VIEW_GRID
            self._view_stack.setCurrentIndex(1)
            settings.setValue(self._keys.view, VIEW_GRID)
            settings.setValue(self._keys.last_browse, VIEW_GRID)
            self._queue_grid_thumbnails()
        else:
            self._last_browse_mode = VIEW_LIST
            self._view_stack.setCurrentIndex(0)
            settings.setValue(self._keys.view, VIEW_LIST)
            settings.setValue(self._keys.last_browse, VIEW_LIST)

    def _drop_pending_preview(self) -> None:
        """A saved / pasted Preview found nothing to play: back to the browse view."""
        if not self._pending_preview:
            return
        self._pending_preview = False
        if self._view_seg.currentData() == VIEW_PREVIEW:
            self._view_seg.blockSignals(True)
            self._view_seg.setCurrentData(self._last_browse_mode)
            self._view_seg.blockSignals(False)
            self._view_stack.setCurrentIndex(1 if self._last_browse_mode == VIEW_GRID else 0)

    # -- navigation ----------------------------------------------------------

    def is_scanning(self) -> bool:
        """True while the current folder is still being read in the background."""
        return self._scanning

    def _enter_folder(self, directory: str) -> None:
        """Point the path field, places and search at *directory*, then scan it."""
        self._path_edit.setText(directory)
        self._places.set_current_dir(directory)
        self._searchable_tree.set_search_root(directory)
        self._index_folder(directory)
        self._scan_directory(directory)

    def _navigate_to(self, directory: str, *, restore_tree: bool = False) -> None:
        # Stay on Preview if active — the scan reloads the new folder's media.
        # The tree lists missing levels in the background, then selects the folder.
        expand_path_chain(self._tree, self._fs_model, directory, select=True)
        self._fs_model.refresh(directory)
        if restore_tree:
            self._restore_tree_session(directory)
        self._enter_folder(directory)

    def _on_tree_clicked(self, index: QModelIndex) -> None:
        path = self._fs_model.filePath(index)
        if not path:
            return
        if self._fs_model.kind(index) == self._TREE_ITEM_KIND:
            self._on_tree_item_clicked(index)
            return
        _tree_click_toggle_expand(self._tree, self._fs_model, index)
        self._fs_model.refresh(path)
        self._enter_folder(path)

    def _on_path_entered(self) -> None:
        """Navigate to a pasted/typed path (folder, media file, or #### pattern)."""
        raw = clean_path_string(self._path_edit.text())
        if not raw:
            return
        # Reflect cleaned paste (file:// / quotes) back into the field.
        if raw != self._path_edit.text().strip():
            self._path_edit.setText(raw)
        self._navigate_to_path_string(raw)

    def _navigate_to_path_string(self, raw: str, *, prefer_preview: bool = True) -> None:
        """Resolve *raw* to a folder + optional selection and open it."""
        raise NotImplementedError

    # -- preview player ------------------------------------------------------

    # Window title; Preview appends `` — <label>``.
    _WINDOW_TITLE = ""

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
            proxy_preview=True,
            parent=self._preview_page,
        )
        self._player.in_out_changed.connect(self._on_in_out_changed)
        self._preview_host.addWidget(self._player, 1)
        return self._player

    def selected_frame_range(self) -> str:
        """``"first-last"`` from the Preview in/out points, or ``""``.

        Only for the media being opened: in/out set on another sequence or
        video does not carry over.
        """
        player = self._player
        if player is None or not self._in_out_media or not self._in_out_matches_selection():
            return ""
        return frame_range_text(player.in_out_range())

    def accepted_frame_range(self) -> str:
        """Frame range from the Preview in/out points at Open, or ``""``."""
        return str(getattr(self, "_accepted_frame_range", "") or "")

    def _on_in_out_changed(self, in_point: object, out_point: object) -> None:
        player = self._player
        rng = player.in_out_range() if player is not None else None
        if rng is None:
            return
        self._status.setText(in_out_status_text(rng))

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
            self.setWindowTitle(self._WINDOW_TITLE)

    @staticmethod
    def _shutdown_player_widget(player: SequencePlayer) -> None:
        """Stop *player* for good; Qt still owns (and later deletes) the widget."""
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

    # -- close / teardown ----------------------------------------------------

    def _shutdown_workers(self) -> None:
        """Drop background scans / probes and stop the tree and search workers."""
        self._thumb_gen += 1
        self._scan_gen += 1
        self._jobs.shutdown()
        self._fs_model.shutdown()
        self._searchable_tree.shutdown()

    def _finish_accept(self) -> None:
        """Close with Open once the subclass has committed the selection."""
        # Read in/out before the player is shut down.
        self._accepted_frame_range = self.selected_frame_range()
        self._teardown()
        self._save_browser_layout()
        QDialog.accept(self)

    def reject(self) -> None:
        # Always close the dialog. Escape already leaves Preview via the key
        # handlers; window chrome (X) and Cancel must not be trapped in Preview.
        self._teardown()
        self._save_browser_layout()
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._teardown()
        self._save_browser_layout()
        super().closeEvent(event)

    # -- context menu --------------------------------------------------------

    def _grid_item_row(self, item: QListWidgetItem) -> int:
        """Data row of a grid *item* (its stored row, else its position)."""
        return int(item.data(Qt.ItemDataRole.UserRole) or self._grid.row(item))

    def _on_table_context_menu(self, pos: QPoint) -> None:
        index = self._table.indexAt(pos)
        if index.isValid():
            self._select_row(index.row())
        self._show_context_menu(self._table.viewport().mapToGlobal(pos))

    def _on_grid_context_menu(self, pos: QPoint) -> None:
        item = self._grid.itemAt(pos)
        if item is not None:
            self._select_row(self._grid_item_row(item))
        self._show_context_menu(self._grid.viewport().mapToGlobal(pos))

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        preview_act = QAction("Preview", self)
        preview_act.setShortcut(Qt.Key.Key_Space)
        preview_act.triggered.connect(lambda: self._view_seg.setCurrentData(VIEW_PREVIEW))
        menu.addAction(preview_act)
        open_act = QAction("Open", self)
        open_act.setEnabled(self._ok_btn.isEnabled())
        open_act.triggered.connect(self.accept)
        menu.addAction(open_act)
        menu.addSeparator()
        file_path, folder_path = self._copy_path_targets()
        _add_copy_path_actions(menu, file_path=file_path, folder_path=folder_path)
        menu.exec(global_pos)

    # -- Metadata pane -------------------------------------------------------

    # Content side floor when the Metadata pane opens.
    _META_CONTENT_MIN = 400
    # Read the splitter sizes before showing / hiding the pane (True) or after
    # (False). A hidden pane counts as 0 px, so the two give different totals.
    _META_SIZES_BEFORE_TOGGLE = False

    def _toggle_inspect(self, checked: bool) -> None:
        sizes = self._content_splitter.sizes() if self._META_SIZES_BEFORE_TOGGLE else None
        self._meta_panel.setVisible(checked)
        if sizes is None:
            sizes = self._content_splitter.sizes()
        total = sum(sizes)
        if total <= 0:
            total = max(self._content_splitter.width(), 640)
        if checked:
            # Prefer last Metadata width from QSettings; otherwise a modest default.
            saved = parse_int_list(browser_qsettings().value(self._keys.content_split))
            self._content_splitter.setSizes(
                metadata_split(total, saved, content_min=self._META_CONTENT_MIN)
            )
            row = self._metadata_row()
            if row >= 0:
                self._show_metadata(row)
        else:
            # Give the full content width back to the browse view.
            self._content_splitter.setSizes([total, 0])
        self._schedule_layout_save()

    def _on_metadata(self, gen: int, path: str, meta: object) -> None:
        if gen != self._meta_gen:
            return
        if meta is None:
            self._meta_text.setPlainText(
                "This file is not fully local (cloud placeholder / online-only).\n"
                "Download it in Dropbox, iCloud, or OneDrive before Metadata or Preview.\n"
                f"Path: {path}"
            )
            return
        if isinstance(meta, BaseException) or not isinstance(meta, dict):
            self._meta_text.setPlainText(f"File: {Path(path).name}\n\nerror: {meta}")
            return
        lines = [f"File: {Path(path).name}", ""]
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
        self._meta_text.setPlainText("\n".join(lines))

    # -- layout persistence --------------------------------------------------

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
        if outer_split_ok(outer):
            self._outer_splitter.setSizes(outer)

        content = parse_int_list(settings.value(keys.content_split))
        if self._meta_panel.isVisible() and content_split_ok(content):
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
        directory = self._layout_last_dir()
        if directory:
            settings.setValue(keys.last_dir, normalize_dir(directory) or directory)
        selected = self._layout_selected()
        if selected:
            settings.setValue(keys.selected, selected)
        settings.setValue(keys.tree_expanded, collect_expanded_dirs(self._tree, self._fs_model))
        settings.setValue(keys.tree_vscroll, tree_vscroll_value(self._tree))
        settings.sync()

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
