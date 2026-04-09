from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QObject,
    QRegularExpression,
    QSettings,
    QStandardPaths,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QGroupBox,
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
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    COMMON_FPS,
    DEFAULT_DST_E2V,
    DEFAULT_DST_V2E,
    DEFAULT_EXR_COMPRESSION,
    DEFAULT_FRAME_PADDING,
    DEFAULT_SCALE,
    DEFAULT_SRC_E2V,
    DEFAULT_SRC_V2E,
    DEFAULT_START_FRAME,
    DEFAULT_VIDEO_CODEC,
    EXR_COMPRESSIONS,
    OCIO_SOURCE_ENV,
    OCIO_SOURCE_FILE,
    SCALE_OPTIONS,
    VIDEO_CODECS,
)
from .ocio_utils import list_builtin_configs, resolve_ocio_config
from .sequence import probe_exr_colorspace, probe_exr_metadata, scan_exr_sequences
from .style import DESC_STYLE, HINT_STYLE, STATUS_DIM, STATUS_ERR, STATUS_OK
from .video import probe_video_metadata, scan_video_files

try:
    import PyOpenColorIO as OCIO
except ImportError:
    OCIO = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Color-space menu button
# ---------------------------------------------------------------------------


class ColorSpaceButton(QToolButton):
    """A button that pops up a nested QMenu grouped by OCIO family."""

    space_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._current = ""
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._set_display("(none)")

    def current_space(self) -> str:
        return self._current

    def set_current_space(self, name: str) -> None:
        self._current = name
        self._set_display(name)

    def populate(self, families: dict[str, list[str]], select: str = "") -> None:
        old_menu = self._menu
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        old_menu.deleteLater()

        submenu_cache: dict[str, QMenu] = {}
        found = False

        for family in sorted(families.keys()):
            names = families[family]
            if "/" in family:
                parts = family.split("/")
                for depth in range(len(parts)):
                    key = "/".join(parts[: depth + 1])
                    if key not in submenu_cache:
                        parent_key = "/".join(parts[:depth]) if depth else ""
                        parent = submenu_cache[parent_key] if parent_key else self._menu
                        submenu_cache[key] = parent.addMenu(parts[depth])
                target_menu = submenu_cache[family]
            else:
                if len(names) == 1:
                    target_menu = self._menu
                else:
                    target_menu = self._menu.addMenu(family)
                    submenu_cache[family] = target_menu

            for cs_name in names:
                action = target_menu.addAction(cs_name)
                action.triggered.connect(lambda checked, n=cs_name: self._pick(n))
                if cs_name == select:
                    found = True

        if select and found:
            self._pick(select)
        elif select:
            self._current = select
            self._set_display(select)

    def _pick(self, name: str) -> None:
        self._current = name
        self._set_display(name)
        self.space_changed.emit(name)

    def try_select(self, name: str) -> bool:
        """Select *name* if it exists in the menu. Returns True on match."""
        if not name:
            return False
        if self._find_action(self._menu, name):
            self._pick(name)
            return True
        # Case-insensitive fallback: scan all actions for a match
        found = self._find_action_ci(self._menu, name.lower())
        if found:
            self._pick(found)
            return True
        return False

    @staticmethod
    def _find_action(menu: QMenu, name: str) -> bool:
        for action in menu.actions():
            sub = action.menu()
            if sub:
                if ColorSpaceButton._find_action(sub, name):
                    return True
            elif action.text() == name:
                return True
        return False

    @staticmethod
    def _find_action_ci(menu: QMenu, name_lower: str) -> str:
        """Case-insensitive search; returns the exact action text or ''."""
        for action in menu.actions():
            sub = action.menu()
            if sub:
                hit = ColorSpaceButton._find_action_ci(sub, name_lower)
                if hit:
                    return hit
            elif action.text().lower() == name_lower:
                return action.text()
        return ""

    def _set_display(self, text: str) -> None:
        metrics = self.fontMetrics()
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, max(self.width() - 30, 120))
        self.setText(elided)
        self.setToolTip(text)


# ---------------------------------------------------------------------------
# FPS combo with common presets + custom
# ---------------------------------------------------------------------------


class FpsCombo(QWidget):
    """Combo with common fps presets and a custom spinbox."""

    CUSTOM_LABEL = "Custom\u2026"

    def __init__(self, settings: QSettings, key: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._key = key

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._combo = QComboBox()
        for fps_val in COMMON_FPS:
            label = str(int(fps_val)) if fps_val == int(fps_val) else f"{fps_val:.3f}"
            self._combo.addItem(label, float(fps_val))
        self._combo.addItem(self.CUSTOM_LABEL, -1.0)

        self._spin = QDoubleSpinBox()
        self._spin.setRange(1.0, 240.0)
        self._spin.setDecimals(3)
        self._spin.setValue(120.0)
        self._spin.setSuffix(" fps")
        self._spin.setVisible(False)

        layout.addWidget(self._combo, 1)
        layout.addWidget(self._spin)

        saved = float(settings.value(key, 24.0))
        self._restore(saved)

        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        self._spin.valueChanged.connect(self._on_spin_changed)

    def fps(self) -> float:
        val = self._combo.currentData()
        if val == -1.0:
            return float(self._spin.value())
        return float(val)

    def _restore(self, saved: float) -> None:
        for i in range(self._combo.count()):
            data = self._combo.itemData(i)
            if data is not None and data != -1.0 and abs(data - saved) < 0.01:
                self._combo.setCurrentIndex(i)
                return
        self._combo.setCurrentIndex(self._combo.count() - 1)
        self._spin.setValue(saved)
        self._spin.setVisible(True)

    def _on_combo_changed(self, idx: int) -> None:
        val = self._combo.currentData()
        is_custom = val == -1.0
        self._spin.setVisible(is_custom)
        if not is_custom:
            self._settings.setValue(self._key, val)
        else:
            self._settings.setValue(self._key, float(self._spin.value()))

    def _on_spin_changed(self, val: float) -> None:
        if self._combo.currentData() == -1.0:
            self._settings.setValue(self._key, float(val))


# ---------------------------------------------------------------------------
# OCIO config panel
# ---------------------------------------------------------------------------


class OcioConfigPanel(QGroupBox):
    """Panel for selecting OCIO config."""

    config_changed = Signal()

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._prev_index = 0
        self._file_path = settings.value("ocio/file_path", "")

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("OCIO Config:"))
        self._source_combo = QComboBox()
        self._source_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_row.addWidget(self._source_combo, 1)
        layout.addLayout(source_row)

        self._status = QLabel()
        self._status.setStyleSheet(STATUS_DIM)
        layout.addWidget(self._status)

        self._builtin_configs = list_builtin_configs()
        env_ocio = os.environ.get("OCIO", "")
        env_label = (
            f"$OCIO environment variable ({Path(env_ocio).name})"
            if env_ocio
            else "$OCIO environment variable (not set)"
        )
        self._source_combo.addItem(env_label, OCIO_SOURCE_ENV)
        self._source_combo.insertSeparator(self._source_combo.count())
        for name, label, recommended in self._builtin_configs:
            short = label
            if recommended:
                short += "  \u2605"
            self._source_combo.addItem(short, name)
        self._source_combo.insertSeparator(self._source_combo.count())
        self._custom_file_idx = self._source_combo.count()
        self._update_custom_label()

        self._select_saved_source()
        self._prev_index = self._source_combo.currentIndex()

        self._source_combo.currentIndexChanged.connect(self._on_source_changed)

    def _update_custom_label(self) -> None:
        if self._file_path:
            label = f"Custom: {Path(self._file_path).name}"
        else:
            label = "Custom config file\u2026"
        if self._source_combo.count() > self._custom_file_idx:
            self._source_combo.setItemText(self._custom_file_idx, label)
        else:
            self._source_combo.addItem(label, OCIO_SOURCE_FILE)

    def _select_saved_source(self) -> None:
        saved = self._settings.value("ocio/source", "")
        if not saved:
            env_ocio = os.environ.get("OCIO", "")
            if env_ocio and Path(env_ocio).expanduser().is_file():
                saved = OCIO_SOURCE_ENV
            else:
                recommended = [b for b in self._builtin_configs if b[2]]
                saved = recommended[0][0] if recommended else self._builtin_configs[-1][0]
        for i in range(self._source_combo.count()):
            if self._source_combo.itemData(i) == saved:
                self._source_combo.setCurrentIndex(i)
                return
        self._source_combo.setCurrentIndex(0)

    def current_source_key(self) -> str:
        return self._source_combo.currentData() or ""

    def load_config(self):  # -> OCIO.Config | None
        source = self.current_source_key()
        file_path = self._file_path
        try:
            cfg = resolve_ocio_config(source, file_path=file_path)
        except Exception as e:
            self._status.setText(f"\u2718  {e}")
            self._status.setStyleSheet(STATUS_ERR)
            return None

        n = len(list(cfg.getColorSpaceNames()))
        if source == OCIO_SOURCE_ENV:
            desc = f"$OCIO: {os.environ.get('OCIO', '?')}"
        elif source == OCIO_SOURCE_FILE:
            desc = f"File: {Path(file_path).name}"
        else:
            desc = source
        self._status.setText(f"\u2714  {desc}  ({n} color spaces)")
        self._status.setStyleSheet(STATUS_OK)
        return cfg

    def _on_source_changed(self, _idx: int) -> None:
        source = self.current_source_key()
        if source == OCIO_SOURCE_FILE:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "OCIO config file",
                self._file_path or "",
                "OCIO (*.ocio);;All (*.*)",
            )
            if path:
                self._file_path = path
                self._settings.setValue("ocio/file_path", path)
                self._update_custom_label()
                self._prev_index = self._source_combo.currentIndex()
                self._settings.setValue("ocio/source", source)
                self.config_changed.emit()
            else:
                self._source_combo.blockSignals(True)
                self._source_combo.setCurrentIndex(self._prev_index)
                self._source_combo.blockSignals(False)
        else:
            self._prev_index = self._source_combo.currentIndex()
            self._settings.setValue("ocio/source", source)
            self.config_changed.emit()


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


