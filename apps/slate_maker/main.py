from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QWheelEvent
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import src.rc_resources  # noqa: F401 — register Qt resources
from src.widgets import SlateFormPanel
from src.worker import RenderSettings, RenderWorker

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = TEMPLATES_DIR / "slate.html"

ZOOM_MIN = 0.05
ZOOM_MAX = 5.0
ZOOM_STEP = 1.15


class Bridge(QObject):
    """Python <-> JavaScript bridge exposed via QWebChannel."""

    ready = Signal()

    @Slot(str)
    def log(self, msg: str):
        log.info("[JS] %s", msg)


class SlatePreviewView(QGraphicsView):
    """QGraphicsView with Nuke/pyqtgraph-style navigation.

    Controls:
      - MMB drag: pan
      - Scroll wheel: zoom to cursor (like pyqtgraph wheelEvent)
      - RMB drag: zoom — horizontal drag scales uniformly,
        anchored at the press position (like pyqtgraph RMB drag)
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

    def set_slate_size(self, w: int, h: int):
        """Size the preview to the target aspect ratio.

        The HTML template uses vh/vw units calibrated to the Netflix Nuke
        template (4K / 2160h reference), so text proportions are resolution-
        independent.  We fix the preview at 1080 logical pixels tall for a
        crisp display and let fitInView scale it into the viewport.
        """
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        self._web.setFixedSize(preview_w, preview_h)
        self._proxy.setMinimumSize(preview_w, preview_h)
        self._proxy.setMaximumSize(preview_w, preview_h)
        self._proxy.resize(preview_w, preview_h)
        self._scene.setSceneRect(QRectF(0, 0, preview_w, preview_h))
        self._web.page().setZoomFactor(1.0)
        self.fit_in_view()

    def fit_in_view(self):
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # --- helpers ---

    def _view_pos(self, event) -> QPointF:
        """Widget-local position from any mouse event."""
        return event.position()

    def _to_scene(self, view_pt: QPointF) -> QPointF:
        return self.mapToScene(view_pt.toPoint())

    def _scale_by(self, factor: float, center_scene: QPointF):
        """Uniform scale around a point in scene coordinates (pyqtgraph style)."""
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

    def _translate_by(self, dx_scene: float, dy_scene: float):
        """Translate the view by a scene-space delta (pyqtgraph style)."""
        r = self.sceneRect()
        self.setSceneRect(r)
        bar_h = self.horizontalScrollBar()
        bar_v = self.verticalScrollBar()
        bar_h.setValue(bar_h.value() - int(dx_scene * self.transform().m11()))
        bar_v.setValue(bar_v.value() - int(dy_scene * self.transform().m22()))

    # --- mouse events ---

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VFX Slate Maker")
        self.resize(1400, 850)

        self._settings = QSettings("DerekVFX", "SlateMaker")
        self._worker: RenderWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Top bar: key shot fields ---
        self._form = SlateFormPanel(self._settings)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(8, 6, 8, 6)
        top_bar.setSpacing(4)
        for label_text, widget in [
            ("Show", self._form.project_edit),
            ("Seq", self._form.sequence_edit),
            ("Shot", self._form.shot_edit),
            ("Version", self._form.version_edit),
            ("Take", self._form.take_edit),
        ]:
            lbl = QLabel(label_text)
            top_bar.addWidget(lbl)
            top_bar.addWidget(widget, 1)
        main_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left: form panel in scroll area ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form)
        scroll.setMinimumWidth(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(scroll)

        # --- Right: web preview with pan/zoom ---
        self._web = QWebEngineView()
        self._web.page().settings().setAttribute(
            QWebEngineSettings.WebAttribute.ShowScrollBars,
            False,
        )
        self._web.page().setBackgroundColor(QColor("#323232"))

        self._bridge = Bridge(self)
        self._channel = QWebChannel(self._web.page())
        self._channel.registerObject("bridge", self._bridge)
        self._web.page().setWebChannel(self._channel)

        self._preview = SlatePreviewView(self._web)
        w, h = self._form.resolution()
        self._preview.set_slate_size(w, h)
        splitter.addWidget(self._preview)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 1020])

        main_layout.addWidget(splitter, 1)

        # --- Bottom bar ---
        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 8, 12, 8)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        bottom.addWidget(self._progress, 1)

        self._render_btn = QPushButton("Export EXR")
        self._render_btn.setObjectName("renderBtn")
        self._render_btn.clicked.connect(self._start_render)
        bottom.addWidget(self._render_btn)

        main_layout.addLayout(bottom)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready  |  MMB: pan   Scroll: zoom   RMB drag: zoom   F: fit")

        # --- Load template ---
        self._load_template()

        # --- Live preview updates ---
        self._form.data_changed.connect(self._push_preview)
        self._form.data_changed.connect(self._update_preview_size)
        self._web.loadFinished.connect(self._on_template_loaded)

    def _load_template(self):
        url = QUrl.fromLocalFile(str(DEFAULT_TEMPLATE))
        self._web.load(url)

    def _on_template_loaded(self, ok: bool):
        if ok:
            self._push_preview(self._form.slate_data())
            self._preview.fit_in_view()

    def _push_preview(self, data: dict | None = None):
        if data is None:
            data = self._form.slate_data()
        js = f"if(typeof updateSlate==='function') updateSlate({json.dumps(data)})"
        self._web.page().runJavaScript(js)

    def _update_preview_size(self, _data: dict | None = None):
        w, h = self._form.resolution()
        self._preview.set_slate_size(w, h)

    def _start_render(self):
        out_file = self._form.output_path.text().strip()
        if not out_file:
            QMessageBox.warning(self, "Missing output", "Please set an output file path.")
            return

        if not out_file.lower().endswith(".exr"):
            out_file += ".exr"

        if Path(out_file).exists():
            reply = QMessageBox.question(
                self,
                "File exists",
                f"{out_file} already exists.\nOverwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        w, h = self._form.resolution()
        settings = RenderSettings(
            output_path=out_file,
            width=w,
            height=h,
            slate_data=self._form.slate_data(),
            bit_depth=self._form.depth_combo.currentData(),
            colorspace=self._form.cs_combo.currentText(),
        )

        self._render_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._status_bar.showMessage("Rendering slate\u2026")

        self._worker = RenderWorker(settings, str(DEFAULT_TEMPLATE), parent=self)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_error(self, msg: str):
        QMessageBox.critical(self, "Render Error", msg)
        log.error("Render error: %s", msg)

    def _on_finished(self, output_path: str):
        self._render_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._worker = None
        if output_path:
            self._status_bar.showMessage(
                f"Saved: {output_path}  |  MMB: pan   Scroll: zoom   RMB drag: zoom   F: fit"
            )
        else:
            self._status_bar.showMessage(
                "Ready  |  MMB: pan   Scroll: zoom   RMB drag: zoom   F: fit"
            )


STYLE_PATH = Path(__file__).parent / "style.qss"


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(":/icon.png"))

    if STYLE_PATH.exists():
        app.setStyleSheet(STYLE_PATH.read_text())

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
