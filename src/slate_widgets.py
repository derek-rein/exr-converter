"""Slate editor widgets: form panel + preview dialog.

The ``SlateDialog`` is opened from the conversion tabs when the user checks
"Prepend slate" and clicks "Edit Slate…".  It contains a form on the left
and a live WebEngine preview on the right.
"""

from __future__ import annotations

import json
import time

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QWheelEvent
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .slate import DEFAULT_TEMPLATE

SLATE_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "720p  1280×720": (1280, 720),
    "HD  1920×1080": (1920, 1080),
    "2K  2048×1080": (2048, 1080),
    "2K Flat  1998×1080": (1998, 1080),
    "2K Scope  2048×858": (2048, 858),
    "2K Full  2048×1556": (2048, 1556),
    "UHD  3840×2160": (3840, 2160),
    "4K  4096×2160": (4096, 2160),
    "4K Flat  3996×2160": (3996, 2160),
    "4K Scope  4096×1716": (4096, 1716),
    "4K Full  4096×3112": (4096, 3112),
    "8K UHD  7680×4320": (7680, 4320),
    "Ana 2K  2048×1536": (2048, 1536),
    "Ana 4K  4096×3072": (4096, 3072),
    "Square 2K  2048×2048": (2048, 2048),
    "Square 4K  4096×4096": (4096, 4096),
}

COMMON_FPS: list[float] = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 60.0]

ZOOM_MIN = 0.05
ZOOM_MAX = 5.0


# ---------------------------------------------------------------------------
# Slate form panel
# ---------------------------------------------------------------------------


