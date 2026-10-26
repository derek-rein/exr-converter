"""EXR <-> video converter — entry point."""

from __future__ import annotations

import sys

from src.cli import build_parser, run_cli
from src.core.app_paths import leave_read_only_install_cwd
from src.core.constants import APP_NAME, APP_ORG, APP_VERSION
from src.core.mp_support import configure_multiprocessing


def main() -> int:
    # Before anything can start a worker pool (CLI and GUI convert both do).
    configure_multiprocessing()

    # Store launch uses the MSIX package root as the working directory. That
    # folder is read-only. Move to the user profile before any relative write.
    leave_read_only_install_cwd()

    parser = build_parser()
    args = parser.parse_args()

    if args.command:
        return run_cli(args)

    if args.headless:
        parser.error("Use: main.py video2exr ... or main.py exr2video ...")

    from PySide6.QtWidgets import QApplication

    import src.rc_resources  # noqa: F401 — register Qt resources
    from src.gui.ocio_gpu_plane import configure_default_gl_format
    from src.gui.style import load_stylesheet
    from src.gui.window import MainWindow

    # Core Profile 4.1 for OCIO GLSL GPU display in the slate viewer.
    # Must be set before the QApplication (and any GL context) is created.
    configure_default_gl_format()

    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
    # macOS: do not setWindowIcon — Dock must use the .app bundle's .icns
    # (CFBundleIconFile). A runtime PNG becomes NSApplication.applicationIconImage
    # and draws a sharp full-bleed square while the app is running.
    if sys.platform != "darwin":
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(":/icon.png"))

    win = MainWindow()
    # Nuke / shell launch: pre-fill media + OCIO before the event loop runs.
    open_path = getattr(args, "open", None)
    gui_ocio = getattr(args, "gui_ocio", None)
    mode = getattr(args, "mode", None)
    if open_path or gui_ocio or (mode and mode != "auto"):
        win.apply_startup(open_path=open_path, ocio_path=gui_ocio, mode=mode)
    win.show()

    if args.smoke_test:
        # Fail the build if the frozen app linked OCIO 2.4 (Nuitka/oiio trap).
        import PyOpenColorIO as _OCIO
        from PySide6.QtCore import QTimer

        from src.core.ocio_utils import get_bundled_aces_studio_path

        # Keep Python ssl usable in the frozen binary (OpenSSL dylibs not stripped).
        try:
            import ssl as _ssl

            _ssl.create_default_context()
            print(f"smoke: ssl OK ({_ssl.OPENSSL_VERSION})")
        except Exception as e:
            print(f"SMOKE FAIL: Python ssl unavailable: {e}", file=sys.stderr)
            return 4

        # Sequence browser fuzzy search (rapidfuzz C extension) must load frozen.
        try:
            from rapidfuzz import fuzz as _fuzz
            from rapidfuzz import process as _process

            _match = _process.extractOne("shto_010_comp", ["shot_010_comp"], scorer=_fuzz.WRatio)
            if not _match or _match[1] < 60:
                raise RuntimeError(f"unexpected score {_match!r}")
            print(f"smoke: rapidfuzz OK (score {_match[1]:.0f})")
        except Exception as e:
            print(f"SMOKE FAIL: rapidfuzz unavailable: {e}", file=sys.stderr)
            return 5

        ver = _OCIO.GetVersion()
        nums = tuple(int(x) for x in ver.split(".")[:3] if x.isdigit())
        if nums < (2, 5, 0):
            print(f"SMOKE FAIL: OpenColorIO {ver} (need >= 2.5.0)", file=sys.stderr)
            return 2
        cfg_path = get_bundled_aces_studio_path()
        if cfg_path is not None and cfg_path.is_file():
            try:
                _OCIO.Config.CreateFromFile(str(cfg_path))
            except Exception as e:
                print(f"SMOKE FAIL: cannot load bundled OCIO config: {e}", file=sys.stderr)
                return 3
            print(f"smoke: OpenColorIO {ver} + bundled config OK")
        else:
            print(f"smoke: OpenColorIO {ver} OK (no bundled config path)")

        QTimer.singleShot(3000, app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
