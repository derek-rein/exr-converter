from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QDir, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
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
    DEFAULT_SCALE,
    DEFAULT_SRC_E2V,
    DEFAULT_SRC_V2E,
    DEFAULT_VIDEO_CODEC,
    EXR_COMPRESSIONS,
    OCIO_SOURCE_ENV,
    OCIO_SOURCE_FILE,
    SCALE_OPTIONS,
    VIDEO_CODECS,
)
from .ocio_utils import list_builtin_configs, resolve_ocio_config
from .sequence import scan_exr_sequences

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
        self.setStyleSheet(
            "QToolButton { text-align: left; padding: 4px 8px; }"
            "QToolButton::menu-indicator { subcontrol-position: right center; }"
        )
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

        self._spin = QSpinBox()
        self._spin.setRange(1, 240)
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
        self._spin.setValue(int(round(saved)))
        self._spin.setVisible(True)

    def _on_combo_changed(self, idx: int) -> None:
        val = self._combo.currentData()
        is_custom = val == -1.0
        self._spin.setVisible(is_custom)
        if not is_custom:
            self._settings.setValue(self._key, val)
        else:
            self._settings.setValue(self._key, float(self._spin.value()))

    def _on_spin_changed(self, val: int) -> None:
        if self._combo.currentData() == -1.0:
            self._settings.setValue(self._key, float(val))


# ---------------------------------------------------------------------------
# OCIO config panel
# ---------------------------------------------------------------------------


class OcioConfigPanel(QGroupBox):
    """Panel for selecting OCIO config: builtin picker, $OCIO env, or file override."""

    config_changed = Signal()

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__("OpenColorIO Config", parent)
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source:"))
        self._source_combo = QComboBox()
        self._source_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_row.addWidget(self._source_combo, 1)
        layout.addLayout(source_row)

        self._file_row = QWidget()
        file_layout = QHBoxLayout(self._file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("Path to .ocio config file")
        saved_file = settings.value("ocio/file_path", "")
        if saved_file:
            self._file_edit.setText(saved_file)
        self._browse_btn = QPushButton("Browse\u2026")
        file_layout.addWidget(self._file_edit, 1)
        file_layout.addWidget(self._browse_btn)
        self._file_row.setVisible(False)
        layout.addWidget(self._file_row)

        self._status = QLabel()
        self._status.setStyleSheet("font-size: 11px; padding: 2px 0;")
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
        self._source_combo.addItem("Custom config file\u2026", OCIO_SOURCE_FILE)

        self._select_saved_source()

        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        self._browse_btn.clicked.connect(self._pick_file)
        self._file_edit.editingFinished.connect(self._on_file_edited)

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
        file_path = self._file_edit.text().strip()
        try:
            cfg = resolve_ocio_config(source, file_path=file_path)
        except Exception as e:
            self._status.setText(f"\u2718  {e}")
            self._status.setStyleSheet("color: #c44; font-size: 11px; padding: 2px 0;")
            return None

        n = len(list(cfg.getColorSpaceNames()))
        if source == OCIO_SOURCE_ENV:
            desc = f"$OCIO: {os.environ.get('OCIO', '?')}"
        elif source == OCIO_SOURCE_FILE:
            desc = f"File: {Path(file_path).name}"
        else:
            desc = source
        self._status.setText(f"\u2714  {desc}  ({n} color spaces)")
        self._status.setStyleSheet("color: #4a4; font-size: 11px; padding: 2px 0;")
        return cfg

    def _on_source_changed(self, _idx: int) -> None:
        source = self.current_source_key()
        self._file_row.setVisible(source == OCIO_SOURCE_FILE)
        self._settings.setValue("ocio/source", source)
        self.config_changed.emit()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "OCIO config file",
            self._file_edit.text(),
            "OCIO (*.ocio);;All (*.*)",
        )
        if path:
            self._file_edit.setText(path)
            self._settings.setValue("ocio/file_path", path)
            self.config_changed.emit()

    def _on_file_edited(self) -> None:
        self._settings.setValue("ocio/file_path", self._file_edit.text().strip())
        self.config_changed.emit()


# ---------------------------------------------------------------------------
# EXR sequence browser dialog
# ---------------------------------------------------------------------------


