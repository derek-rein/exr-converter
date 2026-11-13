from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import PyOpenColorIO as OCIO_mod
from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import (
    APP_VERSION,
    DOCS_URL,
    GITHUB_REPO,
)
from ..core.decoders import set_decode_mode
from ..core.drop_routing import DropTarget, drop_target
from ..core.fs_skip import VIDEO_EXTS
from ..core.ocio_utils import color_space_families, config_source_info
from ..core.pixel_aspect import SQUARE, apply_override, probe_media_aspect
from ..core.quicklook_prefs import schedule_quicklook_view_lut
from ..core.video import is_ignored_media_filename
from ..services.presets import delete_preset, list_presets, load_preset, save_preset
from ..services.worker import ConvertWorker
from . import motion
from .action_bar import PROGRESS_LINE_HEIGHT, PrimaryButton, ProgressLine, StatusLine
from .animated_tabs import SlidingTabBar
from .color_widgets import lock_form_row_height
from .icons import set_button_icon_keep_text
from .motion import SLOW, Animator, crossfade_from
from .preferences import (
    PLAYER_MODE_BUILTIN,
    PreferencesDialog,
    decoder_mode,
    encoder_mode,
    open_video_with_player,
    player_mode,
    resolve_invisible_watermark,
    reveal_in_file_manager,
    sequence_display_name,
)
from .proxy_tab import ProxyTab
from .style import set_style_property
from .transcode_tab import TranscodeTab
from .widgets import ConvertTab, OcioConfigPanel, _select_video_codec_combo_key