class _PlacesSidebar(QWidget):
    """Sidebar listing OS locations and user-defined favorite directories."""

    navigate_requested = Signal(str)
    _FAVORITES_KEY = "browser/favorites"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(140)
        self._current_dir = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._ctx_menu)

        for icon, name, location in _OS_PLACES:
            path = QStandardPaths.writableLocation(location)
            if not path or not Path(path).is_dir():
                continue
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)

        divider = QListWidgetItem()
        divider.setFlags(Qt.ItemFlag.NoItemFlags)
        divider.setSizeHint(divider.sizeHint().expandedTo(QWidget().sizeHint()))
        self._list.addItem(divider)

        from .style import _PALETTE

        frame = QWidget()
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(4, 6, 4, 4)
        frame_layout.setSpacing(0)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {_PALETTE['BORDER']};")
        frame_layout.addWidget(line)
        self._list.setItemWidget(divider, frame)

        header = QListWidgetItem("\u2605  Favorites")
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        self._list.addItem(header)
        self._fav_start = self._list.count()

        settings = QSettings()
        saved = settings.value(self._FAVORITES_KEY, [])
        if isinstance(saved, str):
            saved = [saved] if saved else []
        for fav_path in saved:
            if Path(fav_path).is_dir():
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
        if path and Path(path).is_dir():
            self.navigate_requested.emit(path)

    def _add_current(self) -> None:
        if not self._current_dir or not Path(self._current_dir).is_dir():
            return
        for i in range(self._fav_start, self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == self._current_dir:
                return
        self._add_fav_item(self._current_dir)
        self._save_favorites()

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
        row = self._list.row(item)
        if row < self._fav_start:
            return
        menu = QMenu(self)
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
        QSettings().setValue(self._FAVORITES_KEY, favs)


# ---------------------------------------------------------------------------
# Directory search: background worker + searchable tree panel
# ---------------------------------------------------------------------------

_SEARCH_SKIP_DIRS = frozenset({
    # VCS
    ".git", ".svn", ".hg", ".bzr",
    # Python
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", ".env", ".tox", ".nox",
    "build", "dist", ".eggs", "site-packages",
    # IDE
    ".idea", ".vscode", ".vs",
    # Temp / cache
    ".cache", ".tmp", ".temp", "tmp", "temp",
    # macOS
    ".Trash", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100",
    ".TemporaryItems", ".VolumeIcon.icns",
    # System / library dirs (by name — catches nested occurrences too)
    "System", "Library", "private",
    "usr", "bin", "sbin", "etc", "var", "opt",
    # Windows
    "Windows", "ProgramData", "Program Files", "Program Files (x86)",
    "$Recycle.Bin", "System Volume Information",
    "AppData", "Recovery", "PerfLogs",
    # Linux
    "proc", "sys", "dev", "run", "snap", "lost+found",
    # Package / app internals
    ".app", ".framework", ".bundle", ".plugin", ".kext",
    "__MACOSX", "Frameworks", "PlugIns",
})

_SEARCH_SKIP_ABSPATHS: set[str] = set()
for _d in (
    "/System", "/Library", "/private", "/usr", "/bin", "/sbin", "/etc",
    "/var", "/opt", "/cores", "/dev", "/proc", "/sys", "/run", "/snap",
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\ProgramData", "C:\\$Recycle.Bin", "C:\\Recovery",
):
    _SEARCH_SKIP_ABSPATHS.add(_d)
_SEARCH_SKIP_ABSPATHS = frozenset(_SEARCH_SKIP_ABSPATHS)

_SEARCH_SKIP_FILES = frozenset({
    ".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r",
    ".localized", ".CFUserTextEncoding", ".com.apple.timemachine.donotpresent",
})
_SEARCH_SKIP_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".o", ".obj", ".class",
    ".swp", ".swo", ".swn",
    ".tmp", ".bak", ".orig",
})

_VIDEO_EXTS = frozenset({
    ".mp4", ".mov", ".mkv", ".avi", ".mxf", ".webm", ".m4v", ".ts",
    ".wmv", ".flv", ".f4v", ".vob", ".ogv", ".ogg",
    ".3gp", ".3g2", ".m2ts", ".mts", ".mpg", ".mpeg", ".m2v",
    ".divx", ".rm", ".rmvb", ".asf",
    ".dv", ".r3d", ".braw", ".ari", ".arx", ".mj2",
})

_MAX_SEARCH_DEPTH = 15
_SEARCH_BATCH_SIZE = 60
_MAX_SEARCH_RESULTS = 500
_SEARCH_DEBOUNCE_MS = 200


class _DirSearchWorker(QObject):
    """Runs recursive directory searches on a background thread.

    Each call to ``start_search`` cancels any in-flight search, creates a
    fresh ``threading.Event``, and spawns a daemon thread.  Results stream
    back via ``batch_ready`` in chunks for minimal signal overhead.
    """

    batch_ready = Signal(list)
    search_finished = Signal(int)

    def __init__(
        self,
        parent: QObject | None = None,
        ext_filter: frozenset[str] | None = None,
        dirs_only: bool = False,
    ):
        super().__init__(parent)
        self._cancel: threading.Event = threading.Event()
        self._ext_filter = ext_filter
        self._dirs_only = dirs_only

    def start_search(self, root: str, query: str) -> None:
        self._cancel.set()
        cancel = threading.Event()
        self._cancel = cancel
        threading.Thread(
            target=self._run, args=(root, query, cancel), daemon=True
        ).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(
        self, root: str, query: str, cancel: threading.Event
    ) -> None:
        query_lower = query.lower()
        root_stripped = root.rstrip(os.sep)
        root_len = len(root_stripped) + 1
        batch: list[tuple[str, str, str, bool]] = []
        total = 0

        stack: list[tuple[str, int]] = [(root_stripped, 0)]
        while stack:
            if cancel.is_set():
                return
            dirpath, depth = stack.pop()
            try:
                scanner = os.scandir(dirpath)
            except OSError:
                continue
            with scanner:
                for entry in scanner:
                    if cancel.is_set():
                        return
                    name = entry.name
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue

                    name_lower = name.lower()
                    ext_lower = os.path.splitext(name_lower)[1]

                    if not is_dir and (
                        self._dirs_only
                        or name in _SEARCH_SKIP_FILES
                        or ext_lower in _SEARCH_SKIP_SUFFIXES
                        or (self._ext_filter is not None
                            and ext_lower not in self._ext_filter)
                    ):
                        pass
                    elif query_lower in name_lower or query_lower == ext_lower:
                        rel = entry.path[root_len:]
                        batch.append((name, entry.path, rel, is_dir))
                        total += 1
                        if len(batch) >= _SEARCH_BATCH_SIZE:
                            self.batch_ready.emit(batch)
                            batch = []
                        if total >= _MAX_SEARCH_RESULTS:
                            if batch:
                                self.batch_ready.emit(batch)
                            self.search_finished.emit(total)
                            return

                    if (
                        is_dir
                        and depth < _MAX_SEARCH_DEPTH
                        and name not in _SEARCH_SKIP_DIRS
                        and not name.startswith(".")
                        and entry.path not in _SEARCH_SKIP_ABSPATHS
                    ):
                        stack.append((entry.path, depth + 1))

        if batch:
            self.batch_ready.emit(batch)
        self.search_finished.emit(total)


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

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("\U0001f50d  Search folders\u2026")
        self._search_edit.setClearButtonEnabled(True)
        layout.addWidget(self._search_edit)

        self._spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._spinner_idx = 0
        self._spinner_action = QAction(self._search_edit)
        self._search_edit.addAction(
            self._spinner_action, QLineEdit.ActionPosition.TrailingPosition
        )
        self._spinner_action.setVisible(False)
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(80)
        self._spinner_timer.timeout.connect(self._advance_spinner)

        layout.addWidget(tree, 1)

        self._results = QListWidget()
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

    def _advance_spinner(self) -> None:
        ch = self._spinner_frames[self._spinner_idx % len(self._spinner_frames)]
        self._spinner_idx += 1
        size = self._search_edit.fontMetrics().height()
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setPen(self._search_edit.palette().text().color())
        p.setFont(self._search_edit.font())
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, ch)
        p.end()
        self._spinner_action.setIcon(QIcon(pix))

    def _start_spinner(self) -> None:
        self._spinner_idx = 0
        self._spinner_action.setVisible(True)
        self._advance_spinner()
        self._spinner_timer.start()

    def _stop_spinner(self) -> None:
        self._spinner_timer.stop()
        self._spinner_action.setVisible(False)

    def _on_text_changed(self, text: str) -> None:
        if not text.strip():
            self._worker.cancel()
            self._debounce.stop()
            self._stop_spinner()
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
        self._start_spinner()
        root = self._search_root or QDir.homePath()
        self._worker.start_search(root, query)

    def _on_batch(self, items: list) -> None:
        for name, _full_path, rel_path, is_dir in items:
            icon = "\U0001f4c1" if is_dir else "\U0001f4c4"
            parent = os.path.dirname(rel_path)
            display = f"{icon}  {name}    {parent}" if parent else f"{icon}  {name}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, _full_path)
            item.setToolTip(_full_path)
            self._results.addItem(item)

    def _on_search_done(self, total: int) -> None:
        self._stop_spinner()
        suffix = "s" if total != 1 else ""
        cap = " (limit reached)" if total >= _MAX_SEARCH_RESULTS else ""
        self._search_status.setText(f"{total} result{suffix}{cap}")

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        target = path if os.path.isdir(path) else os.path.dirname(path)
        self.result_navigated.emit(target)
        self._search_edit.clear()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._search_edit.text():
            self._search_edit.clear()
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# EXR sequence browser dialog (with metadata inspector)
# ---------------------------------------------------------------------------


