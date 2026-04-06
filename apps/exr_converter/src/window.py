from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import PyOpenColorIO as OCIO_mod
from PySide6.QtCore import QSettings, Qt, QThread
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .constants import APP_NAME, APP_ORG, APP_VERSION
from .ocio_utils import color_space_families, config_source_info
from .presets import delete_preset, list_presets, load_preset, save_preset
from .widgets import ConvertTab, OcioConfigPanel
from .worker import ConvertWorker


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About EXR Converter")
        self.setFixedSize(480, 440)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"<h2>EXR Converter</h2><p>Version {APP_VERSION}</p>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        try:
            import OpenImageIO as oiio

            oiio_ver = getattr(oiio, "VERSION_STRING", None) or str(oiio.openimageio_version())
        except Exception:
            oiio_ver = "?"

        info_lines = [
            f"Python {sys.version.split()[0]}",
            f"PySide6 {__import__('PySide6').__version__}",
            f"OpenColorIO {OCIO_mod.GetVersion()}",
            f"OpenImageIO {oiio_ver}",
        ]

        info = QLabel("<br>".join(info_lines))
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(
            "<p>by <b>Derek Rein</b></p>"
            '<p><a href="https://derekvfx.ca">derekvfx.ca</a> &nbsp;|&nbsp; '
            '<a href="https://ocio.cc">ocio.cc</a></p>'
            "<hr>"
            f"<p style='font-size:10px;'>"
            f"MIT License &copy; {datetime.now().year} Derek Rein<br><br>"
            "Permission is hereby granted, free of charge, to any person obtaining "
            "a copy of this software and associated documentation files, to deal in "
            "the Software without restriction, including without limitation the "
            "rights to use, copy, modify, merge, publish, distribute, sublicense, "
            "and/or sell copies of the Software, subject to the above copyright "
            "notice and this permission notice being included in all copies.</p>"
        )
        body.setReadOnly(True)
        layout.addWidget(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EXR Converter")
        self.setWindowIcon(QIcon(":/icon.png"))
        self.setMinimumSize(700, 640)
        self.setAcceptDrops(True)
        self._settings = QSettings(APP_ORG, APP_NAME)
        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None
        self._ocio_cfg = None

        self._build_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(splitter)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(8, 8, 8, 4)
        top_layout.setSpacing(6)

        self._ocio_panel = OcioConfigPanel(self._settings)
        top_layout.addWidget(self._ocio_panel)

        self._tabs = QTabWidget()
        self._v2e_tab = ConvertTab("video2exr", self._settings)
        self._e2v_tab = ConvertTab("exr2video", self._settings)
        self._tabs.addTab(self._v2e_tab, "Video \u2192 EXR")
        self._tabs.addTab(self._e2v_tab, "EXR \u2192 Video")
        saved_tab = int(self._settings.value("ui/tab", 0))
        if 0 <= saved_tab < self._tabs.count():
            self._tabs.setCurrentIndex(saved_tab)
        top_layout.addWidget(self._tabs, 1)

        prog_row = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%p%")
        prog_row.addWidget(self._progress, 1)
        self._go = QPushButton("  Convert  ")
        self._go.setObjectName("convertBtn")
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        prog_row.addWidget(self._go)
        prog_row.addWidget(self._cancel_btn)
        top_layout.addLayout(prog_row)

        splitter.addWidget(top)

        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(8, 4, 8, 4)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Log"))
        self._clear_log = QPushButton("Clear")
        self._clear_log.setObjectName("clearBtn")
        log_header.addStretch()
        log_header.addWidget(self._clear_log)
        log_layout.addLayout(log_header)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(5000)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        self._log.setFont(mono)
        self._log.setObjectName("logPane")
        log_layout.addWidget(self._log, 1)
        splitter.addWidget(log_container)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._ocio_panel.config_changed.connect(self._reload_ocio)
        self._go.clicked.connect(self._start)
        self._cancel_btn.clicked.connect(self._cancel_run)
        self._clear_log.clicked.connect(self._log.clear)
        self._tabs.currentChanged.connect(lambda i: self._settings.setValue("ui/tab", i))
        self._v2e_tab.log_message.connect(self._append_log)
        self._e2v_tab.log_message.connect(self._append_log)

        self._reload_ocio()

        geom = self._settings.value("ui/geometry")
        if geom:
            self.restoreGeometry(geom)

    # -- Menu bar --

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()
        mb.setNativeMenuBar(False)

        file_menu = mb.addMenu("&File")
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        self._presets_menu = mb.addMenu("&Presets")
        self._presets_menu.aboutToShow.connect(self._populate_presets_menu)

        help_menu = mb.addMenu("&Help")

        about_action = QAction("&About EXR Converter", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        help_menu.addSeparator()

        site_action = QAction("derekvfx.ca", self)
        site_action.triggered.connect(lambda: QDesktopServices.openUrl("https://derekvfx.ca"))
        help_menu.addAction(site_action)

        ocio_action = QAction("ocio.cc", self)
        ocio_action.triggered.connect(lambda: QDesktopServices.openUrl("https://ocio.cc"))
        help_menu.addAction(ocio_action)

    def _show_about(self) -> None:
        dlg = AboutDialog(self)
        dlg.exec()

    # -- Presets --

    def _populate_presets_menu(self) -> None:
        m = self._presets_menu
        m.clear()

        save_action = QAction("Save Preset As\u2026", self)
        save_action.triggered.connect(self._save_preset)
        m.addAction(save_action)
        m.addSeparator()

        names = list_presets()
        if names:
            for name in names:
                action = QAction(name, self)
                action.triggered.connect(lambda _checked, n=name: self._load_preset(n))
                m.addAction(action)
            m.addSeparator()
            delete_sub = m.addMenu("Delete Preset")
            for name in names:
                action = QAction(name, self)
                action.triggered.connect(lambda _checked, n=name: self._delete_preset(n))
                delete_sub.addAction(action)
        else:
            no_presets = QAction("(no presets saved)", self)
            no_presets.setEnabled(False)
            m.addAction(no_presets)

    def _save_preset(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if ok and name.strip():
            state = self.snapshot_state()
            save_preset(name.strip(), state)
            self._append_log(f"Preset saved: {name.strip()}")

    def _load_preset(self, name: str) -> None:
        try:
            data = load_preset(name)
        except Exception as e:
            QMessageBox.warning(self, "Load Preset", f"Failed to load preset: {e}")
            return
        self.restore_state(data)
        self._append_log(f"Preset loaded: {name}")

    def _delete_preset(self, name: str) -> None:
        delete_preset(name)
        self._append_log(f"Preset deleted: {name}")

    # -- OCIO --

    def _reload_ocio(self) -> None:
        cfg = self._ocio_panel.load_config()
        if cfg is None:
            self._ocio_cfg = None
            self._statusbar.showMessage("OCIO config error", 5000)
            return
        self._ocio_cfg = cfg
        families = color_space_families(cfg)
        n_spaces = sum(len(v) for v in families.values())
        self._v2e_tab.populate_spaces(families, ocio_cfg=cfg)
        self._e2v_tab.populate_spaces(families, ocio_cfg=cfg)
        self._statusbar.showMessage(f"OCIO: {n_spaces} color spaces loaded", 3000)
        self._append_log(f"OCIO config loaded ({n_spaces} spaces)")

    # -- Log --

    def _append_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    # -- Convert --

    def _active_tab(self) -> ConvertTab:
        return self._v2e_tab if self._tabs.currentIndex() == 0 else self._e2v_tab

    def _start(self) -> None:
        if self._ocio_cfg is None:
            QMessageBox.warning(self, "OCIO", "No valid OCIO config loaded.")
            return

        tab = self._active_tab()
        mode = "video2exr" if self._tabs.currentIndex() == 0 else "exr2video"

        inp = tab.input_path.text().strip()
        out = tab.output_path.text().strip()
        if not inp or not out:
            QMessageBox.warning(self, "Missing paths", "Set both input and output paths.")
            return
        src = tab.src_btn.current_space()
        dst = tab.dst_btn.current_space()
        if not src or not dst:
            QMessageBox.warning(self, "Color spaces", "Select source and destination color spaces.")
            return

        self._go.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress.setValue(0)

        cs, cp = config_source_info(
            self._ocio_panel.current_source_key(),
            self._ocio_panel._file_path,
        )

        if mode == "video2exr":
            kwargs = dict(
                video_path=inp,
                output_dir=Path(out),
                ocio_cfg=self._ocio_cfg,
                src_space=src,
                dst_space=dst,
                compression=tab.get_compression(),
                config_source=cs,
                config_path=cp,
                scale=tab.get_scale(),
            )
        else:
            _codec_key, _codec, _pix = tab.get_video_codec_info()
            kwargs = dict(
                input_spec=inp,
                output_video=Path(out),
                ocio_cfg=self._ocio_cfg,
                src_space=src,
                dst_space=dst,
                fps=tab.get_fps(),
                config_source=cs,
                config_path=cp,
                scale=tab.get_scale(),
                video_codec=_codec,
                pix_fmt_out=_pix,
                codec_key=_codec_key,
            )

        self._append_log(f"--- {mode} ---")
        self._thread = QThread()
        self._worker = ConvertWorker(mode, kwargs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._append_log)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.finished_ok.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        worker_ref = self._worker
        self._thread.finished.connect(worker_ref.deleteLater)
        self._thread.start()

    def _on_progress(self, cur: int, total: int) -> None:
        if total > 0:
            self._progress.setValue(int(100 * cur / total))
        self._statusbar.showMessage(f"Frame {cur} / {total}")

    def _on_failed(self, msg: str) -> None:
        self._progress.setValue(0)
        self._statusbar.showMessage("Conversion failed.")
        QMessageBox.critical(self, "Error", msg)
        self._go.setEnabled(True)
        self._cancel_btn.setEnabled(False)

    def _on_done(self) -> None:
        self._progress.setValue(100)
        self._statusbar.showMessage("Done.", 5000)
        self._go.setEnabled(True)
        self._cancel_btn.setEnabled(False)

    def _cleanup_thread(self) -> None:
        self._worker = None
        self._thread = None

    def _cancel_run(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._append_log("Cancellation requested\u2026")

    # -- State snapshot/restore (for presets) --

    def snapshot_state(self) -> dict:
        """Capture parameters only — no input/output paths."""
        return {
            "tab": self._tabs.currentIndex(),
            "ocio_source": self._ocio_panel.current_source_key(),
            "ocio_file": self._ocio_panel._file_path,
            "v2e_src_space": self._v2e_tab.src_btn.current_space(),
            "v2e_dst_space": self._v2e_tab.dst_btn.current_space(),
            "v2e_compression": self._v2e_tab.get_compression(),
            "v2e_scale": self._v2e_tab.get_scale(),
            "e2v_src_space": self._e2v_tab.src_btn.current_space(),
            "e2v_dst_space": self._e2v_tab.dst_btn.current_space(),
            "e2v_fps": self._e2v_tab.get_fps(),
            "e2v_scale": self._e2v_tab.get_scale(),
            "e2v_codec": self._e2v_tab.get_video_codec_info()[0],
        }

    def restore_state(self, data: dict) -> None:
        """Restore parameters only — input/output paths are left untouched."""
        if "tab" in data:
            self._tabs.setCurrentIndex(data["tab"])
        if "ocio_source" in data:
            combo = self._ocio_panel._source_combo
            for i in range(combo.count()):
                if combo.itemData(i) == data["ocio_source"]:
                    combo.setCurrentIndex(i)
                    break
        if "ocio_file" in data:
            self._ocio_panel._file_path = data["ocio_file"]
            self._ocio_panel._settings.setValue("ocio/file_path", data["ocio_file"])
            self._ocio_panel._update_custom_label()
        if "v2e_src_space" in data:
            self._v2e_tab.src_btn.set_current_space(data["v2e_src_space"])
        if "v2e_dst_space" in data:
            self._v2e_tab.dst_btn.set_current_space(data["v2e_dst_space"])
        if "v2e_compression" in data and self._v2e_tab.compression_combo:
            from .constants import EXR_COMPRESSIONS

            val = data["v2e_compression"]
            if val in EXR_COMPRESSIONS:
                self._v2e_tab.compression_combo.setCurrentIndex(EXR_COMPRESSIONS.index(val))
        if "e2v_src_space" in data:
            self._e2v_tab.src_btn.set_current_space(data["e2v_src_space"])
        if "e2v_dst_space" in data:
            self._e2v_tab.dst_btn.set_current_space(data["e2v_dst_space"])
        if "e2v_fps" in data and self._e2v_tab.fps_widget:
            self._e2v_tab.fps_widget._restore(data["e2v_fps"])
        for tab_prefix, tab_widget in [("v2e", self._v2e_tab), ("e2v", self._e2v_tab)]:
            scale_key = f"{tab_prefix}_scale"
            if scale_key in data:
                for i in range(tab_widget.scale_combo.count()):
                    if abs(tab_widget.scale_combo.itemData(i) - data[scale_key]) < 0.01:
                        tab_widget.scale_combo.setCurrentIndex(i)
                        break
        if "e2v_codec" in data and self._e2v_tab.codec_combo:
            for i in range(self._e2v_tab.codec_combo.count()):
                if self._e2v_tab.codec_combo.itemData(i) == data["e2v_codec"]:
                    self._e2v_tab.codec_combo.setCurrentIndex(i)
                    break

    # -- Drag and drop --

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

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    p = Path(url.toLocalFile())
                    if p.is_dir() or p.suffix.lower() in (self._VIDEO_EXTS | {".exr"}):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        mime = event.mimeData()
        if not mime.hasUrls():
            return
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_file() and p.suffix.lower() in self._VIDEO_EXTS:
                self._tabs.setCurrentIndex(0)
                self._v2e_tab.handle_dropped_path(str(p))
                self._append_log(f"Dropped video: {p.name}")
                event.acceptProposedAction()
                return
            if p.is_dir() or (p.is_file() and p.suffix.lower() == ".exr"):
                self._tabs.setCurrentIndex(1)
                self._e2v_tab.handle_dropped_path(str(p))
                self._append_log(f"Dropped: {p.name}")
                event.acceptProposedAction()
                return

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._settings.setValue("ui/geometry", self.saveGeometry())
        if self._worker:
            self._worker.cancel()
        super().closeEvent(event)
