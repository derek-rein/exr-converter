"""EXR <-> video converter — entry point."""

from __future__ import annotations

import sys

from src.cli import build_parser, run_cli
from src.constants import APP_NAME, APP_ORG


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command:
        return run_cli(args)

    if args.headless:
        parser.error("Use: main.py video2exr ... or main.py exr2video ...")

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    import src.rc_resources  # noqa: F401 — register Qt resources
    from src.style import load_stylesheet
    from src.window import MainWindow

    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
    app.setWindowIcon(QIcon(":/icon.png"))

    win = MainWindow()
    win.show()

    if args.smoke_test:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3000, app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