class SequenceBrowserDialog(QDialog):
    """Directory browser + EXR sequence table + toggleable metadata panel."""

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
    ):
        super().__init__(parent)
        self.setWindowTitle("Browse EXR Sequences")
        self.resize(1060, 450)
        self._selected_dir: str = ""
        self._selected_name: str = ""
        self._seq_data: list[dict] = []
        self._auto_select_name = select_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # path bar + inspect toggle
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Folder:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Navigate in the tree or paste a path here")
        path_row.addWidget(self._path_edit, 1)
        self._inspect_cb = QCheckBox("Inspect")
        self._inspect_cb.setToolTip("Show EXR metadata for selected sequence")
        path_row.addWidget(self._inspect_cb)
        layout.addLayout(path_row)

        # -- left: places sidebar + dir tree (full height) --
        self._places = _PlacesSidebar()
        self._places.navigate_requested.connect(self._navigate_to)

        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath(QDir.rootPath())
        self._fs_model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)
        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setHeaderHidden(True)
        for col in range(1, self._fs_model.columnCount()):
            self._tree.hideColumn(col)
        self._tree.setMinimumWidth(200)
        tree_header = self._tree.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self._searchable_tree = _SearchableTree(self._tree, dirs_only=True)
        self._searchable_tree.result_navigated.connect(self._navigate_to)

        left_panel = QWidget()
        left_layout = QHBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self._places)
        left_layout.addWidget(self._searchable_tree, 1)

        # -- center: sequence table --
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
        self._table.setMinimumWidth(320)
        th = self._table.horizontalHeader()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self._COLUMNS)):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        center_layout.addWidget(self._table, 1)

        # -- right: metadata inspector --
        self._meta_panel = QWidget()
        meta_layout = QVBoxLayout(self._meta_panel)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        meta_layout.addWidget(QLabel("<b>EXR Metadata</b>"))
        self._meta_text = QPlainTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setMinimumWidth(240)
        self._meta_text.setObjectName("metaPane")
        meta_layout.addWidget(self._meta_text, 1)
        self._meta_panel.setVisible(False)

        # content splitter: table + metadata
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(center)
        self._content_splitter.addWidget(self._meta_panel)
        self._content_splitter.setStretchFactor(0, 3)
        self._content_splitter.setStretchFactor(1, 2)
        for i in range(self._content_splitter.count()):
            self._content_splitter.setCollapsible(i, False)

        # right side: content splitter + status/buttons row
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._content_splitter, 1)

        bottom_row = QHBoxLayout()
        self._status = QLabel()
        self._status.setStyleSheet(STATUS_DIM)
        bottom_row.addWidget(self._status, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._ok_btn.setEnabled(False)
        bottom_row.addWidget(buttons)
        right_layout.addLayout(bottom_row)

        # outer splitter: left panel (full height) | right side
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.addWidget(left_panel)
        self._outer_splitter.addWidget(right_widget)
        self._outer_splitter.setStretchFactor(0, 2)
        self._outer_splitter.setStretchFactor(1, 5)
        for i in range(self._outer_splitter.count()):
            self._outer_splitter.setCollapsible(i, False)
        layout.addWidget(self._outer_splitter, 1)
        self._tree.clicked.connect(self._on_tree_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellDoubleClicked.connect(lambda _r, _c: self.accept())
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._inspect_cb.toggled.connect(self._toggle_inspect)

        if start_dir and Path(start_dir).is_dir():
            self._navigate_to(start_dir)

    def selected_directory(self) -> str:
        return self._selected_dir

    def selected_name(self) -> str:
        return self._selected_name

    def _navigate_to(self, directory: str) -> None:
        idx = self._fs_model.index(directory)
        if idx.isValid():
            self._tree.setCurrentIndex(idx)
            self._tree.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            self._tree.expand(idx)
        self._path_edit.setText(directory)
        self._places.set_current_dir(directory)
        self._searchable_tree.set_search_root(directory)
        self._scan_directory(directory)

    def _on_tree_clicked(self, index) -> None:
        path = self._fs_model.filePath(index)
        if path:
            self._path_edit.setText(path)
            self._places.set_current_dir(path)
            self._searchable_tree.set_search_root(path)
            self._scan_directory(path)

    def _on_path_entered(self) -> None:
        path = self._path_edit.text().strip()
        if path and Path(path).is_dir():
            self._navigate_to(path)

    def _scan_directory(self, directory: str) -> None:
        self._table.setRowCount(0)
        self._selected_dir = directory
        self._selected_name = ""
        self._seq_data = []
        self._ok_btn.setEnabled(False)
        self._meta_text.clear()

        try:
            seqs = scan_exr_sequences(directory)
        except Exception as e:
            self._status.setText(f"Error: {e}")
            return

        if not seqs:
            self._status.setText("No EXR sequences in this folder.")
            return

        self._seq_data = seqs
        self._table.setRowCount(len(seqs))
        for row, s in enumerate(seqs):
            display_name = s.get("pattern", s["name"])
            name_item = QTableWidgetItem(display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, s["name"])

            frames_item = QTableWidgetItem(str(s["frames"]))
            frames_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            range_item = QTableWidgetItem(s["range"])
            range_item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )

            res_item = QTableWidgetItem(s.get("resolution", ""))
            res_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            type_item = QTableWidgetItem(s.get("pixel_type", ""))
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            comp_item = QTableWidgetItem(s.get("compression", ""))
            comp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            cs_item = QTableWidgetItem(s.get("colorspace", ""))
            cs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, frames_item)
            self._table.setItem(row, 2, range_item)
            self._table.setItem(row, 3, res_item)
            self._table.setItem(row, 4, type_item)
            self._table.setItem(row, 5, comp_item)
            self._table.setItem(row, 6, cs_item)

        selected = False
        if self._auto_select_name:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == self._auto_select_name:
                    self._table.selectRow(row)
                    selected = True
                    break
        if not selected and len(seqs) == 1:
            self._table.selectRow(0)

        self._status.setText(f"{len(seqs)} sequence(s) found")

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            row = rows[0].row()
            item = self._table.item(row, 0)
            self._selected_name = item.data(Qt.ItemDataRole.UserRole) if item else ""
            self._ok_btn.setEnabled(bool(self._selected_name))
            if self._meta_panel.isVisible():
                self._show_metadata(row)
        else:
            self._selected_name = ""
            self._ok_btn.setEnabled(False)

    def _toggle_inspect(self, checked: bool) -> None:
        sizes = self._outer_splitter.sizes()
        self._meta_panel.setVisible(checked)
        self._outer_splitter.setSizes(sizes)
        if checked:
            rows = self._table.selectionModel().selectedRows()
            if rows:
                self._show_metadata(rows[0].row())

    def _show_metadata(self, row: int) -> None:
        if row < 0 or row >= len(self._seq_data):
            self._meta_text.setPlainText("")
            return
        s = self._seq_data[row]
        directory = s["path"]
        name = s["name"]

        import fileseq

        seqs = fileseq.findSequencesOnDisk(directory)
        first_path = ""
        for sq in seqs:
            if (
                sq.basename().rstrip("._") == name
                and sq.extension().lower() == ".exr"
                and sq.frameSet()
            ):
                first_path = sq.frame(sorted(sq.frameSet())[0])
                break
        if not first_path:
            self._meta_text.setPlainText("Could not locate first frame.")
            return

        meta = probe_exr_metadata(first_path)
        lines = [f"File: {Path(first_path).name}", ""]
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
        self._meta_text.setPlainText("\n".join(lines))