class SlateFormPanel(QWidget):
    """Form collecting all slate metadata fields.

    Top section: project, shot, version, artist, date, frame range, fps.
    Collapsible "Additional Details": sequence, take, submit_for, vendor,
    shot_types, scope, logo, notes.
    Resolution row: combo + width/height spins (disabled when locked).
    """

    data_changed = Signal(dict)

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._resolution_locked = False
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # --- Primary fields (always visible) ---
        primary = QGroupBox("Slate Info")
        pf = QFormLayout(primary)
        pf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.project_edit = self._line("slate/project", "Show / Project codename")
        self.shot_edit = self._line("slate/shot", "SHOT_010")
        self.version_edit = self._line("slate/version", "v001")
        self.artist_edit = self._line("slate/artist", "Artist Name")
        self.frame_range_edit = self._line("slate/frame_range", "1001 – 1100")

        self.fps_combo = QComboBox()
        for fps_val in COMMON_FPS:
            label = str(int(fps_val)) if fps_val == int(fps_val) else f"{fps_val:.3f}"
            self.fps_combo.addItem(label, float(fps_val))
        saved_fps = float(settings.value("slate/fps", 24.0))
        for i in range(self.fps_combo.count()):
            data = self.fps_combo.itemData(i)
            if data is not None and abs(data - saved_fps) < 0.01:
                self.fps_combo.setCurrentIndex(i)
                break

        pf.addRow("Project", self.project_edit)
        pf.addRow("Shot", self.shot_edit)
        pf.addRow("Version", self.version_edit)
        pf.addRow("Artist", self.artist_edit)
        pf.addRow("Frame Range", self.frame_range_edit)
        pf.addRow("FPS", self.fps_combo)
        root.addWidget(primary)

        # --- Resolution ---
        res_group = QGroupBox("Resolution")
        res_layout = QFormLayout(res_group)
        res_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.res_combo = QComboBox()
        for label in SLATE_RESOLUTIONS:
            self.res_combo.addItem(label)
        saved_res = settings.value("slate/resolution", list(SLATE_RESOLUTIONS.keys())[0])
        idx = self.res_combo.findText(saved_res)
        if idx >= 0:
            self.res_combo.setCurrentIndex(idx)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(64, 16384)
        self.width_spin.setSuffix(" px")
        self.height_spin = QSpinBox()
        self.height_spin.setRange(64, 16384)
        self.height_spin.setSuffix(" px")

        saved_w = int(settings.value("slate/res_w", 1920))
        saved_h = int(settings.value("slate/res_h", 1080))
        if idx >= 0:
            preset_w, preset_h = SLATE_RESOLUTIONS[saved_res]
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

        res_layout.addRow("Preset", self.res_combo)
        res_layout.addRow("Size", self._res_size_widget)
        root.addWidget(res_group)

        # --- Additional Details (collapsible) ---
        self._details_check = QCheckBox("Additional Details")
        self._details_check.setChecked(bool(settings.value("slate/show_details", False)))
        root.addWidget(self._details_check)

        self._details_group = QWidget()
        details_layout = QFormLayout(self._details_group)
        details_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        details_layout.setContentsMargins(0, 0, 0, 0)

        self.sequence_edit = self._line("slate/sequence", "SEQ010")
        self.take_edit = self._line("slate/take", "01")

        self.submit_for_combo = QComboBox()
        for label in ("WIP", "FINAL", "CBB"):
            self.submit_for_combo.addItem(label)
        saved_sf = settings.value("slate/submit_for", "WIP")
        sf_idx = self.submit_for_combo.findText(saved_sf)
        if sf_idx >= 0:
            self.submit_for_combo.setCurrentIndex(sf_idx)

        self.vendor_edit = self._line("slate/vendor", "Studio / Vendor name")
        self.shot_types_edit = self._line("slate/shot_types", "2d comp, 3d, matte paint…")
        self.scope_edit = self._line("slate/scope", "VFX scope of work")
        self.logo_edit = self._line("slate/logo", "STUDIO")
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setPlaceholderText("Optional notes…")

        details_layout.addRow("Sequence", self.sequence_edit)
        details_layout.addRow("Take", self.take_edit)
        details_layout.addRow("Submitting For", self.submit_for_combo)
        details_layout.addRow("Vendor", self.vendor_edit)
        details_layout.addRow("Shot Types", self.shot_types_edit)
        details_layout.addRow("Scope of Work", self.scope_edit)
        details_layout.addRow("Logo / Studio", self.logo_edit)
        details_layout.addRow("Notes", self.notes_edit)

        self._details_group.setVisible(self._details_check.isChecked())
        root.addWidget(self._details_group)

        root.addStretch()

        # --- Connections ---
        self._details_check.toggled.connect(self._details_group.setVisible)
        self._details_check.toggled.connect(
            lambda v: self._settings.setValue("slate/show_details", v)
        )
        self.res_combo.currentTextChanged.connect(self._on_preset_changed)

        for widget in (
            self.project_edit,
            self.shot_edit,
            self.version_edit,
            self.artist_edit,
            self.frame_range_edit,
            self.sequence_edit,
            self.take_edit,
            self.vendor_edit,
            self.shot_types_edit,
            self.scope_edit,
            self.logo_edit,
        ):
            widget.textChanged.connect(self._emit_changed)

        self.fps_combo.currentIndexChanged.connect(self._emit_changed)
        self.submit_for_combo.currentIndexChanged.connect(self._emit_changed)
        self.width_spin.valueChanged.connect(self._emit_changed)
        self.height_spin.valueChanged.connect(self._emit_changed)
        self.notes_edit.textChanged.connect(self._emit_changed)

    # --- Helpers ---

    def _line(self, key: str, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        saved = self._settings.value(key, "")
        if saved:
            edit.setText(saved)
        return edit

    def _on_preset_changed(self, text: str) -> None:
        if text in SLATE_RESOLUTIONS and not self._resolution_locked:
            w, h = SLATE_RESOLUTIONS[text]
            self.width_spin.setValue(w)
            self.height_spin.setValue(h)
        self._settings.setValue("slate/resolution", text)

    def resolution(self) -> tuple[int, int]:
        return self.width_spin.value(), self.height_spin.value()

    def set_resolution_locked(self, width: int, height: int) -> None:
        """Lock resolution to input dimensions (disables combo and spins)."""
        self._resolution_locked = True
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)
        self.res_combo.setEnabled(False)
        self.width_spin.setEnabled(False)
        self.height_spin.setEnabled(False)

    def set_resolution_unlocked(self) -> None:
        """Re-enable resolution controls."""
        self._resolution_locked = False
        self.res_combo.setEnabled(True)
        self.width_spin.setEnabled(True)
        self.height_spin.setEnabled(True)

    def _save_fields(self) -> None:
        s = self._settings
        s.setValue("slate/project", self.project_edit.text())
        s.setValue("slate/shot", self.shot_edit.text())
        s.setValue("slate/version", self.version_edit.text())
        s.setValue("slate/artist", self.artist_edit.text())
        s.setValue("slate/frame_range", self.frame_range_edit.text())
        s.setValue("slate/fps", self.fps_combo.currentData())
        s.setValue("slate/sequence", self.sequence_edit.text())
        s.setValue("slate/take", self.take_edit.text())
        s.setValue("slate/submit_for", self.submit_for_combo.currentText())
        s.setValue("slate/vendor", self.vendor_edit.text())
        s.setValue("slate/shot_types", self.shot_types_edit.text())
        s.setValue("slate/scope", self.scope_edit.text())
        s.setValue("slate/logo", self.logo_edit.text())
        s.setValue("slate/res_w", self.width_spin.value())
        s.setValue("slate/res_h", self.height_spin.value())

    def _emit_changed(self, *_args) -> None:
        self._save_fields()
        self.data_changed.emit(self.slate_data())

    def slate_data(self) -> dict:
        """Return a dict suitable for passing to the JS ``updateSlate()`` function."""
        w, h = self.resolution()
        fps = self.fps_combo.currentData() or 24.0
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
            "colorspace": "Linear",
            "bitDepth": "16-bit half",
            "notes": self.notes_edit.toPlainText(),
        }


# ---------------------------------------------------------------------------
# Slate preview (pan/zoom graphics view)
# ---------------------------------------------------------------------------


