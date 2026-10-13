"""Video → Proxy tab: clip list, readiness, window wiring, queued conversion."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import src.rc_resources  # noqa: F401 — register :/style.qss
from src.core.proxy import PROXY_SUBFOLDER
from src.gui.convert_tab import ConvertTab
from src.gui.proxy_tab import LOCATION_BESIDE, LOCATION_CUSTOM, ProxyTab
from src.gui.style import load_stylesheet
from src.gui.window import PROXY_TAB_INDEX, MainWindow
from src.services.app_settings import make_ini_settings, set_app_settings
from tests.support.integration import write_synthetic_video


def _wait_until(pred, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        QTest.qWait(25)
    return pred()


@pytest.fixture
def tab(qapp: QApplication, settings) -> Iterator[ProxyTab]:
    t = ProxyTab(settings)
    try:
        yield t
    finally:
        t.shutdown()
        t.deleteLater()


def test_add_paths_and_readiness(tab: ProxyTab, tmp_path) -> None:
    assert not tab.is_ready()
    (tmp_path / "a.mov").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    assert tab.add_paths([str(tmp_path)]) == 2
    assert tab.add_paths([str(tmp_path / "a.mov")]) == 0  # no duplicates
    assert tab.is_ready()

    idx = tab.location_combo.findData(LOCATION_CUSTOM)
    tab.location_combo.setCurrentIndex(idx)
    tab.output_dir.setText("")
    assert not tab.is_ready()
    tab.output_dir.setText(str(tmp_path / "out"))
    assert tab.is_ready()


def test_watch_refuses_missing_folder(tab: ProxyTab, tmp_path) -> None:
    tab.watch_dir.setText(str(tmp_path / "missing"))
    tab.watch_btn.setChecked(True)
    assert not tab.is_watching()
    assert not tab.watch_btn.isChecked()
    # A failed start still opens the section so the path can be corrected.
    assert tab._watch_section.is_expanded()


def test_custom_folder_row_follows_location(tab: ProxyTab, qapp: QApplication) -> None:
    tab.show()
    qapp.processEvents()
    assert tab.output_dir.isHidden()
    tab.location_combo.setCurrentIndex(tab.location_combo.findData(LOCATION_CUSTOM))
    qapp.processEvents()
    assert not tab.output_dir.isHidden()
    tab.location_combo.setCurrentIndex(tab.location_combo.findData(LOCATION_BESIDE))
    qapp.processEvents()
    assert tab.output_dir.isHidden()


def test_watch_section_starts_collapsed(tab: ProxyTab, qapp: QApplication) -> None:
    tab.show()
    qapp.processEvents()
    assert not tab._watch_section.is_expanded()
    assert not tab.watch_dir.isVisible()
    tab._watch_section.set_expanded(True)
    qapp.processEvents()
    assert tab.watch_dir.isVisible()
    assert tab.existing_cb.isVisible()


def test_proxy_form_matches_convert_tab_height(qapp: QApplication, settings) -> None:
    """Default proxy content stays within a row of the other convert tabs."""
    sheet = load_stylesheet()
    made: list[QWidget] = []
    heights: list[int] = []
    try:
        for widget in (
            ConvertTab("video2exr", settings),
            ConvertTab("exr2video", settings),
            ProxyTab(settings),
        ):
            made.append(widget)
            widget.setStyleSheet(sheet)
            widget.show()
            qapp.processEvents()
            body = widget.findChild(QWidget, "convertTabBody")
            assert body is not None
            heights.append(body.sizeHint().height())
        video2exr, exr2video, proxy = heights
        assert video2exr == exr2video
        assert proxy <= video2exr + 24
    finally:
        for widget in made:
            if isinstance(widget, ProxyTab):
                widget.shutdown()
            widget.close()
            widget.deleteLater()


def test_window_has_proxy_tab(qapp: QApplication, tmp_path) -> None:
    set_app_settings(make_ini_settings(str(tmp_path / "ui.ini")))
    win = MainWindow()
    try:
        assert win._tabs.count() == 3
        assert win._tabs.widget(PROXY_TAB_INDEX) is win._proxy_tab
        win._tabs.setCurrentIndex(PROXY_TAB_INDEX)
        assert not win._go.isEnabled()
        (tmp_path / "a.mov").write_bytes(b"x")
        win._proxy_tab.add_paths([str(tmp_path / "a.mov")])
        assert win._go.isEnabled()
        assert win._after_row_widget.isHidden()
        win._tabs.setCurrentIndex(0)
        assert not win._after_row_widget.isHidden()
    finally:
        win.close()
        set_app_settings(None)


@pytest.mark.integration
def test_batch_and_watch_folder_make_proxies(tab: ProxyTab, tmp_path) -> None:
    write_synthetic_video(tmp_path / "a.mp4", frames=3)
    tab.add_paths([str(tmp_path / "a.mp4")])
    assert tab.start_batch() == 1
    assert _wait_until(lambda: not tab.is_busy() and tab.queue.completed == 1)
    assert (tmp_path / PROXY_SUBFOLDER / "a.mov").is_file()

    watch = tmp_path / "ingest"
    watch.mkdir()
    tab.watch_dir.setText(str(watch))
    tab.watch_btn.setChecked(True)
    assert tab.is_watching()
    write_synthetic_video(watch / "new.mp4", frames=3)
    assert _wait_until(lambda: (watch / PROXY_SUBFOLDER / "new.mov").is_file(), timeout=60)
    tab.watch_btn.setChecked(False)
    assert not tab.is_watching()
