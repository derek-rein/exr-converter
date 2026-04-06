from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QDir, QRegularExpression, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QRegularExpressionValidator
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

    def __init__(self, start_dir: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Browse EXR Sequences")
        self.resize(960, 560)
        self._selected_dir: str = ""
        self._selected_name: str = ""
        self._seq_data: list[dict] = []

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

        # main splitter: [dir tree | sequences table | metadata]
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)

        # -- left: dir tree --
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
        self._outer_splitter.addWidget(self._tree)

        # -- center: sequence table --
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)
        center_layout.addWidget(QLabel("<b>EXR Sequences</b>"))

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

        self._status = QLabel()
        self._status.setStyleSheet(STATUS_DIM)
        center_layout.addWidget(self._status)

        self._outer_splitter.addWidget(center)

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
        self._outer_splitter.addWidget(self._meta_panel)
        self._meta_panel.setVisible(False)

        self._outer_splitter.setStretchFactor(0, 2)
        self._outer_splitter.setStretchFactor(1, 3)
        self._outer_splitter.setStretchFactor(2, 2)
        layout.addWidget(self._outer_splitter, 1)

        # buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._ok_btn.setEnabled(False)
        layout.addWidget(buttons)
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
            self._tree.scrollTo(idx)
            self._tree.expand(idx)
        self._path_edit.setText(directory)
        self._scan_directory(directory)

    def _on_tree_clicked(self, index) -> None:
        path = self._fs_model.filePath(index)
        if path:
            self._path_edit.setText(path)
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
            name_item = QTableWidgetItem(s["name"])
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

        if len(seqs) == 1:
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
        self._meta_panel.setVisible(checked)
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
        self.resize(960, 560)
        self._selected_path: str = ""
        self._file_data: list[dict[str, str]] = []

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

        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)

        # -- left: dir tree --
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
        self._outer_splitter.addWidget(self._tree)

        # -- center: video file table --
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)
        center_layout.addWidget(QLabel("<b>Video Files</b>"))

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

        self._status = QLabel()
        self._status.setStyleSheet(STATUS_DIM)
        center_layout.addWidget(self._status)

        self._outer_splitter.addWidget(center)

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
        self._outer_splitter.addWidget(self._meta_panel)
        self._meta_panel.setVisible(False)

        self._outer_splitter.setStretchFactor(0, 2)
        self._outer_splitter.setStretchFactor(1, 3)
        self._outer_splitter.setStretchFactor(2, 2)
        layout.addWidget(self._outer_splitter, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._ok_btn.setEnabled(False)
        layout.addWidget(buttons)

        self._tree.clicked.connect(self._on_tree_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellDoubleClicked.connect(lambda _r, _c: self.accept())
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._inspect_cb.toggled.connect(self._toggle_inspect)

        if start_dir:
            d = Path(start_dir)
            if d.is_file():
                d = d.parent
            if d.is_dir():
                self._navigate_to(str(d))

    def selected_path(self) -> str:
        return self._selected_path

    def _navigate_to(self, directory: str) -> None:
        idx = self._fs_model.index(directory)
        if idx.isValid():
            self._tree.setCurrentIndex(idx)
            self._tree.scrollTo(idx)
            self._tree.expand(idx)
        self._path_edit.setText(directory)
        self._scan_directory(directory)

    def _on_tree_clicked(self, index) -> None:
        path = self._fs_model.filePath(index)
        if path:
            self._path_edit.setText(path)
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

        if len(files) == 1:
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
        self._meta_panel.setVisible(checked)
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

_VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".mxf",
    ".webm",
    ".m4v",
    ".ts",
}


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
        opts_layout.addRow("Scale", self.scale_combo)

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
            dlg = SequenceBrowserDialog(start, parent=self)
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