class SlatePreviewView(QGraphicsView):
    """QGraphicsView with Nuke-style pan/zoom navigation.

    Controls:
      - MMB drag: pan
      - Scroll wheel: zoom to cursor
      - RMB drag: zoom (horizontal)
      - F key: fit slate in view
    """

    WHEEL_SCALE_FACTOR = 1.0 / 4.0

    def __init__(self, web_view: QWebEngineView, parent: QWidget | None = None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#323232")))

        self._proxy: QGraphicsProxyWidget = self._scene.addWidget(web_view)
        self._web = web_view

        self._panning = False
        self._zooming = False
        self._last_pos = None
        self._zoom_anchor_scene = None

    def set_slate_size(self, w: int, h: int) -> None:
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        self._web.setFixedSize(preview_w, preview_h)
        self._proxy.setMinimumSize(preview_w, preview_h)
        self._proxy.setMaximumSize(preview_w, preview_h)
        self._proxy.resize(preview_w, preview_h)
        self._scene.setSceneRect(QRectF(0, 0, preview_w, preview_h))
        self._web.page().setZoomFactor(1.0)
        self.fit_in_view()

    def fit_in_view(self) -> None:
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _view_pos(self, event) -> QPointF:
        return event.position()

    def _to_scene(self, view_pt: QPointF) -> QPointF:
        return self.mapToScene(view_pt.toPoint())

    def _scale_by(self, factor: float, center_scene: QPointF) -> None:
        cur = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, cur * factor))
        s = target / cur
        if abs(s - 1.0) < 1e-7:
            return
        xf = self.transform()
        cx, cy = center_scene.x(), center_scene.y()
        xf.translate(cx, cy)
        xf.scale(s, s)
        xf.translate(-cx, -cy)
        self.setTransform(xf)

    def mousePressEvent(self, event):
        btn = event.button()
        if btn == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_pos = self._view_pos(event)
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton:
            self._zooming = True
            self._last_pos = self._view_pos(event)
            self._zoom_anchor_scene = self._to_scene(self._last_pos)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = self._view_pos(event)
        if self._panning and self._last_pos is not None:
            delta = pos - self._last_pos
            self._last_pos = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        if self._zooming and self._last_pos is not None:
            dx = pos.x() - self._last_pos.x()
            self._last_pos = pos
            s = 1.02 ** (dx * 0.5)
            self._scale_by(s, self._zoom_anchor_scene)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        btn = event.button()
        if btn == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._last_pos = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton and self._zooming:
            self._zooming = False
            self._last_pos = None
            self._zoom_anchor_scene = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        s = 1.02 ** (delta * self.WHEEL_SCALE_FACTOR)
        center = self._to_scene(self._view_pos(event))
        self._scale_by(s, center)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F and not event.modifiers():
            self.fit_in_view()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)


# ---------------------------------------------------------------------------
# Slate dialog
# ---------------------------------------------------------------------------


class SlateDialog(QDialog):
    """Modal dialog for editing slate data with a live preview.

    Left side: ``SlateFormPanel`` in a scroll area.
    Right side: ``SlatePreviewView`` with a WebEngine preview.
    Bottom: OK / Cancel buttons.
    """

    def __init__(
        self,
        settings: QSettings,
        locked_width: int = 0,
        locked_height: int = 0,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Slate")
        self.resize(1400, 850)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left: form ---
        self._form = SlateFormPanel(settings)
        if locked_width > 0 and locked_height > 0:
            self._form.set_resolution_locked(locked_width, locked_height)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form)
        scroll.setMinimumWidth(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(scroll)

        # --- Right: web preview ---
        self._web = QWebEngineView()
        self._web.page().settings().setAttribute(
            QWebEngineSettings.WebAttribute.ShowScrollBars, False
        )
        self._web.page().setBackgroundColor(QColor("#323232"))

        self._preview = SlatePreviewView(self._web)
        w, h = self._form.resolution()
        self._preview.set_slate_size(w, h)
        splitter.addWidget(self._preview)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 1020])

        layout.addWidget(splitter, 1)

        # --- Bottom: buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(12, 8, 12, 8)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_layout.addWidget(buttons)
        layout.addLayout(btn_layout)

        # --- Load template + live preview ---
        self._load_template()
        self._form.data_changed.connect(self._push_preview)
        self._form.data_changed.connect(self._update_preview_size)
        self._web.loadFinished.connect(self._on_template_loaded)

    def _load_template(self) -> None:
        url = QUrl.fromLocalFile(str(DEFAULT_TEMPLATE))
        self._web.load(url)

    def _on_template_loaded(self, ok: bool) -> None:
        if ok:
            self._push_preview(self._form.slate_data())
            self._preview.fit_in_view()

    def _push_preview(self, data: dict | None = None) -> None:
        if data is None:
            data = self._form.slate_data()
        js = f"if(typeof updateSlate==='function') updateSlate({json.dumps(data)})"
        self._web.page().runJavaScript(js)

    def _update_preview_size(self, _data: dict | None = None) -> None:
        w, h = self._form.resolution()
        self._preview.set_slate_size(w, h)

    def slate_data(self) -> dict:
        """Return the form data."""
        return self._form.slate_data()

    def resolution(self) -> tuple[int, int]:
        return self._form.resolution()
