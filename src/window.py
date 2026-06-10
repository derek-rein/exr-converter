from __future__ import annotations

import platform
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import PyOpenColorIO as OCIO_mod
from PySide6.QtCore import QObject, QSettings, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFontDatabase,
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

from .constants import APP_NAME, APP_ORG, APP_VERSION, GITHUB_REPO
from .ocio_utils import color_space_families, config_source_info
from .presets import delete_preset, list_presets, load_preset, save_preset
from .widgets import ConvertTab, OcioConfigPanel
from .worker import ConvertWorker


class _DownloadWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, dest: str) -> None:
        super().__init__()
        self._url = url
        self._dest = dest

    @Slot()
    def run(self) -> None:
        try:
            with urlopen(self._url, timeout=300) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                chunk_size = 64 * 1024
                downloaded = 0
                chunks: list[bytes] = []
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    self.progress.emit(downloaded, total)
            Path(self._dest).write_bytes(b"".join(chunks))
            self.finished.emit(self._dest)
        except Exception as e:
            self.failed.emit(str(e))


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
            "<p><b>Bundled OCIO config:</b> ACES Studio Config v4 (ACES 2.0)<br>"
            "Sourced from <a href='https://github.com/AcademySoftwareFoundation/OpenColorIO-Config-ACES/releases'>"
            "AcademySoftwareFoundation/OpenColorIO-Config-ACES</a> (BSD-3-Clause).<br>"
            "Contains official camera IDTs for <b>Apple Log</b> (iPhone cinematic/ProRes Log), ARRI, RED, Sony, Canon, DJI and many more.</p>"
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
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
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
        self._tabs.currentChanged.connect(lambda _: self._update_go_state())
        self._v2e_tab.log_message.connect(self._append_log)
        self._e2v_tab.log_message.connect(self._append_log)
        self._v2e_tab.readiness_changed.connect(lambda _: self._update_go_state())
        self._e2v_tab.readiness_changed.connect(lambda _: self._update_go_state())
        self._go.setEnabled(self._active_tab().is_ready())

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

        slate_menu = mb.addMenu("&Slate")
        edit_slate_action = QAction("Edit Slate && Overlays\u2026", self)
        edit_slate_action.triggered.connect(self._open_slate_dialog)
        slate_menu.addAction(edit_slate_action)

        help_menu = mb.addMenu("&Help")

        update_action = QAction("Check for &Updates\u2026", self)
        update_action.triggered.connect(self._check_for_updates)
        help_menu.addAction(update_action)

        help_menu.addSeparator()

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

    # -- Updates --

    def _check_for_updates(self) -> None:
        import json

        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        try:
            req = Request(api_url, headers={"Accept": "application/vnd.github+json"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except (URLError, OSError, ValueError) as e:
            QMessageBox.warning(self, "Update Check", f"Could not reach GitHub:\n{e}")
            return

        tag = data.get("tag_name", "")
        remote_ver = tag.lstrip("v")
        if not remote_ver:
            QMessageBox.information(self, "Update Check", "No releases found.")
            return

        def _ver_tuple(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split(".") if x.isdigit())

        try:
            if _ver_tuple(remote_ver) <= _ver_tuple(APP_VERSION):
                QMessageBox.information(
                    self, "Up to date", f"You're on the latest version ({APP_VERSION})."
                )
                return
        except Exception:
            pass

        asset_name = self._update_asset_name()
        if not asset_name:
            html_url = data.get("html_url", "")
            QMessageBox.information(
                self,
                "Update Available",
                f"Version {remote_ver} is available (you have {APP_VERSION}).\n"
                f"No installer found for this platform — visit the release page.",
            )
            if html_url:
                QDesktopServices.openUrl(QUrl(html_url))
            return

        download_url = ""
        for asset in data.get("assets", []):
            if asset.get("name", "") == asset_name:
                download_url = asset.get("browser_download_url", "")
                break

        if not download_url:
            QMessageBox.information(
                self,
                "Update Available",
                f"Version {remote_ver} is available but the expected asset\n"
                f"'{asset_name}' was not found in the release.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Update Available",
            f"Version {remote_ver} is available (you have {APP_VERSION}).\n\n"
            f"Download and run the installer?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        tmp_dir = Path(tempfile.mkdtemp(prefix="exr_converter_update_"))
        installer_path = tmp_dir / asset_name

        self._progress.setValue(0)
        self._progress.setFormat("Downloading update… %p%")
        self._statusbar.showMessage("Downloading update…")
        self._go.setEnabled(False)

        self._dl_thread = QThread()
        self._dl_worker = _DownloadWorker(download_url, str(installer_path))
        self._dl_worker.moveToThread(self._dl_thread)
        self._dl_thread.started.connect(self._dl_worker.run)
        self._dl_worker.progress.connect(self._on_download_progress)
        self._dl_worker.finished.connect(self._on_download_finished)
        self._dl_worker.failed.connect(self._on_download_failed)
        self._dl_worker.finished.connect(self._dl_thread.quit)
        self._dl_worker.failed.connect(self._dl_thread.quit)
        dl_ref = self._dl_worker
        self._dl_thread.finished.connect(dl_ref.deleteLater)
        self._dl_thread.start()

    def _on_download_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            self._progress.setValue(int(100 * downloaded / total))
        else:
            self._progress.setRange(0, 0)

    def _on_download_finished(self, dest: str) -> None:
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._progress.setFormat("%p%")
        self._update_go_state()
        name = Path(dest).name
        self._statusbar.showMessage(f"Downloaded {name}", 5000)
        self._run_installer(Path(dest))
        self._dl_thread = None
        self._dl_worker = None

    def _on_download_failed(self, error: str) -> None:
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        self._update_go_state()
        self._statusbar.clearMessage()
        QMessageBox.warning(self, "Download Failed", error)
        self._dl_thread = None
        self._dl_worker = None

    @staticmethod
    def _update_asset_name() -> str:
        s = sys.platform
        machine = platform.machine().lower()
        if s == "darwin":
            arch = "arm64" if machine == "arm64" else "x86_64"
            return f"exr_converter-macos-{arch}.dmg"
        if s == "win32":
            return "exr_converter-windows-x86_64-setup.exe"
        if s.startswith("linux"):
            return "exr_converter-linux-x86_64.AppImage"
        return ""

    @staticmethod
    def _run_installer(path: Path) -> None:
        s = sys.platform
        if s == "darwin":
            subprocess.run(
                ["xattr", "-dr", "com.apple.quarantine", str(path)],
                check=False,
            )
            subprocess.Popen(["open", str(path)])
        elif s == "win32":
            subprocess.Popen([str(path)], creationflags=subprocess.DETACHED_PROCESS)
        else:
            path.chmod(path.stat().st_mode | 0o755)
            subprocess.Popen(["xdg-open", str(path)])

    # -- Slate menu --

    def _active_tab(self) -> ConvertTab:
        return self._tabs.currentWidget()

    def _open_slate_dialog(self) -> None:
        tab = self._active_tab()
        tab._open_slate_dialog()

    # -- Presets --

    def _populate_presets_menu(self) -> None:
        m = self._presets_menu
        m.clear()

        reset_action = QAction("Reset to Defaults", self)
        reset_action.triggered.connect(self._reset_to_defaults)
        m.addAction(reset_action)
        m.addSeparator()

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

    def _reset_to_defaults(self) -> None:
        from .constants import (
            DEFAULT_EXR_COMPRESSION,
            DEFAULT_FRAME_PADDING,
            DEFAULT_SCALE,
            DEFAULT_SRC_E2V,
            DEFAULT_SRC_V2E,
            DEFAULT_START_FRAME,
            DEFAULT_VIDEO_CODEC,
            OCIO_SOURCE_ENV,
        )

        defaults = {
            "tab": 0,
            "ocio_source": OCIO_SOURCE_ENV,
            "ocio_file": "",
            "v2e_src_space": DEFAULT_SRC_V2E,
            "v2e_dst_space": "ACEScg",
            "v2e_compression": DEFAULT_EXR_COMPRESSION,
            "v2e_scale": DEFAULT_SCALE,
            "v2e_padding": DEFAULT_FRAME_PADDING,
            "v2e_start_frame": DEFAULT_START_FRAME,
            "e2v_src_space": DEFAULT_SRC_E2V,
            "e2v_dst_space": "Output - Rec.709",
            "e2v_fps": 24.0,
            "e2v_scale": DEFAULT_SCALE,
            "e2v_codec": DEFAULT_VIDEO_CODEC,
        }
        self.restore_state(defaults)
        self._append_log("Reset all parameters to defaults")

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

    def _update_go_state(self) -> None:
        """Enable Convert button only when the active tab has valid setup."""
        self._go.setEnabled(self._active_tab().is_ready())

    def _start(self) -> None:
        if self._ocio_cfg is None:
            QMessageBox.warning(self, "OCIO", "No valid OCIO config loaded.")
            return

        tab = self._active_tab()
        mode = "video2exr" if self._tabs.currentIndex() == 0 else "exr2video"

        inp = tab.get_input_path()
        out = tab.get_output_path()
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

        frame_range_str = tab.get_frame_range()
        frame_set: set[int] | None = None
        if frame_range_str:
            from .framerange import parse_frame_range

            try:
                frame_set = set(parse_frame_range(frame_range_str))
            except ValueError as e:
                QMessageBox.warning(self, "Frame range", f"Invalid frame range: {e}")
                self._update_go_state()
                self._cancel_btn.setEnabled(False)
                return
            if not frame_set:
                frame_set = None

        # -- Slate (raw sRGB float32 RGBA -- convert.py does the OCIO transit) --
        slate_np = None
        if tab.slate_enabled():
            slate_data = tab.get_slate_data()
            if slate_data is not None:
                from .slate import render_slate_frame

                sw, sh = self._detect_slate_resolution(mode, inp)
                thumb_b64 = tab.get_slate_thumbnail_b64()
                self._append_log(f"Rendering slate frame ({sw}\u00d7{sh})\u2026")
                try:
                    slate_np = render_slate_frame(slate_data, sw, sh, thumbnail_b64=thumb_b64)
                    self._append_log("Slate frame rendered successfully")
                except Exception as e:
                    QMessageBox.warning(self, "Slate Error", f"Failed to render slate: {e}")
                    self._update_go_state()
                    self._cancel_btn.setEnabled(False)
                    return

        # -- Burn-in + watermark overlays (EXR→Video only) --
        # Burn-in goes only on shot frames; watermark goes on every frame
        # (slate + shots) because that's how the live preview behaves.
        # Both stay as sRGB uint8 RGBA buffers — convert.py linearises them
        # once and keeps the linear copy for compositing in working space.
        overlay_np = None
        slate_overlay_np = None
        if mode == "exr2video":
            bw = bh = 0
            burnin_overlay = None
            watermark_overlay = None

            if tab.burnin_enabled():
                from .burnin import render_burnin_overlay

                bw, bh = self._detect_slate_resolution(mode, inp)
                fields = tab.get_effective_burnin_fields(inp) or {}
                burnin_overlay = render_burnin_overlay(bw, bh, fields)
                self._append_log("Burn-in overlay rendered")

            wm_params = tab.get_watermark_params()
            slate_model = tab.slate_model()
            if wm_params and slate_model is not None and slate_model.watermark_active():
                from .watermark import render_watermark_overlay

                if not (bw and bh):
                    bw, bh = self._detect_slate_resolution(mode, inp)
                watermark_overlay = render_watermark_overlay(bw, bh, wm_params)
                self._append_log("Watermark overlay rendered")

            # Per-frame overlay (burnin + watermark, baked together)
            if burnin_overlay is not None and watermark_overlay is not None:
                import numpy as np

                a = watermark_overlay[..., 3:4].astype(np.float32) / 255.0
                fg = watermark_overlay[..., :3].astype(np.float32)
                bg = burnin_overlay[..., :3].astype(np.float32)
                rgb = fg * a + bg * (1.0 - a)
                bg_a = burnin_overlay[..., 3:4].astype(np.float32) / 255.0
                out_a = a + bg_a * (1.0 - a)
                overlay_np = np.empty_like(burnin_overlay)
                overlay_np[..., :3] = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
                overlay_np[..., 3:4] = np.clip(out_a * 255.0, 0.0, 255.0).astype(np.uint8)
            else:
                overlay_np = burnin_overlay or watermark_overlay

            # Slate-only overlay: watermark only (burn-in skips the slate).
            slate_overlay_np = watermark_overlay

        if mode == "video2exr":
            # v2e still pre-transforms the slate to dst space (no overlays
            # here) — slate becomes one EXR frame on disk in dst colorspace.
            v2e_slate = slate_np
            if v2e_slate is not None:
                from .slate import SLATE_COLORSPACE

                v2e_slate = self._ocio_transform_slate(v2e_slate, SLATE_COLORSPACE, dst)
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
                padding=tab.get_padding(),
                start_frame=tab.get_start_frame(),
                frame_set=frame_set,
                slate_frame=v2e_slate,
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
                frame_set=frame_set,
                slate_frame=slate_np,
                burnin_overlay=overlay_np,
                slate_overlay=slate_overlay_np,
            )

        out_path = Path(out)
        self._output_folder = str(out_path if out_path.is_dir() else out_path.parent)

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
        self._update_go_state()
        self._cancel_btn.setEnabled(False)

    def _on_done(self) -> None:
        self._progress.setValue(100)
        self._statusbar.showMessage("Done.", 5000)
        self._update_go_state()
        self._cancel_btn.setEnabled(False)
        folder = getattr(self, "_output_folder", None)
        if folder and Path(folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _cleanup_thread(self) -> None:
        self._worker = None
        self._thread = None

    def _cancel_run(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._append_log("Cancellation requested\u2026")

    # -- Slate colorspace + resolution --

    def _ocio_transform_slate(self, slate, slate_cs: str, dst_space: str):
        """OCIO-transform the slate frame from its native colorspace to *dst_space*.

        The slate is painted with QPainter and is always sRGB.  This converts it
        into whatever the pipeline destination is (e.g. ACEScg for EXR output,
        or Rec.709 for video output).
        """
        import numpy as np
        import PyOpenColorIO as OCIO_mod

        cfg = self._ocio_cfg
        if cfg is None:
            return slate

        from .ocio_utils import resolve_alias

        src_name = resolve_alias(cfg, slate_cs)
        if not src_name:
            for candidate in ("sRGB", "sRGB - Texture", "Utility - sRGB - Texture", "srgb"):
                src_name = resolve_alias(cfg, candidate)
                if src_name:
                    break
        if not src_name:
            self._append_log(
                "Warning: could not find sRGB colorspace in OCIO config, "
                "slate will not be color-managed"
            )
            return slate

        dst_name = resolve_alias(cfg, dst_space) or dst_space
        if src_name == dst_name:
            return slate

        self._append_log(f"Slate OCIO: {src_name} \u2192 {dst_name}")
        rgb = np.ascontiguousarray(slate[:, :, :3], dtype=np.float32)
        h, w = rgb.shape[:2]
        cpu = cfg.getProcessor(src_name, dst_name).getDefaultCPUProcessor()
        desc = OCIO_mod.PackedImageDesc(rgb, w, h, 3)
        cpu.apply(desc)
        result = np.empty_like(slate)
        result[:, :, :3] = rgb
        result[:, :, 3] = slate[:, :, 3]
        return result

    @staticmethod
    def _detect_slate_resolution(mode: str, inp: str) -> tuple[int, int]:
        """Probe the input to determine the resolution for the slate frame."""
        try:
            if mode == "video2exr":
                from .video import probe_video

                w, h, _fps, _total = probe_video(inp)
                return w, h
            else:
                from .exr_io import read_exr
                from .sequence import find_exr_sequence

                paths, _bn = find_exr_sequence(inp)
                if paths:
                    first = read_exr(paths[0])
                    return first.shape[1], first.shape[0]
        except Exception:
            pass
        return 1920, 1080

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
            "v2e_padding": self._v2e_tab.get_padding(),
            "v2e_start_frame": self._v2e_tab.get_start_frame(),
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
        if "v2e_padding" in data and self._v2e_tab.padding_spin:
            self._v2e_tab.padding_spin.setValue(int(data["v2e_padding"]))
        if "v2e_start_frame" in data and self._v2e_tab.start_frame_spin:
            self._v2e_tab.start_frame_spin.setValue(int(data["v2e_start_frame"]))
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