# ---------------------------------------------------------------------------
# Video file browser dialog (with metadata inspector)
# ---------------------------------------------------------------------------


class VideoBrowserDialog(QDialog):
    """Directory browser + video file table + toggleable metadata panel."""

    _COLUMNS = ["Name", "Resolution", "Codec", "FPS", "Frames", "Duration"]

    def __init__(self, start_dir: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Browse Video Files")
        self.resize(1060, 450)
        self._selected_path: str = ""
        self._file_data: list[dict[str, str]] = []
        self._auto_select_path: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Folder:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Navigate in the tree or paste a path here")
        path_row.addWidget(self._path_edit, 1)
        self._inspect_cb = QCheckBox("Inspect")
        self._inspect_cb.setToolTip("Show video metadata for selected file")
        path_row.addWidget(self._inspect_cb)
        layout.addLayout(path_row)

        # -- left: places sidebar + dir tree (full height) --
        self._places = _PlacesSidebar()
        self._places.navigate_requested.connect(self._navigate_to)

        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath(QDir.rootPath())
        self._fs_model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)
        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setHeaderHidden(True)
        for col in range(1, self._fs_model.columnCount()):
            self._tree.hideColumn(col)
        self._tree.setMinimumWidth(200)
        tree_header = self._tree.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self._searchable_tree = _SearchableTree(self._tree, ext_filter=_VIDEO_EXTS)
        self._searchable_tree.result_navigated.connect(self._navigate_to)

        left_panel = QWidget()
        left_layout = QHBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self._places)
        left_layout.addWidget(self._searchable_tree, 1)

        # -- center: video file table --
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
        self._table.setMinimumWidth(320)
        th = self._table.horizontalHeader()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self._COLUMNS)):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        center_layout.addWidget(self._table, 1)

        # -- right: metadata inspector --
        self._meta_panel = QWidget()
        meta_layout = QVBoxLayout(self._meta_panel)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        meta_layout.addWidget(QLabel("<b>Video Metadata</b>"))
        self._meta_text = QPlainTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setMinimumWidth(240)
        self._meta_text.setObjectName("metaPane")
        meta_layout.addWidget(self._meta_text, 1)
        self._meta_panel.setVisible(False)

        # content splitter: table + metadata
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(center)
        self._content_splitter.addWidget(self._meta_panel)
        self._content_splitter.setStretchFactor(0, 3)
        self._content_splitter.setStretchFactor(1, 2)
        for i in range(self._content_splitter.count()):
            self._content_splitter.setCollapsible(i, False)

        # right side: content splitter + status/buttons row
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._content_splitter, 1)

        bottom_row = QHBoxLayout()
        self._status = QLabel()
        self._status.setStyleSheet(STATUS_DIM)
        bottom_row.addWidget(self._status, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._ok_btn.setEnabled(False)
        bottom_row.addWidget(buttons)
        right_layout.addLayout(bottom_row)

        # outer splitter: left panel (full height) | right side
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.addWidget(left_panel)
        self._outer_splitter.addWidget(right_widget)
        self._outer_splitter.setStretchFactor(0, 2)
        self._outer_splitter.setStretchFactor(1, 5)
        for i in range(self._outer_splitter.count()):
            self._outer_splitter.setCollapsible(i, False)
        layout.addWidget(self._outer_splitter, 1)

        self._tree.clicked.connect(self._on_tree_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellDoubleClicked.connect(lambda _r, _c: self.accept())
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._inspect_cb.toggled.connect(self._toggle_inspect)

        if start_dir:
            d = Path(start_dir)
            if d.is_file():
                self._auto_select_path = str(d)
                d = d.parent
            if d.is_dir():
                self._navigate_to(str(d))

    def selected_path(self) -> str:
        return self._selected_path

    def _navigate_to(self, directory: str) -> None:
        idx = self._fs_model.index(directory)
        if idx.isValid():
            self._tree.setCurrentIndex(idx)
            self._tree.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            self._tree.expand(idx)
        self._path_edit.setText(directory)
        self._places.set_current_dir(directory)
        self._searchable_tree.set_search_root(directory)
        self._scan_directory(directory)

    def _on_tree_clicked(self, index) -> None:
        path = self._fs_model.filePath(index)
        if path:
            self._path_edit.setText(path)
            self._places.set_current_dir(path)
            self._searchable_tree.set_search_root(path)
            self._scan_directory(path)

    def _on_path_entered(self) -> None:
        path = self._path_edit.text().strip()
        if path and Path(path).is_dir():
            self._navigate_to(path)

    def _scan_directory(self, directory: str) -> None:
        self._table.setRowCount(0)
        self._selected_path = ""
        self._file_data = []
        self._ok_btn.setEnabled(False)
        self._meta_text.clear()

        try:
            files = scan_video_files(directory)
        except Exception as e:
            self._status.setText(f"Error: {e}")
            return

        if not files:
            self._status.setText("No video files in this folder.")
            return

        self._file_data = files
        self._table.setRowCount(len(files))
        center_align = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        for row, f in enumerate(files):
            name_item = QTableWidgetItem(f["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, f["path"])

            res_item = QTableWidgetItem(f.get("resolution", ""))
            res_item.setTextAlignment(center_align)

            codec_item = QTableWidgetItem(f.get("codec", ""))
            codec_item.setTextAlignment(center_align)

            fps_item = QTableWidgetItem(f.get("fps", ""))
            fps_item.setTextAlignment(center_align)

            frames_item = QTableWidgetItem(f.get("frames", ""))
            frames_item.setTextAlignment(center_align)

            dur_item = QTableWidgetItem(f.get("duration", ""))
            dur_item.setTextAlignment(center_align)

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, res_item)
            self._table.setItem(row, 2, codec_item)
            self._table.setItem(row, 3, fps_item)
            self._table.setItem(row, 4, frames_item)
            self._table.setItem(row, 5, dur_item)

        selected = False
        if self._auto_select_path:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == self._auto_select_path:
                    self._table.selectRow(row)
                    selected = True
                    break
        if not selected and len(files) == 1:
            self._table.selectRow(0)

        self._status.setText(f"{len(files)} video file(s) found")

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            row = rows[0].row()
            item = self._table.item(row, 0)
            self._selected_path = item.data(Qt.ItemDataRole.UserRole) if item else ""
            self._ok_btn.setEnabled(bool(self._selected_path))
            if self._meta_panel.isVisible():
                self._show_metadata(row)
        else:
            self._selected_path = ""
            self._ok_btn.setEnabled(False)

    def _toggle_inspect(self, checked: bool) -> None:
        sizes = self._outer_splitter.sizes()
        self._meta_panel.setVisible(checked)
        self._outer_splitter.setSizes(sizes)
        if checked:
            rows = self._table.selectionModel().selectedRows()
            if rows:
                self._show_metadata(rows[0].row())

    def _show_metadata(self, row: int) -> None:
        if row < 0 or row >= len(self._file_data):
            self._meta_text.setPlainText("")
            return
        fpath = self._file_data[row]["path"]
        meta = probe_video_metadata(fpath)
        lines = [f"File: {Path(fpath).name}", ""]
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
        self._meta_text.setPlainText("\n".join(lines))


# ---------------------------------------------------------------------------
# EXR compression settings dialog
# ---------------------------------------------------------------------------

_EXR_HAS_SETTINGS = {"dwaa", "dwab", "zip", "zips"}
_CODEC_HAS_SETTINGS = {"h264", "prores", "prores_4444"}

_EXR_COMPRESSION_HELP: dict[str, str] = {
    "none": "No compression. Fastest write, largest files.",
    "rle": "Run-length encoding. Fast, good for flat areas.",
    "zip": "Zip per scanline block (16 rows). Good general-purpose.",
    "zips": "Zip per scanline. Slightly smaller than ZIP.",
    "piz": "Wavelet-based. Best lossless ratio for noisy/CG images.",
    "pxr24": "Lossy 24-bit float. Good ratio, slight precision loss.",
    "b44": "Lossy fixed-rate. Constant block size, fast random access.",
    "b44a": "Like B44 but flat areas compress further.",
    "dwaa": "Lossy DCT-based, per-scanline. Best lossy ratio at low levels.",
    "dwab": "Lossy DCT-based, per-tile (256 scanlines). Slightly better ratio than DWAA.",
}


class ExrCompressionSettingsDialog(QDialog):
    """Show settings relevant to the selected EXR compression method."""

    def __init__(
        self,
        compression: str,
        settings: QSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._compression = compression
        self._settings = settings
        self.setWindowTitle(f"EXR Compression Settings — {compression.upper()}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        desc = _EXR_COMPRESSION_HELP.get(compression, "")
        if desc:
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(DESC_STYLE)
            layout.addWidget(lbl)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._dwa_spin: QSpinBox | None = None
        self._zip_spin: QSpinBox | None = None

        if compression in ("dwaa", "dwab"):
            saved = int(float(settings.value("exr_opts/dwa_level", 45)))
            row = QHBoxLayout()
            row.setSpacing(6)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 250)
            slider.setValue(saved)
            self._dwa_spin = QSpinBox()
            self._dwa_spin.setRange(0, 250)
            self._dwa_spin.setValue(saved)
            self._dwa_spin.setFixedWidth(64)
            self._dwa_spin.setToolTip(
                "0 = lossless, 45 = visually lossless (default), higher = more compression"
            )
            slider.valueChanged.connect(self._dwa_spin.setValue)
            self._dwa_spin.valueChanged.connect(slider.setValue)
            row.addWidget(slider, 1)
            row.addWidget(self._dwa_spin)
            hint = QLabel("0 = lossless · 45 = visually lossless (default) · 100+ = aggressive")
            hint.setStyleSheet(HINT_STYLE)
            form.addRow("Compression level", row)
            form.addRow("", hint)
        elif compression in ("zip", "zips"):
            saved = int(settings.value("exr_opts/zip_level", 4))
            row = QHBoxLayout()
            row.setSpacing(6)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(1, 9)
            slider.setValue(saved)
            self._zip_spin = QSpinBox()
            self._zip_spin.setRange(1, 9)
            self._zip_spin.setValue(saved)
            self._zip_spin.setFixedWidth(48)
            self._zip_spin.setToolTip("1 = fastest, 9 = best compression")
            slider.valueChanged.connect(self._zip_spin.setValue)
            self._zip_spin.valueChanged.connect(slider.setValue)
            row.addWidget(slider, 1)
            row.addWidget(self._zip_spin)
            form.addRow("Zip level", row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self) -> dict[str, str]:
        """Return dict of option key -> value strings."""
        result: dict[str, str] = {}
        if self._dwa_spin is not None:
            result["dwa_compression_level"] = str(self._dwa_spin.value())
        if self._zip_spin is not None:
            result["zip_level"] = str(self._zip_spin.value())
        return result

    def accept(self) -> None:
        if self._dwa_spin is not None:
            self._settings.setValue("exr_opts/dwa_level", float(self._dwa_spin.value()))
        if self._zip_spin is not None:
            self._settings.setValue("exr_opts/zip_level", self._zip_spin.value())
        super().accept()


# ---------------------------------------------------------------------------
# Video codec settings dialog
# ---------------------------------------------------------------------------

_CODEC_HELP: dict[str, str] = {
    "prores": "Apple ProRes 422 HQ — high quality, large files, wide compatibility.",
    "prores_4444": "Apple ProRes 4444 — highest quality ProRes with alpha support.",
    "h264": "H.264 — excellent compression, universal playback support.",
    "dnxhr_hq": "DNxHR HQ — Avid's high-quality intermediate codec.",
    "dnxhr_hqx": "DNxHR HQX — 10-bit variant for high-end workflows.",
    "ffv1": "FFV1 — mathematically lossless, open-source archival codec.",
}

_H264_PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]

_PRORES_PROFILES = [
    ("0", "Proxy"),
    ("1", "LT"),
    ("2", "Standard"),
    ("3", "HQ"),
]

_PRORES_4444_PROFILES = [
    ("4", "4444"),
    ("5", "4444 XQ"),
]


class VideoCodecSettingsDialog(QDialog):
    """Show settings relevant to the selected video codec."""

    def __init__(
        self,
        codec_key: str,
        settings: QSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._codec_key = codec_key
        self._settings = settings

        display = codec_key
        for k, d, _c, _p in VIDEO_CODECS:
            if k == codec_key:
                display = d
                break
        self.setWindowTitle(f"Codec Settings — {display}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        desc = _CODEC_HELP.get(codec_key, "")
        if desc:
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(DESC_STYLE)
            layout.addWidget(lbl)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._crf_spin: QSpinBox | None = None
        self._preset: QComboBox | None = None
        self._prores_profile: QComboBox | None = None

        if codec_key == "h264":
            saved_crf = int(settings.value("codec_opts/h264_crf", 18))
            crf_row = QHBoxLayout()
            crf_row.setSpacing(6)
            crf_slider = QSlider(Qt.Orientation.Horizontal)
            crf_slider.setRange(0, 51)
            crf_slider.setValue(saved_crf)
            self._crf_spin = QSpinBox()
            self._crf_spin.setRange(0, 51)
            self._crf_spin.setValue(saved_crf)
            self._crf_spin.setFixedWidth(48)
            self._crf_spin.setToolTip(
                "0 = lossless, 18 = visually lossless, 23 = default, 51 = worst quality"
            )
            crf_slider.valueChanged.connect(self._crf_spin.setValue)
            self._crf_spin.valueChanged.connect(crf_slider.setValue)
            crf_row.addWidget(crf_slider, 1)
            crf_row.addWidget(self._crf_spin)
            form.addRow("CRF (quality)", crf_row)

            self._preset = QComboBox()
            for p in _H264_PRESETS:
                self._preset.addItem(p, p)
            saved_preset = settings.value("codec_opts/h264_preset", "medium")
            idx = _H264_PRESETS.index(saved_preset) if saved_preset in _H264_PRESETS else 5
            self._preset.setCurrentIndex(idx)
            self._preset.setToolTip("Slower = better compression at same quality")
            form.addRow("Preset", self._preset)

        elif codec_key == "prores":
            self._prores_profile = QComboBox()
            for val, label in _PRORES_PROFILES:
                self._prores_profile.addItem(label, val)
            saved_prof = settings.value("codec_opts/prores_profile", "3")
            for i in range(self._prores_profile.count()):
                if self._prores_profile.itemData(i) == saved_prof:
                    self._prores_profile.setCurrentIndex(i)
                    break
            form.addRow("Profile", self._prores_profile)

        elif codec_key == "prores_4444":
            self._prores_profile = QComboBox()
            for val, label in _PRORES_4444_PROFILES:
                self._prores_profile.addItem(label, val)
            saved_prof = settings.value("codec_opts/prores4444_profile", "4")
            for i in range(self._prores_profile.count()):
                if self._prores_profile.itemData(i) == saved_prof:
                    self._prores_profile.setCurrentIndex(i)
                    break
            form.addRow("Profile", self._prores_profile)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self) -> dict[str, str]:
        """Return dict of option key -> value strings for PyAV stream.options."""
        result: dict[str, str] = {}
        if self._crf_spin is not None:
            result["crf"] = str(self._crf_spin.value())
        if self._preset is not None:
            result["preset"] = self._preset.currentData() or "medium"
        if self._prores_profile is not None:
            result["profile"] = self._prores_profile.currentData() or "3"
            result["vendor"] = "apl0"
        return result

    def accept(self) -> None:
        if self._codec_key == "h264":
            if self._crf_spin:
                self._settings.setValue("codec_opts/h264_crf", self._crf_spin.value())
            if self._preset:
                self._settings.setValue(
                    "codec_opts/h264_preset",
                    self._preset.currentData(),
                )
        elif self._codec_key == "prores" and self._prores_profile:
            self._settings.setValue(
                "codec_opts/prores_profile",
                self._prores_profile.currentData(),
            )
        elif self._codec_key == "prores_4444" and self._prores_profile:
            self._settings.setValue(
                "codec_opts/prores4444_profile",
                self._prores_profile.currentData(),
            )
        super().accept()


# ---------------------------------------------------------------------------
# Conversion tab
# ---------------------------------------------------------------------------

class ConvertTab(QWidget):
    log_message = Signal(str)

    def __init__(self, mode: str, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._mode = mode
        self._settings = settings
        self._ocio_cfg: object | None = None
        self._input_seq: object | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # -- Input + src colorspace inline --
        in_group = QGroupBox("Input")
        in_main = QVBoxLayout(in_group)
        in_main.setSpacing(4)

        in_row = QHBoxLayout()
        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText(
            "Video file (mp4, mov, mkv, \u2026)"
            if mode == "video2exr"
            else "Folder with EXRs, or any .exr from the sequence"
        )
        saved_in = settings.value(f"{mode}/input", "")
        if saved_in:
            self.input_path.setText(saved_in)
        self._browse_in = QPushButton("Browse\u2026")
        in_row.addWidget(self.input_path, 1)
        in_row.addWidget(self._browse_in)
        in_main.addLayout(in_row)

        cs_in_row = QHBoxLayout()
        cs_in_row.addWidget(QLabel("Color space:"))
        self.src_btn = ColorSpaceButton()
        cs_in_row.addWidget(self.src_btn, 1)
        in_main.addLayout(cs_in_row)

        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Frames:"))
        self._frame_range_edit = QLineEdit()
        self._frame_range_edit.setPlaceholderText("e.g. 1001-1100, 1-50x2")
        self._frame_range_edit.setToolTip(
            "Nuke-style frame range.\nExamples: 1-100, 1-10x2, 1-4 8-10"
        )
        self._frame_range_edit.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"(\d+(-\d+)?(x\d+)?([, ] *)?)*"),
                self._frame_range_edit,
            )
        )
        range_row.addWidget(self._frame_range_edit, 1)

        self._reset_range_btn = QToolButton()
        self._reset_range_btn.setText("\u21ba")
        self._reset_range_btn.setToolTip("Reset to source range")
        self._reset_range_btn.setEnabled(False)
        self._reset_range_btn.clicked.connect(self._reset_to_source_range)
        range_row.addWidget(self._reset_range_btn)

        in_main.addLayout(range_row)
        self._full_input_range = ""

        layout.addWidget(in_group)

        # -- Output + dst colorspace inline --
        out_group = QGroupBox("Output")
        out_main = QVBoxLayout(out_group)
        out_main.setSpacing(4)

        out_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText(
            f"Output directory for EXR sequence (name.{'#' * DEFAULT_FRAME_PADDING}.exr)"
            if mode == "video2exr"
            else "Output video file (mp4, mov, \u2026)"
        )
        saved_out = settings.value(f"{mode}/output", "")
        if saved_out:
            self.output_path.setText(saved_out)
        self._browse_out = QPushButton("Browse\u2026")
        out_row.addWidget(self.output_path, 1)
        out_row.addWidget(self._browse_out)
        out_main.addLayout(out_row)

        cs_out_row = QHBoxLayout()
        cs_out_row.addWidget(QLabel("Color space:"))
        self.dst_btn = ColorSpaceButton()
        cs_out_row.addWidget(self.dst_btn, 1)
        out_main.addLayout(cs_out_row)

        layout.addWidget(out_group)

        # -- Options row: scale + mode-specific in one group --
        opts_group = QGroupBox("Options")
        opts_layout = QFormLayout(opts_group)
        opts_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.scale_combo = QComboBox()
        for scale_val, scale_label in SCALE_OPTIONS:
            self.scale_combo.addItem(scale_label, scale_val)
        saved_scale = float(settings.value(f"{mode}/scale", DEFAULT_SCALE))
        for i in range(self.scale_combo.count()):
            if abs(self.scale_combo.itemData(i) - saved_scale) < 0.01:
                self.scale_combo.setCurrentIndex(i)
                break
        self.scale_combo.currentIndexChanged.connect(
            lambda _: self._settings.setValue(f"{self._mode}/scale", self.scale_combo.currentData())
        )

        self._slate_check = QCheckBox("Prepend Slate")
        self._slate_check.setToolTip("Add a 1-frame slate image before the converted output")
        self._slate_check.setChecked(bool(settings.value(f"{mode}/slate_enabled", False)))

        scale_row = QHBoxLayout()
        scale_row.setSpacing(12)
        scale_row.addWidget(self.scale_combo, 1)
        scale_row.addWidget(self._slate_check)
        opts_layout.addRow("Scale", scale_row)

        if mode == "video2exr":
            self.compression_combo = QComboBox()
            for c in EXR_COMPRESSIONS:
                self.compression_combo.addItem(c.upper(), c)
            saved_comp = settings.value(f"{mode}/exr_compression", DEFAULT_EXR_COMPRESSION)
            idx = EXR_COMPRESSIONS.index(saved_comp) if saved_comp in EXR_COMPRESSIONS else 0
            self.compression_combo.setCurrentIndex(idx)
            self.compression_combo.currentIndexChanged.connect(
                lambda _: self._settings.setValue(
                    f"{self._mode}/exr_compression",
                    self.compression_combo.currentData(),
                )
            )
            comp_row = QHBoxLayout()
            comp_row.setSpacing(4)
            comp_row.addWidget(self.compression_combo, 1)
            self._comp_settings_btn = QPushButton("\U0001f527")
            self._comp_settings_btn.setObjectName("gearBtn")
            self._comp_settings_btn.setFixedWidth(28)
            self._comp_settings_btn.setToolTip("Compression settings\u2026")
            self._comp_settings_btn.clicked.connect(self._open_compression_settings)
            self._update_comp_btn_state()
            self.compression_combo.currentIndexChanged.connect(
                lambda _: self._update_comp_btn_state()
            )
            comp_row.addWidget(self._comp_settings_btn)
            opts_layout.addRow("EXR Compression", comp_row)

            self.padding_spin = QSpinBox()
            self.padding_spin.setRange(1, 8)
            self.padding_spin.setValue(
                int(settings.value(f"{mode}/padding", DEFAULT_FRAME_PADDING))
            )
            self.padding_spin.setToolTip("Number of # digits in the frame number (e.g. #### = 4)")
            self.padding_spin.valueChanged.connect(
                lambda v: self._settings.setValue(f"{self._mode}/padding", v)
            )
            self.padding_spin.valueChanged.connect(lambda _: self._update_output_placeholder())

            self.start_frame_spin = QSpinBox()
            self.start_frame_spin.setRange(0, 999999)
            self.start_frame_spin.setValue(
                int(settings.value(f"{mode}/start_frame", DEFAULT_START_FRAME))
            )
            self.start_frame_spin.setToolTip("First frame number in the output sequence")
            self.start_frame_spin.valueChanged.connect(
                lambda v: self._settings.setValue(f"{self._mode}/start_frame", v)
            )

            frame_row = QHBoxLayout()
            frame_row.setSpacing(8)
            frame_row.addWidget(QLabel("Padding"))
            frame_row.addWidget(self.padding_spin)
            frame_row.addSpacing(12)
            frame_row.addWidget(QLabel("Start frame"))
            frame_row.addWidget(self.start_frame_spin)
            frame_row.addStretch()
            opts_layout.addRow("Frame numbering", frame_row)

            self.fps_widget = None
            self.codec_combo = None
        elif mode == "exr2video":
            self.compression_combo = None
            self.padding_spin = None
            self.start_frame_spin = None
            self.fps_widget = FpsCombo(settings, f"{mode}/fps")
            opts_layout.addRow("Frame rate", self.fps_widget)

            self.codec_combo = QComboBox()
            for key, display, _codec, _pix in VIDEO_CODECS:
                self.codec_combo.addItem(display, key)
            saved_codec = settings.value(f"{mode}/video_codec", DEFAULT_VIDEO_CODEC)
            for i in range(self.codec_combo.count()):
                if self.codec_combo.itemData(i) == saved_codec:
                    self.codec_combo.setCurrentIndex(i)
                    break
            self.codec_combo.currentIndexChanged.connect(
                lambda _: self._settings.setValue(
                    f"{self._mode}/video_codec",
                    self.codec_combo.currentData(),
                )
            )
            codec_row = QHBoxLayout()
            codec_row.setSpacing(4)
            codec_row.addWidget(self.codec_combo, 1)
            self._codec_settings_btn = QPushButton("\U0001f527")
            self._codec_settings_btn.setObjectName("gearBtn")
            self._codec_settings_btn.setFixedWidth(28)
            self._codec_settings_btn.setToolTip("Codec settings\u2026")
            self._codec_settings_btn.clicked.connect(self._open_codec_settings)
            self._update_codec_btn_state()
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_codec_btn_state())
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_output_ext())
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_dst_for_codec())
            codec_row.addWidget(self._codec_settings_btn)
            opts_layout.addRow("Codec", codec_row)
        else:
            self.compression_combo = None
            self.padding_spin = None
            self.start_frame_spin = None
            self.fps_widget = None
            self.codec_combo = None

        layout.addWidget(opts_group)

        self._slate_data: dict | None = None
        self._slate_thumbnail_b64: str = ""
        self._slate_check.toggled.connect(self._on_slate_toggled)

        layout.addStretch()

        # -- Tab order --
        tab_chain = [
            self.input_path,
            self._browse_in,
            self.src_btn,
            self._frame_range_edit,
            self._reset_range_btn,
            self.output_path,
            self._browse_out,
            self.dst_btn,
            self.scale_combo,
        ]
        if mode == "video2exr":
            tab_chain += [
                self.compression_combo,
                self._comp_settings_btn,
                self.padding_spin,
                self.start_frame_spin,
            ]
        elif mode == "exr2video":
            if self.fps_widget:
                tab_chain.append(self.fps_widget)
            tab_chain += [self.codec_combo, self._codec_settings_btn]
        for i in range(len(tab_chain) - 1):
            if tab_chain[i] and tab_chain[i + 1]:
                self.setTabOrder(tab_chain[i], tab_chain[i + 1])

        # -- Connections --
        self._browse_in.clicked.connect(self._pick_input)
        self._browse_out.clicked.connect(self._pick_output)
        self.input_path.textChanged.connect(
            lambda t: self._settings.setValue(f"{self._mode}/input", t)
        )
        self.output_path.textChanged.connect(
            lambda t: self._settings.setValue(f"{self._mode}/output", t)
        )
        self.src_btn.space_changed.connect(
            lambda n: self._settings.setValue(f"{self._mode}/src_space", n)
        )
        self.dst_btn.space_changed.connect(
            lambda n: self._settings.setValue(f"{self._mode}/dst_space", n)
        )

    def populate_spaces(
        self,
        families: dict[str, list[str]],
        ocio_cfg: object | None = None,
    ) -> None:
        self._ocio_cfg = ocio_cfg
        if self._mode == "video2exr":
            default_src, default_dst = DEFAULT_SRC_V2E, DEFAULT_DST_V2E
        else:
            default_src, default_dst = DEFAULT_SRC_E2V, DEFAULT_DST_E2V
        saved_src = self._settings.value(f"{self._mode}/src_space", default_src)
        saved_dst = self._settings.value(f"{self._mode}/dst_space", default_dst)
        self.src_btn.populate(families, saved_src)
        self.dst_btn.populate(families, saved_dst)

    def get_fps(self) -> float:
        if self.fps_widget:
            return self.fps_widget.fps()
        return 24.0

    def get_compression(self) -> str:
        if self.compression_combo:
            return self.compression_combo.currentData() or DEFAULT_EXR_COMPRESSION
        return DEFAULT_EXR_COMPRESSION

    def get_scale(self) -> float:
        return float(self.scale_combo.currentData() or DEFAULT_SCALE)

    def get_padding(self) -> int:
        if self.padding_spin:
            return self.padding_spin.value()
        return DEFAULT_FRAME_PADDING

    def get_start_frame(self) -> int:
        if self.start_frame_spin:
            return self.start_frame_spin.value()
        return DEFAULT_START_FRAME

    def get_video_codec_info(self) -> tuple[str, str, str]:
        """Return (key, libav_codec, pix_fmt) for the selected video codec."""
        if not self.codec_combo:
            return ("h264", "libx264", "yuv420p")
        key = self.codec_combo.currentData() or DEFAULT_VIDEO_CODEC
        for k, _display, codec, pix in VIDEO_CODECS:
            if k == key:
                return (k, codec, pix)
        return ("h264", "libx264", "yuv420p")

    def get_exr_opts(self) -> dict[str, str]:
        """Return saved EXR compression options."""
        comp = self.get_compression()
        result: dict[str, str] = {}
        if comp in ("dwaa", "dwab"):
            level = float(self._settings.value("exr_opts/dwa_level", 45.0))
            result["dwa_compression_level"] = str(level)
        elif comp in ("zip", "zips"):
            level = int(self._settings.value("exr_opts/zip_level", 4))
            result["zip_level"] = str(level)
        return result

    def get_codec_opts(self) -> dict[str, str]:
        """Return saved video codec options for PyAV stream.options."""
        key = self.get_video_codec_info()[0]
        if key == "h264":
            crf = str(int(self._settings.value("codec_opts/h264_crf", 18)))
            preset = self._settings.value("codec_opts/h264_preset", "medium")
            return {"crf": crf, "preset": preset}
        if key == "prores":
            prof = self._settings.value("codec_opts/prores_profile", "3")
            return {"profile": prof, "vendor": "apl0"}
        if key == "prores_4444":
            prof = self._settings.value("codec_opts/prores4444_profile", "4")
            return {"profile": prof, "vendor": "apl0"}
        if key.startswith("dnxhr"):
            profile = "dnxhr_hq" if key == "dnxhr_hq" else "dnxhr_hqx"
            return {"profile": profile}
        if key == "ffv1":
            return {"slicecrc": "1"}
        return {}

    def slate_enabled(self) -> bool:
        return self._slate_check.isChecked()

    def get_slate_data(self) -> dict | None:
        """Return the last-edited slate data, or None if slate is disabled."""
        if not self._slate_check.isChecked():
            return None
        return self._slate_data

    def get_slate_thumbnail_b64(self) -> str:
        """Return the base64-encoded thumbnail for the slate, or ''."""
        if not self._slate_check.isChecked():
            return ""
        return self._slate_thumbnail_b64

    def get_slate_resolution(self) -> tuple[int, int] | None:
        """Return the slate resolution if slate is enabled."""
        if not self._slate_check.isChecked() or self._slate_data is None:
            return None
        res_str = self._slate_data.get("resolution", "")
        if "\u00d7" in res_str:
            parts = res_str.split("\u00d7")
            try:
                return int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass
        return None

    def _on_slate_toggled(self, checked: bool) -> None:
        self._settings.setValue(f"{self._mode}/slate_enabled", checked)
        if checked and self._slate_data is None:
            self._open_slate_dialog()

    def _open_slate_dialog(self) -> None:
        from .slate_widgets import SlateDialog

        locked_w, locked_h = self._detect_input_resolution()
        inp = self.get_input_path()
        inferred_fps = self._infer_fps_from_input()
        dst_cs = self.dst_btn.current_space()
        dlg = SlateDialog(
            self._settings,
            locked_width=locked_w,
            locked_height=locked_h,
            input_path=inp,
            mode=self._mode,
            inferred_fps=inferred_fps,
            frame_range=self._full_input_range,
            dst_colorspace=dst_cs,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._slate_data = dlg.slate_data()
            self._slate_thumbnail_b64 = dlg.thumbnail_b64()
            self.log_message.emit("Slate data updated")

    def _infer_fps_from_input(self) -> float:
        """Probe the input to infer frame rate. Returns 0.0 if unavailable."""
        inp = self.get_input_path()
        if not inp:
            return 0.0
        try:
            if self._mode == "video2exr":
                from .video import probe_video

                _w, _h, fps, _total = probe_video(inp)
                return fps
        except Exception:
            pass
        return 0.0

    def _detect_input_resolution(self) -> tuple[int, int]:
        """Probe the input to determine resolution for the slate.

        Returns (0, 0) if no input is set or probing fails.
        """
        inp = self.get_input_path()
        if not inp:
            return 0, 0
        try:
            if self._mode == "video2exr":
                from .video import probe_video

                w, h, _fps, _total = probe_video(inp)
                return w, h
            else:
                import OpenImageIO as oiio

                from .sequence import find_exr_sequence_info

                _paths, _seq_name, _frames, _pad_len, seq = find_exr_sequence_info(inp)
                first_frame = sorted(seq.frameSet())[0]
                first_path = seq.frame(first_frame)
                inp_img = oiio.ImageInput.open(first_path)
                if inp_img:
                    spec = inp_img.spec()
                    w = spec.full_width if spec.full_width > 0 else spec.width
                    h = spec.full_height if spec.full_height > 0 else spec.height
                    inp_img.close()
                    return w, h
        except Exception:
            pass
        return 0, 0

    def _update_comp_btn_state(self) -> None:
        comp = self.get_compression()
        self._comp_settings_btn.setVisible(comp in _EXR_HAS_SETTINGS)

    def _update_codec_btn_state(self) -> None:
        key = self.get_video_codec_info()[0]
        self._codec_settings_btn.setVisible(key in _CODEC_HAS_SETTINGS)

    def _open_compression_settings(self) -> None:
        comp = self.get_compression()
        dlg = ExrCompressionSettingsDialog(comp, self._settings, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            opts = dlg.get_settings()
            if opts:
                parts = [f"{k}={v}" for k, v in opts.items()]
                self.log_message.emit(f"EXR compression ({comp.upper()}): {', '.join(parts)}")
            else:
                self.log_message.emit(f"EXR compression ({comp.upper()}): default settings")

    def _open_codec_settings(self) -> None:
        key = self.get_video_codec_info()[0]
        dlg = VideoCodecSettingsDialog(key, self._settings, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            opts = dlg.get_settings()
            if opts:
                parts = [f"{k}={v}" for k, v in opts.items()]
                self.log_message.emit(f"Codec ({key}): {', '.join(parts)}")
            else:
                self.log_message.emit(f"Codec ({key}): default settings")

    def _pick_input(self) -> None:
        if self._mode == "video2exr":
            start = self.input_path.text().strip() or str(Path.home())
            dlg = VideoBrowserDialog(start, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_path():
                video_path = dlg.selected_path()
                self.input_path.setText(video_path)
                self._auto_fill_exr_output(video_path)
                self._auto_detect_video_colorspace(video_path)
                self._detect_input_range()
        else:
            start = self.get_input_path() or str(Path.home())
            sel_name = ""
            if self._input_seq is not None:
                sel_name = self._input_seq.basename().rstrip("._")
            dlg = SequenceBrowserDialog(start, select_name=sel_name, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_directory():
                sel_dir = dlg.selected_directory()
                self.input_path.setText(sel_dir)
                self._auto_fill_video_output(sel_dir)
                self._auto_detect_colorspace(sel_dir)
                self._detect_input_range()

    def _pick_output(self) -> None:
        if self._mode == "video2exr":
            path = QFileDialog.getExistingDirectory(
                self,
                "Output directory",
                self.output_path.text(),
            )
        else:
            codec_key = ""
            if self.codec_combo:
                codec_key = self.codec_combo.currentData() or ""
            if codec_key in ("prores", "prores_4444"):
                filt = "Video (*.mov)"
            elif codec_key == "ffv1":
                filt = "Video (*.mkv *.avi)"
            else:
                filt = "Video (*.mp4 *.mov *.mkv)"
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save video as",
                self.output_path.text(),
                filt,
            )
        if path:
            self.output_path.setText(path)

    def handle_dropped_path(self, path: str) -> bool:
        """Accept a dropped path if valid for this tab's mode. Returns True if accepted."""
        p = Path(path)
        if self._mode == "video2exr":
            if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
                self.input_path.setText(str(p))
                self._auto_fill_exr_output(str(p))
                self._auto_detect_video_colorspace(str(p))
                self._detect_input_range()
                return True
        else:
            if p.is_dir():
                self.input_path.setText(str(p))
                self._auto_fill_video_output(str(p))
                self._auto_detect_colorspace(str(p))
                self._detect_input_range()
                return True
            if p.is_file() and p.suffix.lower() == ".exr":
                self.input_path.setText(str(p.parent))
                self._auto_fill_video_output(str(p.parent))
                self._auto_detect_colorspace(str(p.parent))
                self._detect_input_range()
                return True
        return False

    def _codec_ext(self) -> str:
        """Return the preferred file extension for the current codec."""
        codec_key = ""
        if self.codec_combo:
            codec_key = self.codec_combo.currentData() or ""
        if codec_key in ("prores", "prores_4444"):
            return ".mov"
        if codec_key == "ffv1":
            return ".mkv"
        if codec_key == "h264":
            return ".mp4"
        if codec_key == "dnxhr_hq":
            return ".mxf"
        return ".mov"

    def _auto_fill_video_output(self, exr_dir: str) -> None:
        """Set output video path to <parent>/<dirname>.<ext> if not already set."""
        if self._mode != "exr2video":
            return
        if self.output_path.text().strip():
            return
        p = Path(exr_dir)
        out = p.parent / f"{p.name}{self._codec_ext()}"
        self.output_path.setText(str(out))

    def _auto_fill_exr_output(self, video_path: str) -> None:
        """Set output EXR path to <video_parent>/<stem>/<stem>.####.exr."""
        if self._mode != "video2exr":
            return
        p = Path(video_path)
        out_dir = p.parent / p.stem
        pad = "#" * self.get_padding()
        display = str(out_dir / f"{p.stem}.{pad}.exr")
        self.output_path.setText(display)

    def _auto_detect_video_colorspace(self, video_path: str) -> None:
        """Guess the source colorspace from video codec/format and select it."""
        if self._mode != "video2exr":
            return
        from .video import guess_video_colorspace_candidates

        candidates = guess_video_colorspace_candidates(video_path)
        if not candidates:
            return
        ocio_cfg = getattr(self, "_ocio_cfg", None)
        preferred = candidates[0]
        if ocio_cfg is not None:
            from .ocio_utils import resolve_alias

            for name in candidates:
                resolved = resolve_alias(ocio_cfg, name)
                if resolved:
                    preferred = resolved
                    break
        if self.src_btn.try_select(preferred):
            self.log_message.emit(f"Auto-detected source color space: {preferred}")
            self.src_btn.setStyleSheet("background-color: #3a3020;")
            QTimer.singleShot(500, lambda: self.src_btn.setStyleSheet(""))

    def _update_output_placeholder(self) -> None:
        """Update the output placeholder and current pattern to reflect padding."""
        if self._mode != "video2exr" or not self.padding_spin:
            return
        pat = "#" * self.padding_spin.value()
        self.output_path.setPlaceholderText(f"Output EXR sequence (name.{pat}.exr)")
        import re

        current = self.output_path.text()
        if current and re.search(r"#+\.exr$", current):
            updated = re.sub(r"#+\.exr$", f"{pat}.exr", current)
            if updated != current:
                self.output_path.setText(updated)

    def _update_output_ext(self) -> None:
        """Update the output path extension to match the current codec."""
        if self._mode != "exr2video":
            return
        current = self.output_path.text().strip()
        if not current:
            return
        p = Path(current)
        new_ext = self._codec_ext()
        if p.suffix.lower() != new_ext:
            self.output_path.setText(str(p.with_suffix(new_ext)))
            self.output_path.setStyleSheet("background-color: #3a3020;")
            QTimer.singleShot(500, lambda: self.output_path.setStyleSheet(""))

    def _update_dst_for_codec(self) -> None:
        """Suggest a sensible destination colorspace for the selected codec."""
        if self._mode != "exr2video":
            return
        codec_key = self.codec_combo.currentData() if self.codec_combo else ""
        if codec_key == "ffv1":
            candidates = ["scene_linear"]
        else:
            candidates = [
                "Output - Rec.709",
                "Rec.1886 Rec.709 - Display",
            ]
        ocio_cfg = getattr(self, "_ocio_cfg", None)
        preferred = candidates[0]
        if ocio_cfg is not None:
            from .ocio_utils import resolve_alias

            for name in candidates:
                resolved = resolve_alias(ocio_cfg, name)
                if resolved:
                    preferred = resolved
                    break
        if self.dst_btn.try_select(preferred):
            self.dst_btn.setStyleSheet("background-color: #3a3020;")
            QTimer.singleShot(500, lambda: self.dst_btn.setStyleSheet(""))

    def _auto_detect_colorspace(self, exr_dir: str) -> None:
        """Probe colorspace from EXRs and select it in src_btn if found."""
        if self._mode != "exr2video":
            return
        cs = probe_exr_colorspace(exr_dir)
        if not cs:
            return
        canonical = cs
        ocio_cfg = getattr(self, "_ocio_cfg", None)
        if ocio_cfg is not None:
            from .ocio_utils import resolve_alias

            resolved = resolve_alias(ocio_cfg, cs)
            if resolved:
                canonical = resolved
        if self.src_btn.try_select(canonical):
            self.log_message.emit(f"Auto-detected source color space: {canonical}")
            self.src_btn.setStyleSheet("background-color: #3a3020;")
            QTimer.singleShot(500, lambda: self.src_btn.setStyleSheet(""))
        else:
            self.log_message.emit(f'EXR color space "{cs}" not found in current OCIO config')

    # -- Frame range --

    def _detect_input_range(self) -> None:
        """Detect the frame range of the current input and populate the field."""
        from .framerange import format_frame_range

        inp = self.get_input_path()
        if not inp:
            self._full_input_range = ""
            self._input_seq = None
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)
            return

        try:
            if self._mode == "video2exr":
                from .video import probe_video

                _w, _h, _fps, total = probe_video(inp)
                frames = list(range(1, total + 1))
                self._input_seq = None
            else:
                from .sequence import find_exr_sequence_info

                _paths, _seq_name, frames, _pad_len, seq = find_exr_sequence_info(inp)
                self._input_seq = seq
                pad = "#" * seq.zfill()
                display = f"{seq.dirname()}{seq.basename()}{pad}{seq.extension()}"
                self.input_path.blockSignals(True)
                self.input_path.setText(display)
                self.input_path.blockSignals(False)
        except Exception:
            self._full_input_range = ""
            self._input_seq = None
            self._reset_range_btn.setEnabled(False)
            return

        if not frames:
            self._full_input_range = ""
            self._reset_range_btn.setEnabled(False)
            return

        range_str = format_frame_range(frames)
        self._full_input_range = range_str
        self._frame_range_edit.setText(range_str)
        self._reset_range_btn.setEnabled(True)

    def _reset_to_source_range(self) -> None:
        """Reset the frame range field to the full source range."""
        if self._full_input_range:
            self._frame_range_edit.setText(self._full_input_range)

    def get_input_path(self) -> str:
        """Return the real filesystem path for the input.

        For EXR sequences, returns the directory (from the stored FileSequence)
        rather than the display pattern shown in the text field.
        """
        if self._mode == "exr2video" and self._input_seq is not None:
            return self._input_seq.dirname().rstrip("/")
        return self.input_path.text().strip()

    def get_output_path(self) -> str:
        """Return the real filesystem path for the output.

        For video2exr, the display shows a sequence pattern (e.g. name.####.exr)
        but the converter needs just the directory.
        """
        raw = self.output_path.text().strip()
        if self._mode == "video2exr" and raw:
            p = Path(raw)
            if "#" in p.name:
                return str(p.parent)
        return raw

    def get_frame_range(self) -> str:
        """Return the user-specified frame range string, or '' for all frames."""
        return self._frame_range_edit.text().strip()
