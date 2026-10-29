"""Help → Documentation, the About links, and the bundled stylesheet."""

from __future__ import annotations

import sys

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtWidgets import QApplication, QMenu

import src.rc_resources  # noqa: F401 — register :/style.qss
from src.core.constants import DOCS_URL
from src.gui.window import MainWindow
from src.services.app_settings import make_ini_settings, set_app_settings


def test_docs_url():
    assert DOCS_URL == "https://vfx.systems/docs/exr-converter"


def test_help_menu_has_documentation(qapp: QApplication, tmp_path) -> None:
    set_app_settings(make_ini_settings(str(tmp_path / "ui.ini")))
    win = MainWindow()
    try:
        menus = {m.title().replace("&", ""): m for m in win.menuBar().findChildren(QMenu)}
        help_texts = [a.text().replace("&", "") for a in menus["Help"].actions()]
        assert help_texts[0] == "Documentation"
        file_texts = [a.text().replace("&", "") for a in menus["File"].actions()]
        assert ("Quick Look…" in file_texts) == (sys.platform == "darwin")
    finally:
        win.close()
        set_app_settings(None)


def test_about_links_vfx_systems():
    import inspect

    from src.gui import window

    src = inspect.getsource(window)
    assert 'href="https://vfx.systems"' in src
    assert "ocio.cc" not in src


def test_stylesheet_resource_is_readable(qapp: QApplication) -> None:
    # rcc's zstd output reads back empty on some PySide6 builds; keep zlib.
    f = QFile(":/style.qss")
    assert f.open(QIODevice.OpenModeFlag.ReadOnly)
    try:
        assert len(bytes(f.readAll().data())) > 1000
    finally:
        f.close()