class SequenceBrowserDialog(QDialog):
    """Integrated directory browser + EXR sequence table in a single dialog.

    Left pane: directory tree (folders only).
    Right pane: table of detected EXR sequences with Name, Frames, Range,
    and Resolution columns.
    """

    _COLUMNS = ["Name", "Frames", "Range", "Resolution"]

    def __init__(self, start_dir: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Browse EXR Sequences")
        self.resize(840, 520)
        self._selected_dir: str = ""
        self._selected_name: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Folder:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Navigate in the tree or paste a path here")
        path_row.addWidget(self._path_edit, 1)
        layout.addLayout(path_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath(QDir.rootPath())
        self._fs_model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)

        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setHeaderHidden(True)
        for col in range(1, self._fs_model.columnCount()):
            self._tree.hideColumn(col)
        self._tree.setMinimumWidth(240)
        tree_header = self._tree.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self._tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        right_layout.addWidget(QLabel("<b>EXR Sequences</b>"))

        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumWidth(380)
        th = self._table.horizontalHeader()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self._COLUMNS)):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        right_layout.addWidget(self._table, 1)

        self._status = QLabel()
        self._status.setStyleSheet("font-size: 11px; color: #888;")
        right_layout.addWidget(self._status)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        layout.addWidget(buttons)

        self._tree.clicked.connect(self._on_tree_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellDoubleClicked.connect(lambda _r, _c: self.accept())
        self._path_edit.returnPressed.connect(self._on_path_entered)

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
        self._ok_btn.setEnabled(False)

        try:
            seqs = scan_exr_sequences(directory)
        except Exception as e:
            self._status.setText(f"Error: {e}")
            return

        if not seqs:
            self._status.setText("No EXR sequences in this folder.")
            return

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

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, frames_item)
            self._table.setItem(row, 2, range_item)
            self._table.setItem(row, 3, res_item)

        if len(seqs) == 1:
            self._table.selectRow(0)

        self._status.setText(f"{len(seqs)} sequence(s) found")

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            item = self._table.item(rows[0].row(), 0)
            self._selected_name = item.data(Qt.ItemDataRole.UserRole) if item else ""
            self._ok_btn.setEnabled(bool(self._selected_name))
        else:
            self._selected_name = ""
            self._ok_btn.setEnabled(False)


# ---------------------------------------------------------------------------
# Conversion tab
# ---------------------------------------------------------------------------


class ConvertTab(QWidget):
    def __init__(self, mode: str, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._mode = mode
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # -- Input --
        in_group = QGroupBox("Input")
        in_layout = QHBoxLayout(in_group)
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
        in_layout.addWidget(self.input_path, 1)
        in_layout.addWidget(self._browse_in)
        layout.addWidget(in_group)

        # -- Output --
        out_group = QGroupBox("Output")
        out_layout = QHBoxLayout(out_group)
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText(
            "Output directory for EXR sequence"
            if mode == "video2exr"
            else "Output video file (mp4, mov, \u2026)"
        )
        saved_out = settings.value(f"{mode}/output", "")
        if saved_out:
            self.output_path.setText(saved_out)
        self._browse_out = QPushButton("Browse\u2026")
        out_layout.addWidget(self.output_path, 1)
        out_layout.addWidget(self._browse_out)
        layout.addWidget(out_group)

        # -- Color spaces --
        cs_group = QGroupBox("Color Space Transform")
        cs_layout = QFormLayout(cs_group)
        cs_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.src_btn = ColorSpaceButton()
        self.dst_btn = ColorSpaceButton()
        cs_layout.addRow("Source", self.src_btn)
        cs_layout.addRow("Destination", self.dst_btn)
        layout.addWidget(cs_group)

        # -- Scale --
        scale_group = QGroupBox("Scale")
        scale_layout = QFormLayout(scale_group)
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
        scale_layout.addRow("Output resolution", self.scale_combo)
        layout.addWidget(scale_group)

        # -- Mode-specific options --
        if mode == "video2exr":
            exr_group = QGroupBox("EXR Options")
            exr_layout = QFormLayout(exr_group)
            self.compression_combo = QComboBox()
            for c in EXR_COMPRESSIONS:
                self.compression_combo.addItem(c.upper(), c)
            saved_comp = settings.value(f"{mode}/exr_compression", DEFAULT_EXR_COMPRESSION)
            idx = EXR_COMPRESSIONS.index(saved_comp) if saved_comp in EXR_COMPRESSIONS else 0
            self.compression_combo.setCurrentIndex(idx)
            self.compression_combo.currentIndexChanged.connect(
                lambda _: self._settings.setValue(
                    f"{self._mode}/exr_compression", self.compression_combo.currentData()
                )
            )
            exr_layout.addRow("Compression", self.compression_combo)
            layout.addWidget(exr_group)
            self.fps_widget = None
            self.codec_combo = None
        elif mode == "exr2video":
            self.compression_combo = None
            opts_group = QGroupBox("Video Options")
            opts_layout = QFormLayout(opts_group)
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
                    f"{self._mode}/video_codec", self.codec_combo.currentData()
                )
            )
            opts_layout.addRow("Codec", self.codec_combo)
            layout.addWidget(opts_group)
        else:
            self.compression_combo = None
            self.fps_widget = None
            self.codec_combo = None

        layout.addStretch()

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

    def populate_spaces(self, families: dict[str, list[str]]) -> None:
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

    def get_video_codec_info(self) -> tuple[str, str, str]:
        """Return (key, libav_codec, pix_fmt) for the selected video codec."""
        if not self.codec_combo:
            return ("h264", "libx264", "yuv420p")
        key = self.codec_combo.currentData() or DEFAULT_VIDEO_CODEC
        for k, _display, codec, pix in VIDEO_CODECS:
            if k == key:
                return (k, codec, pix)
        return ("h264", "libx264", "yuv420p")

    def _pick_input(self) -> None:
        if self._mode == "video2exr":
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select video file",
                self.input_path.text(),
                "Video (*.mp4 *.mov *.mkv *.avi *.mxf *.webm);;All (*.*)",
            )
            if path:
                self.input_path.setText(path)
        else:
            start = self.input_path.text().strip() or str(Path.home())
            dlg = SequenceBrowserDialog(start, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_directory():
                self.input_path.setText(dlg.selected_directory())

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
