from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    BIT_DEPTHS,
    COLORSPACES,
    COMMON_FPS,
    DEFAULT_FPS,
    RESOLUTIONS,
)


class FpsCombo(QWidget):
    """FPS selector with common presets and a custom spin box."""

    CUSTOM_LABEL = "Custom\u2026"
    fps_changed = Signal(float)

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings

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

        saved = float(settings.value("fps", DEFAULT_FPS))
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

    def _on_combo_changed(self, _idx: int) -> None:
        val = self._combo.currentData()
        is_custom = val == -1.0
        self._spin.setVisible(is_custom)
        fps = float(self._spin.value()) if is_custom else float(val)
        self._settings.setValue("fps", fps)
        self.fps_changed.emit(fps)

    def _on_spin_changed(self, val: int) -> None:
        if self._combo.currentData() == -1.0:
            self._settings.setValue("fps", float(val))
            self.fps_changed.emit(float(val))


class SlateFormPanel(QWidget):
    """Left-side form collecting all slate metadata."""

    data_changed = Signal(dict)

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # --- Key fields (exposed in top bar via MainWindow) ---
        self.project_edit = self._line("project", "Show / Project codename")
        self.sequence_edit = self._line("sequence", "SEQ010")
        self.shot_edit = self._line("shot", "SHOT_010")
        self.version_edit = self._line("version", "v001")
        self.take_edit = self._line("take", "01")

        # --- Shot details ---
        details_group = QGroupBox("Shot Details")
        details_layout = QFormLayout(details_group)
        details_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.submit_for_combo = QComboBox()
        for label in ("WIP", "FINAL", "CBB"):
            self.submit_for_combo.addItem(label)
        saved_sf = settings.value("submit_for", "WIP")
        sf_idx = self.submit_for_combo.findText(saved_sf)
        if sf_idx >= 0:
            self.submit_for_combo.setCurrentIndex(sf_idx)

        self.artist_edit = self._line("artist", "Artist Name")
        self.vendor_edit = self._line("vendor", "Studio / Vendor name")
        self.shot_types_edit = self._line("shot_types", "2d comp, 3d, matte paint…")
        self.scope_edit = self._line("scope", "VFX scope of work")
        self.logo_edit = self._line("logo", "STUDIO")

        details_layout.addRow("Submitting For", self.submit_for_combo)
        details_layout.addRow("Artist", self.artist_edit)
        details_layout.addRow("Vendor", self.vendor_edit)
        details_layout.addRow("Shot Types", self.shot_types_edit)
        details_layout.addRow("Scope of Work", self.scope_edit)
        details_layout.addRow("Logo / Studio", self.logo_edit)
        root.addWidget(details_group)

        # --- Delivery info (metadata shown on slate) ---
        delivery_group = QGroupBox("Delivery Info")
        delivery_layout = QFormLayout(delivery_group)
        delivery_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.frame_range_edit = self._line("frame_range", "1001 – 1100")
        self.fps_widget = FpsCombo(settings)

        delivery_layout.addRow("Frame Range", self.frame_range_edit)
        delivery_layout.addRow("FPS", self.fps_widget)
        root.addWidget(delivery_group)

        # --- Output ---
        out_group = QGroupBox("Output")
        out_layout = QFormLayout(out_group)
        out_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.res_combo = QComboBox()
        for label in RESOLUTIONS:
            self.res_combo.addItem(label)
        saved_res = settings.value("resolution", list(RESOLUTIONS.keys())[0])
        idx = self.res_combo.findText(saved_res)
        if idx >= 0:
            self.res_combo.setCurrentIndex(idx)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(64, 16384)
        self.width_spin.setSuffix(" px")
        self.height_spin = QSpinBox()
        self.height_spin.setRange(64, 16384)
        self.height_spin.setSuffix(" px")

        saved_w = int(settings.value("res_w", 1920))
        saved_h = int(settings.value("res_h", 1080))
        if idx >= 0:
            preset_w, preset_h = RESOLUTIONS[saved_res]
            self.width_spin.setValue(preset_w)
            self.height_spin.setValue(preset_h)
        else:
            self.width_spin.setValue(saved_w)
            self.height_spin.setValue(saved_h)

        res_size_row = QHBoxLayout()
        res_size_row.setContentsMargins(0, 0, 0, 0)
        res_size_row.addWidget(self.width_spin)
        res_size_row.addWidget(QLabel("\u00d7"))
        res_size_row.addWidget(self.height_spin)
        self._res_size_widget = QWidget()
        self._res_size_widget.setLayout(res_size_row)

        self.depth_combo = QComboBox()
        for d in BIT_DEPTHS:
            self.depth_combo.addItem("16-bit half" if d == "half" else "32-bit float", d)
        saved_depth = settings.value("bit_depth", "half")
        depth_idx = next(
            (
                i
                for i in range(self.depth_combo.count())
                if self.depth_combo.itemData(i) == saved_depth
            ),
            0,
        )
        self.depth_combo.setCurrentIndex(depth_idx)

        self.cs_combo = QComboBox()
        for cs in COLORSPACES:
            self.cs_combo.addItem(cs)
        saved_cs = settings.value("colorspace", "Linear")
        cs_idx = self.cs_combo.findText(saved_cs)
        if cs_idx >= 0:
            self.cs_combo.setCurrentIndex(cs_idx)

        file_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("slate.exr")
        saved_path = settings.value("output_path", "")
        if saved_path:
            self.output_path.setText(saved_path)
        self._browse_btn = QPushButton("Browse\u2026")
        file_row.addWidget(self.output_path, 1)
        file_row.addWidget(self._browse_btn)

        out_layout.addRow("Preset", self.res_combo)
        out_layout.addRow("Resolution", self._res_size_widget)
        out_layout.addRow("Bit Depth", self.depth_combo)
        out_layout.addRow("Color Space", self.cs_combo)
        out_layout.addRow("Output File", file_row)
        root.addWidget(out_group)

        # --- Notes ---
        notes_group = QGroupBox("Notes")
        notes_layout = QVBoxLayout(notes_group)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setPlaceholderText("Optional notes\u2026")
        notes_layout.addWidget(self.notes_edit)
        root.addWidget(notes_group)

        root.addStretch()

        # --- Connections ---
        self._browse_btn.clicked.connect(self._pick_file)

        for widget in (
            self.project_edit,
            self.sequence_edit,
            self.shot_edit,
            self.version_edit,
            self.take_edit,
            self.artist_edit,
            self.vendor_edit,
            self.shot_types_edit,
            self.scope_edit,
            self.logo_edit,
            self.frame_range_edit,
            self.output_path,
        ):
            widget.textChanged.connect(self._emit_changed)

        self.submit_for_combo.currentIndexChanged.connect(self._emit_changed)
        self.fps_widget.fps_changed.connect(self._emit_changed)
        self.res_combo.currentTextChanged.connect(self._on_preset_changed)
        self.width_spin.valueChanged.connect(self._emit_changed)
        self.height_spin.valueChanged.connect(self._emit_changed)
        self.depth_combo.currentIndexChanged.connect(self._emit_changed)
        self.cs_combo.currentIndexChanged.connect(self._emit_changed)
        self.notes_edit.textChanged.connect(self._emit_changed)

    # --- Helpers ---

    def _line(self, key: str, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        saved = self._settings.value(key, "")
        if saved:
            edit.setText(saved)
        return edit

    def _pick_file(self) -> None:
        current = self.output_path.text().strip()
        start_dir = str(Path(current).parent) if current else ""
        path, _ = QFileDialog.getSaveFileName(self, "Save EXR", start_dir, "OpenEXR (*.exr)")
        if path:
            if not path.lower().endswith(".exr"):
                path += ".exr"
            self.output_path.setText(path)
            self._settings.setValue("output_path", path)

    def _on_preset_changed(self, text: str) -> None:
        if text in RESOLUTIONS:
            w, h = RESOLUTIONS[text]
            self.width_spin.setValue(w)
            self.height_spin.setValue(h)
        self._settings.setValue("resolution", text)

    def resolution(self) -> tuple[int, int]:
        return self.width_spin.value(), self.height_spin.value()

    def _save_fields(self) -> None:
        s = self._settings
        s.setValue("project", self.project_edit.text())
        s.setValue("sequence", self.sequence_edit.text())
        s.setValue("shot", self.shot_edit.text())
        s.setValue("version", self.version_edit.text())
        s.setValue("take", self.take_edit.text())
        s.setValue("submit_for", self.submit_for_combo.currentText())
        s.setValue("artist", self.artist_edit.text())
        s.setValue("vendor", self.vendor_edit.text())
        s.setValue("shot_types", self.shot_types_edit.text())
        s.setValue("scope", self.scope_edit.text())
        s.setValue("logo", self.logo_edit.text())
        s.setValue("frame_range", self.frame_range_edit.text())
        s.setValue("res_w", self.width_spin.value())
        s.setValue("res_h", self.height_spin.value())
        s.setValue("bit_depth", self.depth_combo.currentData())
        s.setValue("colorspace", self.cs_combo.currentText())
        s.setValue("output_path", self.output_path.text())

    def _emit_changed(self, *_args) -> None:
        self._save_fields()
        self.data_changed.emit(self.slate_data())

    def slate_data(self) -> dict:
        """Return a dict suitable for passing to the JS updateSlate() function."""
        w, h = self.resolution()
        fps = self.fps_widget.fps()
        return {
            "project": self.project_edit.text() or "PROJECT",
            "sequence": self.sequence_edit.text() or "SEQUENCE",
            "shot": self.shot_edit.text() or "SHOT",
            "version": self.version_edit.text() or "v001",
            "take": self.take_edit.text() or "01",
            "submitFor": self.submit_for_combo.currentText(),
            "artist": self.artist_edit.text() or "\u2014",
            "vendor": self.vendor_edit.text() or "\u2014",
            "shotTypes": self.shot_types_edit.text() or "\u2014",
            "scope": self.scope_edit.text() or "\u2014",
            "logo": self.logo_edit.text() or "STUDIO",
            "date": time.strftime("%Y-%m-%d"),
            "fps": str(int(fps)) if fps == int(fps) else f"{fps:.3f}",
            "resolution": f"{w}\u00d7{h}",
            "frameRange": self.frame_range_edit.text() or "\u2014",
            "colorspace": self.cs_combo.currentText(),
            "bitDepth": (
                "16-bit half" if self.depth_combo.currentData() == "half" else "32-bit float"
            ),
            "notes": self.notes_edit.toPlainText(),
        }