# Floor so the log splitter cannot hide the convert form or the log entirely.
# ConvertTab scrolls internally if the tab page is shorter than the form.
CONVERT_TABS_MIN_HEIGHT = 200
# Tab order: Video → EXR, EXR → Video, Video → Proxy, Transcode.
PROXY_TAB_INDEX = 2
TRANSCODE_TAB_INDEX = 3
LOG_PANE_MIN_HEIGHT = 72
# First-open height of the log drawer (the user's drag is remembered).
LOG_OPEN_HEIGHT = 190


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About EXR Converter")
        self.setMinimumSize(480, 480)
        self.resize(520, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header only outside the scroll area.
        title = QLabel(f"<h2>EXR Converter</h2><p>Version {APP_VERSION}</p>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        try:
            import OpenImageIO as oiio

            oiio_ver = getattr(oiio, "VERSION_STRING", None) or str(oiio.openimageio_version())
        except Exception:
            oiio_ver = "?"

        r3d_line = ""
        try:
            from ..core.r3d import decoder_kind, sdk_version
            from ..core.r3d import is_available as r3d_available

            if r3d_available():
                ver = sdk_version() or "loaded"
                kind = decoder_kind()
                gpu = {
                    "metal": " · Metal GPU",
                    "cuda": " · CUDA GPU",
                    "opencl": " · OpenCL GPU",
                }.get(kind, "")
                r3d_line = f"<br>RED R3D SDK: {ver}{gpu}"
            else:
                r3d_line = "<br>RED R3D: not available (optional)"
        except Exception:
            r3d_line = ""

        braw_line = ""
        try:
            from ..core.braw import decoder_kind as braw_kind
            from ..core.braw import is_available as braw_available
            from ..core.braw import sdk_version as braw_sdk_version

            if braw_available():
                ver = braw_sdk_version() or "loaded"
                kind = braw_kind()
                gpu = {
                    "metal": " · Metal GPU",
                    "cuda": " · CUDA GPU",
                    "opencl": " · OpenCL GPU",
                    "cpu": " · CPU",
                }.get(kind, "")
                braw_line = f"<br>Blackmagic RAW SDK: {ver}{gpu}"
            else:
                braw_line = "<br>Blackmagic RAW: not available (optional)"
        except Exception:
            braw_line = ""

        plugin_lines = ""
        try:
            from html import escape

            from ..core.source_plugins import about_lines as plugin_about_lines

            plugin_lines = "".join(f"<br>{escape(line)}" for line in plugin_about_lines())
        except Exception:
            plugin_lines = ""

        deps = (
            f"Python {sys.version.split()[0]} · "
            f"PySide6 {__import__('PySide6').__version__}<br>"
            f"OpenColorIO {OCIO_mod.GetVersion()} · "
            f"OpenImageIO {oiio_ver}"
            f"{r3d_line}"
            f"{braw_line}"
            f"{plugin_lines}"
        )

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(
            f"<p style='text-align:center;'>{deps}</p>"
            "<hr>"
            "<p>by <b>Derek Rein</b></p>"
            '<p><a href="https://derekvfx.ca">derekvfx.ca</a> &nbsp;|&nbsp; '
            '<a href="https://vfx.systems">vfx.systems</a></p>'
            "<hr>"
            "<p><b>Bundled OCIO config:</b> ACES Studio Config v4 (ACES 2.0)<br>"
            "Sourced from <a href='https://github.com/AcademySoftwareFoundation/OpenColorIO-Config-ACES/releases'>"
            "AcademySoftwareFoundation/OpenColorIO-Config-ACES</a> (BSD-3-Clause).<br>"
            "Contains official camera IDTs for <b>Apple Log</b> (iPhone cinematic/ProRes Log), "
            "RED, Sony, DJI and many more, plus specification transforms for "
            "<b>Nikon N-Log</b>, Fujifilm F-Log / F-Log2 / F-Log2 C, Leica L-Log, "
            "Sony S-Log / S-Log2, KineLOG3, "
            "GoPro Protune / GP-Log / GP-Log2, Blackmagic Film Gen 4, "
            "OPPO O-Log, and Apple Log 2.</p>"
            f"<p style='font-size:10px;'>"
            f"MIT License &copy; {datetime.now().year} Derek Rein<br><br>"
            "Permission is hereby granted, free of charge, to any person obtaining "
            "a copy of this software and associated documentation files, to deal in "
            "the Software without restriction, including without limitation the "
            "rights to use, copy, modify, merge, publish, distribute, sublicense, "
            "and/or sell copies of the Software, subject to the above copyright "
            "notice and this permission notice being included in all copies.</p>"
            "<p style='font-size:10px;'>"
            "This product includes DNG technology under license by Adobe. "
            "CinemaDNG frames are read by a small decoder in this app, "
            "not the Adobe DNG SDK.</p>"
            "<p style='font-size:10px;'>Invisible watermarks use "
            "<a href='https://github.com/adobe/trustmark'>Adobe TrustMark</a> (MIT) "
            "through ONNX Runtime (MIT). The encoder model downloads on first use. "
            "Notices are in THIRD_PARTY_NOTICES.md in the source tree.</p>"
            "<p style='font-size:10px;'>Application source is MIT-licensed. "
            "Optional RED Redistributable libraries and Blackmagic RAW runtime "
            "libraries (when present) remain under their proprietary SDK licenses "
            "— use the buttons below.</p>"
        )
        body.setReadOnly(True)
        layout.addWidget(body, 1)

        notice_row = QHBoxLayout()
        red_btn = QPushButton("RED redistributable notice…")
        red_btn.setToolTip("End-user terms for optional RED R3D / N-RAW libraries")
        red_btn.clicked.connect(self._show_red_notice)
        notice_row.addWidget(red_btn)
        braw_btn = QPushButton("Blackmagic RAW notice…")
        braw_btn.setToolTip("End-user terms for optional Blackmagic RAW libraries")
        braw_btn.clicked.connect(self._show_braw_notice)
        notice_row.addWidget(braw_btn)
        try:
            from ..core.source_plugins import notices as plugin_notices

            for title_text, notice_text in plugin_notices():
                btn = QPushButton(f"{title_text}\u2026")
                btn.clicked.connect(
                    lambda _checked=False, t=title_text, n=notice_text: self._show_notice(t, n)
                )
                notice_row.addWidget(btn)
        except Exception:
            pass
        layout.addLayout(notice_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _show_notice(self, title: str, notice: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(480, 360)
        lay = QVBoxLayout(dlg)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(notice.strip())
        lay.addWidget(text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def _show_red_notice(self) -> None:
        from ..core.r3d import RED_REDISTRIBUTABLE_NOTICE

        dlg = QDialog(self)
        dlg.setWindowTitle("RED R3D redistributable notice")
        dlg.setMinimumSize(480, 360)
        lay = QVBoxLayout(dlg)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(RED_REDISTRIBUTABLE_NOTICE.strip())
        lay.addWidget(text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def _show_braw_notice(self) -> None:
        from ..core.braw import BMD_REDISTRIBUTABLE_NOTICE

        dlg = QDialog(self)
        dlg.setWindowTitle("Blackmagic RAW redistributable notice")
        dlg.setMinimumSize(480, 360)
        lay = QVBoxLayout(dlg)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(BMD_REDISTRIBUTABLE_NOTICE.strip())
        lay.addWidget(text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EXR Converter")
        # macOS Dock icon comes from the bundle .icns; a runtime PNG override
        # replaces the system tile and shows sharp square corners while running.
        if sys.platform != "darwin":
            self.setWindowIcon(QIcon(":/icon.png"))
        self.setMinimumSize(700, 640)
        self.setAcceptDrops(True)
        from ..services.app_settings import get_app_settings

        # Process-wide settings façade (single QSettings backend).
        self._app_settings = get_app_settings()
        self._settings = self._app_settings.qsettings
        set_decode_mode(decoder_mode(self._settings))
        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None
        self._ocio_cfg = None
        # "convert" | None — keeps progress UI from fighting itself.
        self._busy: str | None = None
        # Standalone sequence player from post-convert Open result (Video → EXR).
        self._result_player_win: QDialog | None = None

        motion.apply_saved_preference(self._settings)
        self._build_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("logSplitter")
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(12, 10, 12, 8)
        top_layout.setSpacing(8)

        self._ocio_panel = OcioConfigPanel(self._settings)
        top_layout.addWidget(self._ocio_panel)

        self._tabs = QTabWidget()
        self._tabs.setTabBar(SlidingTabBar())
        self._tabs.setMinimumHeight(CONVERT_TABS_MIN_HEIGHT)
        self._v2e_tab = ConvertTab("video2exr", self._settings)
        self._e2v_tab = ConvertTab("exr2video", self._settings)
        self._tabs.addTab(self._v2e_tab, "Video \u2192 EXR")
        self._proxy_tab = ProxyTab(self._settings)
        self._tabs.addTab(self._e2v_tab, "EXR \u2192 Video")
        self._tabs.addTab(self._proxy_tab, "Video \u2192 Proxy")
        self._transcode_tab = TranscodeTab(self._settings)
        self._tabs.addTab(self._transcode_tab, "Transcode")
        saved_tab = int(self._settings.value("ui/tab", 0))
        if 0 <= saved_tab < self._tabs.count():
            self._tabs.setCurrentIndex(saved_tab)
        top_layout.addWidget(self._tabs, 1)

        # Action bar. A thin progress line runs along its top edge while a job
        # is active; under it sit the When done options, Cancel, and Convert.
        self._progress = ProgressLine()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setObjectName("convertProgress")
        self._progress.setAccessibleName("Conversion progress")
        self._progress.setVisible(False)
        # The slot keeps its 3 px whether or not the line shows: no layout shift.
        progress_slot = QWidget()
        progress_slot.setFixedHeight(PROGRESS_LINE_HEIGHT)
        slot_layout = QHBoxLayout(progress_slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        slot_layout.addWidget(self._progress)
        top_layout.addWidget(progress_slot)

        prog_row = QHBoxLayout()
        prog_row.setContentsMargins(0, 0, 0, 0)
        prog_row.setSpacing(8)
        self._go = PrimaryButton("Convert")
        self._go.setObjectName("convertBtn")
        convert_keys = QKeySequence("Ctrl+Return")
        native_keys = convert_keys.toString(QKeySequence.SequenceFormat.NativeText)
        self._go.setToolTip(f"Start the conversion ({native_keys})")
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        lock_form_row_height(self._go)
        lock_form_row_height(self._cancel_btn)

        # Post-convert actions (persisted). Hidden on the batch tabs (Proxy, Transcode).
        self._after_row_widget = QWidget()
        after_row = QHBoxLayout(self._after_row_widget)
        after_row.setContentsMargins(0, 0, 0, 0)
        after_row.setSpacing(14)
        after_lbl = QLabel("When done")
        set_style_property(after_lbl, "role", "muted")
        after_row.addWidget(after_lbl)
        self._copy_path_cb = QCheckBox("Copy path")
        self._copy_path_cb.setToolTip(
            "Copy the output path to the clipboard (Nuke-style #### pattern for EXR sequences)."
        )
        # Default ON for new installs; persisted after first toggle.
        self._copy_path_cb.setChecked(self._settings.value("ui/copy_path_after", True, type=bool))
        self._copy_path_cb.toggled.connect(
            lambda v: self._settings.setValue("ui/copy_path_after", v)
        )
        after_row.addWidget(self._copy_path_cb)
        self._open_after_cb = QCheckBox("Open result")
        self._open_after_cb.setToolTip(
            "EXR → Video: open the finished file with the preferred player "
            "(File → Preferences; default is the built-in player).\n"
            "Video → EXR: open the sequence in the built-in player window "
            "(with playback cache)."
        )
        self._open_after_cb.setChecked(self._settings.value("ui/open_after", False, type=bool))
        self._open_after_cb.toggled.connect(lambda v: self._settings.setValue("ui/open_after", v))
        after_row.addWidget(self._open_after_cb)
        self._show_folder_cb = QCheckBox("Show in folder")
        self._show_folder_cb.setToolTip(
            "Reveal the output file (or EXR sequence folder) in the system file manager."
        )
        self._show_folder_cb.setChecked(
            self._settings.value("ui/show_folder_after", False, type=bool)
        )
        self._show_folder_cb.toggled.connect(
            lambda v: self._settings.setValue("ui/show_folder_after", v)
        )
        after_row.addWidget(self._show_folder_cb)
        prog_row.addWidget(self._after_row_widget)
        prog_row.addStretch(1)
        prog_row.addWidget(self._cancel_btn)
        prog_row.addWidget(self._go)
        top_layout.addLayout(prog_row)

        splitter.addWidget(top)

        # Log drawer. Closed, it is one line: a toggle and the latest status.
        # Open, the full log shows under it and the splitter sets its height.
        log_container = QWidget()
        log_container.setObjectName("logDrawer")
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(12, 4, 12, 6)
        log_layout.setSpacing(4)
        log_header = QHBoxLayout()
        log_header.setSpacing(8)
        self._log_toggle = QPushButton("Log")
        self._log_toggle.setCheckable(True)
        self._log_toggle.setObjectName("logToggle")
        self._log_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_toggle.setToolTip("Show or hide the log")
        set_style_property(self._log_toggle, "variant", "ghost")
        log_header.addWidget(self._log_toggle)
        # Stands in for the old status bar: same showMessage() calls.
        self._statusbar = StatusLine()
        log_header.addWidget(self._statusbar, 1)
        self._clear_log = QPushButton("Clear")
        self._clear_log.setObjectName("clearBtn")
        set_style_property(self._clear_log, "variant", "ghost")
        log_header.addWidget(self._clear_log)
        log_layout.addLayout(log_header)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(5000)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(11)
        self._log.setFont(mono)
        self._log.setObjectName("logPane")
        self._log.setMinimumHeight(LOG_PANE_MIN_HEIGHT)
        log_layout.addWidget(self._log, 1)
        self._log_container = log_container
        splitter.addWidget(log_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        self._log_splitter = splitter
        self._log_height = Animator(self, self._apply_log_height, duration_ms=SLOW)
        self._log_toggle.toggled.connect(self._set_log_open)

        self._ocio_panel.config_changed.connect(self._reload_ocio)
        self._go.clicked.connect(self._start)
        self._cancel_btn.clicked.connect(self._cancel_run)
        self._clear_log.clicked.connect(self._log.clear)
        self._tabs.currentChanged.connect(lambda i: self._settings.setValue("ui/tab", i))
        self._tabs.currentChanged.connect(lambda _: self._update_go_state())
        self._tabs.currentChanged.connect(lambda _: self._sync_proxy_tab_chrome())
        self._shown_tab = self._tabs.currentIndex()
        self._tabs.currentChanged.connect(self._crossfade_tab)
        self._v2e_tab.log_message.connect(self._append_log)
        self._e2v_tab.log_message.connect(self._append_log)
        self._v2e_tab.readiness_changed.connect(lambda _: self._update_go_state())
        self._e2v_tab.readiness_changed.connect(lambda _: self._update_go_state())
        for batch_tab in (self._proxy_tab, self._transcode_tab):
            batch_tab.log_message.connect(self._append_log)
            batch_tab.readiness_changed.connect(lambda _: self._update_go_state())
        self._proxy_tab.progress.connect(
            lambda cur, total: self._on_batch_progress(self._proxy_tab, cur, total)
        )
        self._proxy_tab.busy_changed.connect(
            lambda busy: self._on_batch_busy_changed(self._proxy_tab, busy)
        )
        self._transcode_tab.progress.connect(
            lambda cur, total: self._on_batch_progress(self._transcode_tab, cur, total)
        )
        self._transcode_tab.busy_changed.connect(
            lambda busy: self._on_batch_busy_changed(self._transcode_tab, busy)
        )
        self._update_go_state()
        self._sync_proxy_tab_chrome()

        QShortcut(convert_keys, self).activated.connect(self._start_from_shortcut)
        for index in range(self._tabs.count()):
            QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self).activated.connect(
                lambda i=index: self._tabs.setCurrentIndex(i)
            )

        self._reload_ocio()

        geom = self._settings.value("ui/geometry")
        if geom:
            self.restoreGeometry(geom)
        else:
            # First launch: tall enough that the EXR and video tabs do not scroll.
            self.resize(880, 860)
        self._log_open_height = int(self._settings.value("ui/log_height", LOG_OPEN_HEIGHT))
        self._set_log_open(self._settings.value("ui/log_open", False, type=bool), animate=False)

    def apply_startup(
        self,
        open_path: str | None = None,
        ocio_path: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Apply GUI launch options (CLI ``--open`` / ``--gui-ocio`` / ``--mode``).

        Used by the bare ``main.py`` entry and by the Nuke menu integration.
        """
        if ocio_path:
            ok = self._ocio_panel.set_custom_config_file(ocio_path)
            if ok:
                self._append_log(f"OCIO config (launch): {ocio_path}")
                self._reload_ocio()
            else:
                self._append_log(f"OCIO config not found (launch ignored): {ocio_path}")

        open_path = (open_path or "").strip()
        mode_norm = (mode or "auto").strip().lower()
        if mode_norm in ("video2exr", "v2e", "video"):
            self._tabs.setCurrentIndex(0)
        elif mode_norm in ("exr2video", "e2v", "exr"):
            self._tabs.setCurrentIndex(1)
        elif mode_norm in ("proxy", "video2proxy"):
            self._tabs.setCurrentIndex(PROXY_TAB_INDEX)
        elif mode_norm in ("transcode", "video2video"):
            self._tabs.setCurrentIndex(TRANSCODE_TAB_INDEX)
        elif open_path:
            # Prefer extension over filesystem existence (Nuke may pass paths
            # that are not yet fully visible, or sequence tokens).
            p = Path(open_path.split()[0] if open_path else "")
            name = p.name.lower()
            if not is_ignored_media_filename(p.name) and (
                p.suffix.lower() in self._video_exts()
                or any(name.endswith(ext) for ext in self._video_exts())
            ):
                self._tabs.setCurrentIndex(0)
            else:
                # Directory, image sequence frame, or pattern → EXR → Video tab
                self._tabs.setCurrentIndex(1)

        if open_path:
            open_name = Path(open_path.split()[0]).name
            if is_ignored_media_filename(open_name):
                self._append_log(f"Ignored OS metadata path on launch (not media): {open_name}")
            elif (batch := self._batch_tab()) is not None:
                batch.add_paths([open_path])
                self._append_log(f"Opened (launch): {open_path}")
            else:
                # Prevent deferred QSettings restore from overwriting --open / Nuke.
                self._v2e_tab.suppress_saved_input_restore()
                self._e2v_tab.suppress_saved_input_restore()
                tab = self._active_tab()
                # Async probe so large MXFs / sequences don't freeze launch.
                tab.set_input_async(open_path)
                self._append_log(f"Opened (launch): {open_path}")

    # -- Menu bar --

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()
        mb.setNativeMenuBar(False)

        file_menu = mb.addMenu("&File")
        prefs_action = QAction("&Preferences\u2026", self)
        prefs_action.setShortcut(QKeySequence.StandardKey.Preferences)
        prefs_action.triggered.connect(self._open_preferences)
        file_menu.addAction(prefs_action)
        if sys.platform == "darwin":
            ql_action = QAction("&Quick Look\u2026", self)
            ql_action.setToolTip("Spacebar previews and Finder thumbnails for EXR and DPX")
            ql_action.triggered.connect(self._open_quicklook)
            file_menu.addAction(ql_action)
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        self._presets_menu = mb.addMenu("&Presets")
        self._presets_menu.aboutToShow.connect(self._populate_presets_menu)

        help_menu = mb.addMenu("&Help")

        docs_action = QAction("&Documentation", self)
        docs_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        docs_action.setToolTip(DOCS_URL)
        docs_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)))
        help_menu.addAction(docs_action)
        help_menu.addSeparator()

        update_action = QAction("Check for &Updates\u2026", self)
        update_action.triggered.connect(self._check_for_updates)
        help_menu.addAction(update_action)

        about_action = QAction("&About EXR Converter", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        help_menu.addSeparator()

        version_action = QAction(f"Version {APP_VERSION}", self)
        version_action.setEnabled(False)
        help_menu.addAction(version_action)

    def _show_about(self) -> None:
        dlg = AboutDialog(self)
        dlg.exec()

    def _open_preferences(self) -> None:
        dlg = PreferencesDialog(self._settings, self)
        dlg.exec()

    def _open_quicklook(self) -> None:
        from .quicklook_dialog import QuickLookDialog

        QuickLookDialog(self._settings, self).exec()

    # -- Updates --

    def _is_convert_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _check_for_updates(self) -> None:
        """Open the latest GitHub release page in the system browser."""
        url = QUrl(f"https://github.com/{GITHUB_REPO}/releases/latest")
        QDesktopServices.openUrl(url)

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
            try:
                state = self.snapshot_state()
                save_preset(name.strip(), state)
                self._append_log(f"Preset saved: {name.strip()}")
            except ValueError as e:
                QMessageBox.warning(self, "Save Preset", str(e))

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
        from ..core.constants import (
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
        if self._is_convert_running():
            self._append_log("OCIO reload deferred — conversion in progress")
            self._statusbar.showMessage("Cannot reload OCIO during conversion", 4000)
            return
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
        cs, cp = config_source_info(
            self._ocio_panel.current_source_key(),
            self._ocio_panel._file_path,
        )
        self._transcode_tab.populate_spaces(
            families, ocio_cfg=cfg, config_source=cs, config_path=cp
        )
        self._update_go_state()
        # Surface invalid color spaces after a config switch.
        tab = self._active_tab()
        bad: list[str] = []
        if not tab.src_btn.is_valid():
            bad.append("source")
        if not tab.dst_btn.is_valid():
            bad.append("destination")
        if bad:
            self._statusbar.showMessage(
                f"OCIO loaded — pick a new {' & '.join(bad)} color space",
                6000,
            )
        else:
            self._statusbar.showMessage(f"OCIO: {n_spaces} color spaces loaded", 3000)
        self._append_log(f"OCIO config loaded ({n_spaces} spaces)")
        schedule_quicklook_view_lut(cfg)

    # -- Log --

    def _append_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    # -- Convert --

    def _active_tab(self) -> ConvertTab:
        """Active OCIO convert tab (the proxy tab falls back to EXR → Video)."""
        return self._v2e_tab if self._tabs.currentIndex() == 0 else self._e2v_tab

    def _is_proxy_tab(self) -> bool:
        return self._tabs.currentIndex() == PROXY_TAB_INDEX

    def _is_transcode_tab(self) -> bool:
        return self._tabs.currentIndex() == TRANSCODE_TAB_INDEX

    def _batch_tab(self) -> ProxyTab | TranscodeTab | None:
        """The current tab when it is a queued batch tab (Proxy / Transcode)."""
        if self._is_proxy_tab():
            return self._proxy_tab
        if self._is_transcode_tab():
            return self._transcode_tab
        return None

    def _crossfade_tab(self, index: int) -> None:
        """Fade a picture of the page being left out over the page being shown."""
        previous = self._tabs.widget(self._shown_tab)
        self._shown_tab = index
        current = self._tabs.widget(index)
        if previous is None or current is None or previous is current or not self.isVisible():
            return
        crossfade_from(previous.grab(), current)

    # -- Log drawer --

    def _log_header_height(self) -> int:
        margins = self._log_container.layout().contentsMargins()
        return self._log_toggle.sizeHint().height() + margins.top() + margins.bottom()

    def _apply_log_height(self, height: float) -> None:
        total = sum(self._log_splitter.sizes()) or self._log_splitter.height()
        h = int(round(height))
        self._log_splitter.setSizes([max(0, total - h), h])

    def _set_log_open(self, is_open: bool, *, animate: bool = True) -> None:
        """Open or close the log drawer; the open height is remembered."""
        if self._log_toggle.isChecked() != is_open:
            self._log_toggle.blockSignals(True)
            self._log_toggle.setChecked(is_open)
            self._log_toggle.blockSignals(False)
        closed = self._log_header_height()
        container = self._log_container
        if is_open:
            self._log.setVisible(True)
            self._clear_log.setVisible(True)
            container.setMinimumHeight(0)
            container.setMaximumHeight(16_777_215)
            target = max(self._log_open_height, closed + LOG_PANE_MIN_HEIGHT + 4)
            self._log_height.set(float(container.height() or closed))
            self._log_height.to(float(target), None if animate else 0)
        else:
            if container.height() > closed + LOG_PANE_MIN_HEIGHT:
                self._log_open_height = container.height()
            self._log.setVisible(False)
            self._clear_log.setVisible(False)
            # Fixed at one line: nothing to drag while the pane is hidden.
            container.setFixedHeight(closed)
        set_button_icon_keep_text(self._log_toggle, "chevron-down" if is_open else "chevron-right")

    def _start_from_shortcut(self) -> None:
        """Ctrl+Return: same as clicking Convert, and only when it is enabled."""
        if self._go.isEnabled():
            self._start()

    def _update_go_state(self) -> None:
        """Enable Convert only when idle and the active tab is ready.

        The batch tabs queue jobs, so Convert stays enabled while they run.
        """
        batch = self._batch_tab()
        if batch is not None:
            self._go.setEnabled(batch.is_ready())
        else:
            busy = self._is_convert_running() or self._busy is not None
            self._go.setEnabled(not busy and self._active_tab().is_ready())
        self._update_cancel_state()

    def _update_cancel_state(self) -> None:
        batch = self._batch_tab()
        if batch is not None:
            self._cancel_btn.setEnabled(batch.is_busy())
        else:
            self._cancel_btn.setEnabled(self._is_convert_running() or self._busy == "convert")
        # The bar shows exactly while there is something to cancel.
        self._progress.setVisible(self._cancel_btn.isEnabled())

    def _sync_proxy_tab_chrome(self) -> None:
        """Post-convert actions apply to single convert jobs, not batch tabs."""
        self._after_row_widget.setVisible(self._batch_tab() is None)

    def _on_batch_progress(self, tab: ProxyTab | TranscodeTab, cur: int, total: int) -> None:
        if self._busy is not None or self._batch_tab() is not tab:
            return
        if total > 0:
            self._progress.setValue(int(100 * cur / total))
        pending = tab.queue.pending()
        more = f" \u00b7 {pending - 1} more queued" if pending > 1 else ""
        noun = "Proxy" if tab is self._proxy_tab else "Transcode"
        self._statusbar.showMessage(f"{noun} frame {cur} / {total}{more}")

    def _on_batch_busy_changed(self, tab: ProxyTab | TranscodeTab, busy: bool) -> None:
        if not busy and self._busy is None:
            if self._batch_tab() is tab:
                self._progress.setValue(0)
            q = tab.queue
            if tab is self._proxy_tab:
                msg = f"Proxies done ({q.completed} made, {q.failed} failed)."
            else:
                msg = f"Transcodes done ({q.completed} made, {q.failed} failed)."
            self._statusbar.showMessage(msg, 6000)
        self._update_go_state()

    def _start(self) -> None:
        batch = self._batch_tab()
        if batch is not None:
            batch.start_batch()
            self._update_go_state()
            return
        if self._is_convert_running():
            self._append_log("Conversion already running — ignore start.")
            return
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
        if not tab.src_btn.is_valid() or not tab.dst_btn.is_valid() or not src or not dst:
            QMessageBox.warning(
                self,
                "Color spaces",
                "Source and/or destination color space is missing or not in the "
                "current OCIO config. Pick valid spaces before converting.",
            )
            return

        cs, cp = config_source_info(
            self._ocio_panel.current_source_key(),
            self._ocio_panel._file_path,
        )

        frame_range_str = tab.get_frame_range()
        frame_set: set[int] | None = None
        if frame_range_str:
            from ..core.framerange import parse_frame_range

            try:
                frame_set = set(parse_frame_range(frame_range_str))
            except ValueError as e:
                QMessageBox.warning(self, "Frame range", f"Invalid frame range: {e}")
                return
            if not frame_set:
                frame_set = None

        # -- Slate / burn-in / watermark: EXR → video only (never Video → EXR) --
        slate_np = None
        overlay_np = None
        slate_overlay_np = None
        overlay_provider = None
        if mode == "exr2video" and tab.slate_enabled():
            slate_data = tab.get_slate_data()
            if slate_data is not None:
                from ..render.slate import render_slate_frame

                sw, sh = self._detect_slate_resolution(mode, inp)
                slate_par = self._output_pixel_aspect(inp, tab)
                thumb_b64 = tab.get_slate_thumbnail_b64()
                # If the slate dialog was never opened, still extract a thumb
                # from the known EXR frame list (first / mid / last).
                if not thumb_b64 and inp:
                    try:
                        from .preferences import thumbnail_frame_choice
                        from .slate_widgets import extract_thumbnail_b64

                        thumb_b64 = extract_thumbnail_b64(
                            inp,
                            "exr2video",
                            which=thumbnail_frame_choice(self._settings),
                            ocio_cfg=self._ocio_cfg,
                            src_space=tab.src_btn.current_space() or "",
                        )
                        if thumb_b64 and tab._slate_model is not None:
                            tab._slate_model.set_thumbnail_b64(thumb_b64)
                    except Exception as e:
                        self._append_log(f"Slate thumbnail extract skipped: {e}")
                self._append_log(f"Rendering slate frame ({sw}\u00d7{sh})\u2026")
                try:
                    slate_np = render_slate_frame(
                        slate_data, sw, sh, thumbnail_b64=thumb_b64, pixel_aspect=slate_par
                    )
                    if thumb_b64:
                        self._append_log("Slate frame rendered (with thumbnail)")
                    else:
                        self._append_log("Slate frame rendered (no thumbnail)")
                except Exception as e:
                    QMessageBox.warning(self, "Slate Error", f"Failed to render slate: {e}")
                    return

        self._busy = "convert"
        self._go.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress.setVisible(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")

        # Burn-in and watermark are stamped on shot frames only; the slate is
        # left clean (matching the live preview). Overlay is sRGB uint8 RGBA —
        # convert.py linearises once and composites in working space.
        if mode == "exr2video":
            overlay_np, slate_overlay_np, overlay_provider = self._build_overlays(
                tab, inp, frame_range_str, frame_set
            )

        if mode == "video2exr":
            # Sequence name + pad from the output field (name.####.exr).
            # Empty name → convert falls back to the video stem.
            try:
                out_name = tab.get_output_sequence_name()
            except Exception:
                out_name = ""
            try:
                pattern_pad = tab.get_output_sequence_padding()
            except Exception:
                pattern_pad = None
            pad = int(pattern_pad) if pattern_pad is not None else tab.get_padding()
            # ocio_cfg intentionally omitted — worker rebuilds from config_source/path.
            kwargs = dict(
                video_path=inp,
                output_dir=Path(out),
                src_space=src,
                dst_space=dst,
                compression=tab.get_compression(),
                config_source=cs,
                config_path=cp,
                scale=tab.get_scale(),
                padding=pad,
                start_frame=tab.get_start_frame(),
                frame_set=frame_set,
                exr_opts=tab.get_exr_opts() or None,
                output_name=out_name,
                pixel_aspect=tab.pixel_aspect_override(),
            )
        else:
            _codec_key, _codec, _pix = tab.get_video_codec_info()
            kwargs = dict(
                input_spec=inp,
                output_video=Path(out),
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
                overlay_provider=overlay_provider,
                codec_opts=tab.get_codec_opts() or None,
                encoder=encoder_mode(self._settings),
                pixel_aspect=tab.pixel_aspect_override(),
            )
            if tab.invisible_watermark_enabled():
                wm_text, wm_strength = resolve_invisible_watermark(
                    self._settings, source_name=sequence_display_name(inp), log=self._append_log
                )
                if wm_text:
                    kwargs["watermark_text"] = wm_text
                    kwargs["watermark_strength"] = wm_strength

        out_path = Path(out)
        self._output_folder = str(out_path if out_path.is_dir() else out_path.parent)
        # Paths used by post-convert actions (clipboard / open).
        self._output_mode = mode
        self._output_file = str(out_path)  # video file or EXR directory
        # Prefer the Nuke-style #### pattern shown in the field for sequences.
        display_out = tab.output_path.text().strip()
        self._output_clipboard = display_out or str(out_path)
        # Video → EXR open-result: files are ``{output_name}.{frame}.exr``.
        self._output_seq_dir = ""
        self._output_seq_stem = ""
        self._output_seq_pad = 4
        self._output_seq_start = 1001
        self._output_dst_space = dst
        self._output_fps = 24.0
        if mode == "video2exr":
            self._output_seq_dir = str(out_path)
            written_name = str(kwargs.get("output_name") or Path(inp).stem)
            self._output_seq_stem = written_name
            self._output_seq_pad = int(kwargs.get("padding") or tab.get_padding())
            self._output_seq_start = int(tab.get_start_frame())
            vi = getattr(tab, "_video_info", None)
            if vi is not None and getattr(vi, "fps", 0) and float(vi.fps) > 0:
                self._output_fps = float(vi.fps)

        self._append_log(f"--- {mode} ---")
        self._thread = QThread(self)
        self._worker = ConvertWorker(mode, kwargs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._append_log)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.finished_ok.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        worker_ref = self._worker
        thr_ref = self._thread
        self._thread.finished.connect(worker_ref.deleteLater)
        self._thread.finished.connect(thr_ref.deleteLater)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_progress(self, cur: int, total: int) -> None:
        if self._busy != "convert":
            return
        if total > 0:
            self._progress.setValue(int(100 * cur / total))
        self._statusbar.showMessage(f"Frame {cur} / {total}")

    def _on_failed(self, msg: str) -> None:
        self._busy = None
        self._progress.setValue(0)
        self._statusbar.showMessage("Conversion failed.")
        # The details are in the log: show it rather than leave the drawer shut.
        self._set_log_open(True)
        QMessageBox.critical(self, "Error", msg)
        self._update_go_state()

    def _on_cancelled(self) -> None:
        """User hit Cancel — quiet UI, no error dialog."""
        self._busy = None
        self._progress.setValue(0)
        self._statusbar.showMessage("Cancelled.", 4000)
        self._update_go_state()

    def _on_done(self) -> None:
        self._busy = None
        self._progress.setValue(100)
        self._update_go_state()

        notes: list[str] = []
        clip = getattr(self, "_output_clipboard", "") or ""
        if self._copy_path_cb.isChecked() and clip:
            QGuiApplication.clipboard().setText(clip)
            notes.append("path copied")
            self._append_log(f"Copied to clipboard: {clip}")

        mode = getattr(self, "_output_mode", "")
        target = getattr(self, "_output_file", "") or ""
        folder = getattr(self, "_output_folder", None)

        if self._open_after_cb.isChecked():
            if mode == "exr2video" and target and Path(target).is_file():
                try:
                    if player_mode(self._settings) == PLAYER_MODE_BUILTIN:
                        msg = self._open_video_result(target)
                    else:
                        msg = open_video_with_player(target, self._settings)
                    notes.append(msg)
                    self._append_log(f"{msg.capitalize()}: {target}")
                except OSError as e:
                    self._append_log(f"Could not open player: {e}")
                except Exception as e:
                    self._append_log(f"Could not open video player: {e}")
            elif mode == "video2exr" and target:
                try:
                    msg = self._open_sequence_result(target)
                    notes.append(msg)
                    self._append_log(f"{msg.capitalize()}: {target}")
                except Exception as e:
                    self._append_log(f"Could not open sequence player: {e}")

        if self._show_folder_cb.isChecked():
            reveal_target = target if (target and Path(target).exists()) else folder
            if reveal_target:
                try:
                    msg = reveal_in_file_manager(reveal_target)
                    notes.append(msg.split(":")[0] if ":" in msg else msg)
                    self._append_log(msg)
                except OSError as e:
                    self._append_log(f"Could not show in folder: {e}")

        status = "Done."
        if notes:
            status = f"Done — {', '.join(notes)}."
        self._statusbar.showMessage(status, 6000)

    def _cleanup_thread(self) -> None:
        self._worker = None
        self._thread = None
        if self._busy == "convert":
            self._busy = None
        self._update_go_state()

    def _resolve_v2e_open_path(self, fallback: str) -> str:
        """First-frame path for the sequence just written by Video → EXR.

        Convert always names files ``{video_stem}.{frame:0N}.exr`` under the
        output directory. Opening the directory alone is wrong when other
        sequences already live there (``find_exr_sequence`` picks sorted[0]).
        """
        folder = str(getattr(self, "_output_seq_dir", "") or fallback or "")
        stem = str(getattr(self, "_output_seq_stem", "") or "")
        pad = int(getattr(self, "_output_seq_pad", 4) or 4)
        start = int(getattr(self, "_output_seq_start", 1001) or 1001)
        if not folder:
            return fallback
        if stem:
            first = Path(folder) / f"{stem}.{start:0{pad}d}.exr"
            if first.is_file():
                return str(first)
            # Frame range / start may not match start_frame (trimmed range).
            # Prefer any sequence whose basename matches the video stem.
            try:
                from ..core.sequence import scan_exr_sequences

                for row in scan_exr_sequences(folder):
                    if row.get("name") == stem and row.get("first_frame"):
                        return str(row["first_frame"])
            except Exception:
                pass
        return folder

    def _open_sequence_result(self, path: str) -> str:
        """Open a converted EXR (or image) sequence in the built-in player window.

        Colour pipeline (matches convert write):

        * EXR pixels are already in convert **destination** space (e.g. ACEScg).
        * Player treats that as source → OCIO to compositing/working space →
          display/view (GPU when available).

        Do **not** trust ``oiio:ColorSpace`` alone (OIIO often tags any
        scene-linear EXR as ``lin_rec709``).
        """
        open_path = self._resolve_v2e_open_path(path)
        # Known write colorspace from convert start (authoritative).
        src_cs = str(getattr(self, "_output_dst_space", "") or "")
        if not src_cs:
            try:
                src_cs = str(self._v2e_tab.dst_btn.current_space() or "")
            except Exception:
                src_cs = ""
        # Fall back to our EXR attribute (not mangled oiio:ColorSpace).
        if not src_cs and open_path:
            try:
                from ..core.sequence import probe_pixel_colorspace

                src_cs = probe_pixel_colorspace(open_path)
            except Exception:
                src_cs = ""
        src_cs = self._resolve_player_src_colorspace(src_cs)
        self._log_player_ocio("Sequence player", src_cs)
        fps = float(getattr(self, "_output_fps", 0) or 0) or 24.0
        return self._show_result_player(
            open_path,
            src_colorspace=src_cs,
            fps=fps,
            media_kind="sequence",
            status_label="sequence player",
        )

    def _open_video_result(self, path: str) -> str:
        """Open a converted video in the built-in player (Preferences: built-in).

        Encode destination space is used as OCIO source (same as convert write).
        """
        src_cs = str(getattr(self, "_output_dst_space", "") or "")
        if not src_cs:
            try:
                src_cs = str(self._e2v_tab.dst_btn.current_space() or "")
            except Exception:
                src_cs = ""
        src_cs = self._resolve_player_src_colorspace(src_cs)
        self._log_player_ocio("Video player", src_cs)
        fps = float(getattr(self, "_output_fps", 0) or 0) or 0.0
        return self._show_result_player(
            path,
            src_colorspace=src_cs,
            fps=fps,
            media_kind="video",
            status_label="built-in player",
        )

    def _resolve_player_src_colorspace(self, src_cs: str) -> str:
        if src_cs and self._ocio_cfg is not None:
            try:
                from ..core.ocio_utils import find_equivalent_space

                resolved = find_equivalent_space(self._ocio_cfg, src_cs)
                if resolved:
                    return resolved
            except Exception:
                pass
        return src_cs

    def _log_player_ocio(self, label: str, src_cs: str) -> None:
        if self._ocio_cfg is None:
            self._append_log(
                f"{label}: no OCIO config — frames will show without a display transform."
            )
            return
        try:
            from ..core.ocio_utils import get_compositing_space

            working = get_compositing_space(self._ocio_cfg)
            self._append_log(f"{label} OCIO: {src_cs or '?'} → {working} → display/view")
        except Exception:
            self._append_log(f"{label} OCIO source: {src_cs or '?'}")

    def _show_result_player(
        self,
        open_path: str,
        *,
        src_colorspace: str,
        fps: float,
        media_kind: str,
        status_label: str,
    ) -> str:
        """Show or reuse the standalone :class:`SequencePlayerWindow`."""
        from .player.player_window import SequencePlayerWindow

        win = self._result_player_win
        if win is not None:
            try:
                _ = win.isVisible()
                win.reload(
                    open_path,
                    ocio_cfg=self._ocio_cfg,
                    src_colorspace=src_colorspace,
                    fps=fps,
                    media_kind=media_kind,
                )
                win.show()
                win.raise_()
                win.activateWindow()
                return f"opened in {status_label} ({src_colorspace or 'no OCIO src'})"
            except RuntimeError:
                self._result_player_win = None

        win = SequencePlayerWindow(
            open_path,
            settings=self._settings,
            ocio_cfg=self._ocio_cfg,
            src_colorspace=src_colorspace,
            fps=fps,
            parent=self,
            media_kind=media_kind,
        )
        win.destroyed.connect(self._on_result_player_destroyed)
        self._result_player_win = win
        win.show()
        win.raise_()
        win.activateWindow()
        return f"opened in {status_label} ({src_colorspace or 'no OCIO src'})"

    def _on_result_player_destroyed(self, *_args: object) -> None:
        self._result_player_win = None

    def _cancel_run(self) -> None:
        batch = self._batch_tab()
        if batch is not None:
            if batch.is_busy():
                batch.cancel()
                noun = "Proxy" if batch is self._proxy_tab else "Transcode"
                self._append_log(f"{noun} cancellation requested\u2026")
            return
        if self._worker:
            self._worker.cancel()
            self._append_log("Cancellation requested\u2026")

    # -- Burn-in / watermark overlays --

    def _build_overlays(self, tab, inp, frame_range_str, frame_set):
        """Prepare the burn-in + watermark overlays for an EXR→video render.

        Returns ``(shot_overlay, slate_overlay, overlay_provider)``:

        - *shot_overlay* — combined burn-in + watermark for shot frames, or
          ``None`` when a per-frame token forces per-frame rendering instead.
        - *slate_overlay* — always ``None``: burn-in and watermark are stamped
          on shot frames only, never on the designed slate frame.
        - *overlay_provider* — ``fn(frame_num) -> uint8 RGBA`` used by the
          pipeline when any field contains a per-frame token (e.g. ``<frame>``);
          ``None`` for the fast, render-once path.

        Token text is expanded here so the renderers only ever see literal
        strings.  Everything captured by the provider closure is plain data so
        it is safe to call from the conversion worker thread.
        """
        import numpy as np

        from ..render import tokens as tok
        from ..render.burnin import render_burnin_overlay
        from ..render.watermark import render_watermark_overlay

        bw, bh = self._detect_slate_resolution("exr2video", inp)
        overlay_par = self._output_pixel_aspect(inp, tab)

        burnin_on = tab.burnin_enabled()
        raw_fields = (tab.get_effective_burnin_fields(inp) or {}) if burnin_on else {}

        wm_params = tab.get_watermark_params()
        slate_model = tab.slate_model()
        wm_on = bool(wm_params and slate_model is not None and slate_model.watermark_active())

        if not burnin_on and not wm_on:
            return None, None, None

        slate_render = slate_model.slate_data_for_render() if slate_model is not None else {}
        start_f = min(frame_set) if frame_set else None
        end_f = max(frame_set) if frame_set else None
        pad = max(4, len(str(end_f))) if end_f is not None else 4
        base_values = tok.build_values(
            slate_render,
            input_name=Path(inp).name,
            frame_pad=pad,
            start_frame=start_f,
            end_frame=end_f,
            resolution=f"{bw}x{bh}",
            frame_range=frame_range_str or None,
        )

        def _composite(burnin_ov, wm_ov):
            if burnin_ov is None or wm_ov is None:
                return burnin_ov if burnin_ov is not None else wm_ov
            a = wm_ov[..., 3:4].astype(np.float32) / 255.0
            rgb = wm_ov[..., :3].astype(np.float32) * a + burnin_ov[..., :3].astype(np.float32) * (
                1.0 - a
            )
            bg_a = burnin_ov[..., 3:4].astype(np.float32) / 255.0
            out = np.empty_like(burnin_ov)
            out[..., :3] = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
            out[..., 3:4] = np.clip((a + bg_a * (1.0 - a)) * 255.0, 0.0, 255.0).astype(np.uint8)
            return out

        def _render(frame_num):
            values = dict(base_values)
            # On static renders (no <frame> in use) collapse <frame> to empty so
            # it never leaks into the slate watermark as literal text.
            values["frame"] = f"{int(frame_num):0{pad}d}" if frame_num is not None else ""
            burnin_ov = None
            if burnin_on:
                fields = {k: tok.substitute(v, values) for k, v in raw_fields.items()}
                burnin_ov = render_burnin_overlay(bw, bh, fields, pixel_aspect=overlay_par)
            wm_ov = None
            if wm_on:
                p = dict(wm_params)
                p["text"] = tok.substitute(p.get("text", ""), values)
                wm_ov = render_watermark_overlay(bw, bh, p, pixel_aspect=overlay_par)
            return _composite(burnin_ov, wm_ov), wm_ov

        per_frame = tok.any_per_frame_token(raw_fields) or (
            wm_on and tok.has_per_frame_token(wm_params.get("text", ""))
        )

        # Render the base overlay once (with <frame> collapsed). Only shot
        # frames get an overlay; the slate is left clean.
        shot_overlay, _ = _render(None)

        if per_frame:
            self._append_log("Per-frame tokens detected — overlays render per frame (1 thread)")
            return None, None, lambda f: _render(f)[0]

        if shot_overlay is not None:
            self._append_log("Burn-in / watermark overlay rendered")
        return shot_overlay, None, None

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

        from ..core.ocio_utils import resolve_alias

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
    def _output_pixel_aspect(inp: str, tab) -> float:
        """Display pixel aspect for slate and burn-in (override or the clip)."""
        override = tab.pixel_aspect_override() if hasattr(tab, "pixel_aspect_override") else None
        try:
            detected = probe_media_aspect(inp) if inp else SQUARE
        except Exception:
            detected = SQUARE
        return apply_override(detected, override).factor

    @staticmethod
    def _detect_slate_resolution(mode: str, inp: str) -> tuple[int, int]:
        """Probe the input to determine the resolution for the slate frame."""
        try:
            if mode == "video2exr":
                from ..core.video import probe_video

                w, h, _fps, _total = probe_video(inp)
                return w, h
            else:
                from ..core.exr_io import read_image
                from ..core.sequence import find_exr_sequence

                paths, _bn = find_exr_sequence(inp)
                if paths:
                    first = read_image(paths[0])
                    return first.shape[1], first.shape[0]
        except Exception:
            pass
        return 1920, 1080

    # -- State snapshot/restore (for presets) --

    def snapshot_state(self) -> dict:
        """Capture convert **document** params only — no I/O paths or geometry.

        Written as a versioned preset via :func:`~src.services.presets.save_preset`.
        """
        from ..services.presets import SCHEMA_VERSION

        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "convert_preset",
            "tab": self._tabs.currentIndex(),
            "ocio_source": self._ocio_panel.current_source_key(),
            "ocio_file": self._ocio_panel._file_path,
            "v2e_src_space": self._v2e_tab.src_btn.current_space(),
            "v2e_dst_space": self._v2e_tab.dst_btn.current_space(),
            "v2e_compression": self._v2e_tab.get_compression(),
            "v2e_scale": self._v2e_tab.get_scale(),
            "v2e_pixel_aspect": str(self._v2e_tab._pixel_aspect_combo.currentData() or "auto"),
            "v2e_padding": self._v2e_tab.get_padding(),
            "v2e_start_frame": self._v2e_tab.get_start_frame(),
            "e2v_src_space": self._e2v_tab.src_btn.current_space(),
            "e2v_dst_space": self._e2v_tab.dst_btn.current_space(),
            "e2v_fps": self._e2v_tab.get_fps(),
            "e2v_scale": self._e2v_tab.get_scale(),
            "e2v_pixel_aspect": str(self._e2v_tab._pixel_aspect_combo.currentData() or "auto"),
            "e2v_codec": self._e2v_tab.get_video_codec_info()[0],
        }

    def restore_state(self, data: dict) -> None:
        """Restore convert params only — input/output paths are left untouched."""
        from ..services.presets import normalize_preset

        try:
            data = normalize_preset(dict(data or {}))
        except ValueError:
            data = dict(data or {})
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
            from ..core.constants import EXR_COMPRESSIONS

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
            par_key = f"{tab_prefix}_pixel_aspect"
            if par_key in data and getattr(tab_widget, "_pixel_aspect_combo", None) is not None:
                idx = tab_widget._pixel_aspect_combo.findData(str(data[par_key]))
                if idx >= 0:
                    tab_widget._pixel_aspect_combo.setCurrentIndex(idx)
        if "e2v_codec" in data and self._e2v_tab.codec_combo:
            _select_video_codec_combo_key(
                self._e2v_tab.codec_combo,
                str(data["e2v_codec"]),
            )

    # -- Drag and drop --

    @staticmethod
    def _video_exts() -> set[str]:
        """Every video suffix the app reads: the core list plus decoder plugins."""
        from ..core.source_plugins import plugin_suffixes

        return set(VIDEO_EXTS) | set(plugin_suffixes())

    @staticmethod
    def _drop_target(path: Path) -> DropTarget | None:
        from ..core.source_plugins import plugin_suffixes

        return drop_target(path.name, is_dir=path.is_dir(), extra_video_exts=plugin_suffixes())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    target = self._drop_target(Path(url.toLocalFile()))
                    if target is not None:
                        self._highlight_drop(target)
                        event.acceptProposedAction()
                        return
        event.ignore()

    def _highlight_drop(self, target: DropTarget | None) -> None:
        """Light the source card the drop would land on (``None`` clears both)."""
        on_batch = self._batch_tab() is not None
        self._v2e_tab.source_card.set_drop_active(target is DropTarget.VIDEO and not on_batch)
        self._e2v_tab.source_card.set_drop_active(target is DropTarget.SEQUENCE and not on_batch)

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._highlight_drop(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._highlight_drop(None)
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return
        batch = self._batch_tab()
        if batch is not None:
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if batch.add_paths(paths):
                event.acceptProposedAction()
            return
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            target = self._drop_target(p)
            if target is DropTarget.VIDEO and p.is_file():
                self._tabs.setCurrentIndex(0)
                self._v2e_tab.handle_dropped_path(str(p))
                self._append_log(f"Dropped video: {p.name}")
                event.acceptProposedAction()
                return
            if target is DropTarget.SEQUENCE and (p.is_dir() or p.is_file()):
                self._tabs.setCurrentIndex(1)
                self._e2v_tab.handle_dropped_path(str(p))
                self._append_log(f"Dropped: {p.name}")
                event.acceptProposedAction()
                return

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("ui/geometry", self.saveGeometry())
        self._settings.setValue("ui/log_open", self._log_toggle.isChecked())
        if self._log_toggle.isChecked():
            self._log_open_height = self._log_container.height()
        self._settings.setValue("ui/log_height", self._log_open_height)
        # Flush prefs (input paths, post-convert toggles, …) before exit so a
        # subsequent launch restores a validated source without re-browsing.
        self._settings.sync()
        # Close standalone sequence player first (releases GL / prefetch).
        win = self._result_player_win
        self._result_player_win = None
        if win is not None:
            try:
                win.close()
            except RuntimeError:
                pass
        # Stop background work so QApplication tear-down does not race threads.
        self._proxy_tab.shutdown()
        self._transcode_tab.shutdown()
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            if not self._thread.wait(8000):
                self._append_log("Convert thread did not stop in time; forcing exit.")
        super().closeEvent(event)
