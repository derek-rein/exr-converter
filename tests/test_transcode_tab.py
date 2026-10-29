"""Transcode tab: dependent controls, readiness, settings, window wiring, queue."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import src.rc_resources  # noqa: F401 — register :/style.qss
from src.core.ocio_utils import color_space_families, resolve_ocio_for_cli
from src.gui.transcode_tab import LOCATION_CUSTOM, TranscodeTab
from src.gui.window import TRANSCODE_TAB_INDEX, MainWindow
from src.services.app_settings import make_ini_settings, set_app_settings
from tests.support.integration import write_synthetic_video


def _wait_until(pred, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        QTest.qWait(25)
    return pred()


def _load_ocio(tab: TranscodeTab, config_path: str = "") -> object:
    cfg = resolve_ocio_for_cli(None)
    tab.populate_spaces(color_space_families(cfg), ocio_cfg=cfg, config_path=config_path)
    return cfg


def _detected(tab: TranscodeTab) -> bool:
    return _wait_until(lambda: not tab.clip_tree.detection_pending())


@pytest.fixture
def tab(qapp: QApplication, settings) -> Iterator[TranscodeTab]:
    t = TranscodeTab(settings)
    try:
        yield t
    finally:
        t.shutdown()
        t.deleteLater()


def _select(combo, data) -> None:
    idx = combo.findData(data)
    assert idx >= 0, data
    combo.setCurrentIndex(idx)


def test_defaults_are_prores_hq_passthrough(tab: TranscodeTab, qapp: QApplication) -> None:
    tab.show()
    qapp.processEvents()
    assert tab.codec_key() == "prores"
    assert tab.container() == "mov"
    assert tab.audio_mode() == "copy"
    assert tab.resolution_key() == "original"
    assert tab.fps() == 0.0
    # Output space and watermark sit in a plain group, not a collapsible.
    assert tab.dst_btn.isVisible() and tab.watermark_cb.isVisible()
    # ProRes is intra-frame: no CRF / bitrate / preset controls.
    assert not tab.rate_combo.isVisible() and tab._intra_note.isVisible()
    assert not tab.width_spin.isVisible()
    assert not tab.output_dir.isVisible()


def test_codec_drives_container_quality_and_audio(tab: TranscodeTab, qapp) -> None:
    tab.show()
    _select(tab.codec_combo, "h264")
    qapp.processEvents()
    assert [tab.container_combo.itemData(i) for i in range(tab.container_combo.count())] == [
        "mp4",
        "mov",
    ]
    assert tab.rate_combo.isVisible() and tab.crf_spin.isVisible()
    assert not tab.bitrate_spin.isVisible()
    audio = [tab.audio_combo.itemData(i) for i in range(tab.audio_combo.count())]
    assert "pcm" not in audio
    _select(tab.rate_combo, "bitrate")
    qapp.processEvents()
    _load_ocio(tab)
    assert tab.bitrate_spin.isVisible() and not tab.crf_spin.isVisible()
    s = tab.current_settings()
    assert (s.codec_key, s.container, s.rate_control) == ("h264", "mp4", "bitrate")

    _select(tab.codec_combo, "dnxhr_hqx")
    _select(tab.container_combo, "mxf")
    audio = [tab.audio_combo.itemData(i) for i in range(tab.audio_combo.count())]
    assert audio == ["copy", "pcm", "none"]


def test_custom_size_and_fps_rows(tab: TranscodeTab, qapp) -> None:
    tab.show()
    _select(tab.resolution_combo, "custom")
    qapp.processEvents()
    assert tab.width_spin.isVisible()
    _select(tab.fps_combo, -1.0)
    qapp.processEvents()
    assert tab.fps_spin.isVisible()
    tab.fps_spin.setValue(48.0)
    assert tab.fps() == 48.0


def test_readiness(tab: TranscodeTab, tmp_path) -> None:
    assert not tab.is_ready()
    (tmp_path / "a.mov").write_bytes(b"x")
    assert tab.add_paths([str(tmp_path)]) == 1
    assert _detected(tab)
    assert not tab.is_ready()  # no OCIO spaces loaded yet
    _load_ocio(tab)
    assert tab.is_ready()
    _select(tab.location_combo, LOCATION_CUSTOM)
    assert not tab.is_ready()
    tab.output_dir.setText(str(tmp_path / "out"))
    assert tab.is_ready()
    _select(tab.resolution_combo, "custom")
    tab.width_spin.setValue(0)
    tab.height_spin.setValue(0)
    assert not tab.is_ready()
    tab.width_spin.setValue(1280)
    assert tab.is_ready()
    clip = str(tmp_path / "a.mov")
    tab.clip_tree.set_override(clip, "Not A Real Space")
    assert not tab.is_ready()
    tab.clip_tree.set_override(clip, "")
    assert tab.is_ready()


def test_input_space_folder_and_clip_overrides(tab: TranscodeTab, tmp_path) -> None:
    cfg = _load_ocio(tab, "/x.ocio")
    shots = tmp_path / "shots"
    shots.mkdir()
    for name in ("a.mov", "b.mov"):
        (shots / name).write_bytes(b"x")
    (tmp_path / "loose.mov").write_bytes(b"x")
    assert tab.add_paths([str(shots), str(tmp_path / "loose.mov")]) == 3
    assert _detected(tab)
    a, b, loose = (str(shots / "a.mov"), str(shots / "b.mov"), str(tmp_path / "loose.mov"))
    assert tab.clips() == [a, b, loose]
    folder = tab.clip_tree.folder_item(str(shots))
    assert folder is not None and folder.childCount() == 2
    assert tab.clip_tree.file_item(loose).parent() is None

    # Unreadable files fall back to the default detected space.
    detected = tab.input_space(a)
    assert detected and detected == tab.input_space(loose)
    assert folder.text(1) == detected

    from src.core.ocio_utils import find_equivalent_space

    logc = find_equivalent_space(cfg, "ARRI LogC3 (EI800)")
    slog = find_equivalent_space(cfg, "S-Log3 S-Gamut3")
    assert logc and slog
    tab.clip_tree.set_override(str(shots), logc)
    assert tab.input_space(a) == logc and tab.input_space(b) == logc
    assert tab.input_space(loose) == detected

    # A clip can override its folder; setting the folder again resets it.
    tab.clip_tree.set_override(b, slog)
    assert tab.input_space(a) == logc and tab.input_space(b) == slog
    tab.clip_tree.set_override(str(shots), detected)
    assert tab.input_space(b) == detected

    base = tab.current_settings()
    assert base.config_path == "/x.ocio" and base.color_dst == tab.dst_btn.current_space()
    tab.clip_tree.set_override(str(shots), logc)
    s = tab.settings_for(a, base)
    assert s.color_src == logc and s.color_enabled


def test_space_menu_applies_to_selection(tab: TranscodeTab, tmp_path) -> None:
    cfg = _load_ocio(tab)
    for name in ("a.mov", "b.mov"):
        (tmp_path / name).write_bytes(b"x")
    tab.add_paths([str(tmp_path / "a.mov"), str(tmp_path / "b.mov")])
    assert _detected(tab)
    items = [tab.clip_tree.file_item(str(tmp_path / n)) for n in ("a.mov", "b.mov")]
    for it in items:
        it.setSelected(True)
    menu = tab.clip_tree.build_space_menu(items[0])
    names = {n for v in color_space_families(cfg).values() for n in v}
    first_space = next(a for a in menu.findChildren(QAction) if a.text() in names)
    first_space.trigger()
    assert {tab.input_space(str(tmp_path / n)) for n in ("a.mov", "b.mov")} == {first_space.text()}
    reset = menu.actions()[0]
    assert reset.text().startswith("Use detected")
    reset.trigger()
    assert not tab.clip_tree.override(str(tmp_path / "a.mov"))


def test_remove_clip_and_empty_folder(tab: TranscodeTab, tmp_path) -> None:
    (tmp_path / "a.mov").write_bytes(b"x")
    tab.add_paths([str(tmp_path)])
    tab.clip_tree.file_item(str(tmp_path / "a.mov")).setSelected(True)
    tab._remove_selected()
    assert tab.clips() == []
    assert tab.clip_tree.topLevelItemCount() == 0


def test_settings_are_remembered(qapp, settings) -> None:
    a = TranscodeTab(settings)
    _select(a.codec_combo, "hevc")
    _select(a.container_combo, "mov")
    _select(a.overwrite_combo, "rename")
    a.name_edit.setText("<name>_v2")
    a.watermark_cb.setChecked(True)
    a.shutdown()
    b = TranscodeTab(settings)
    try:
        assert b.codec_key() == "hevc"
        assert b.container() == "mov"
        assert b.overwrite_mode() == "rename"
        assert b.name_edit.text() == "<name>_v2"
        assert b.watermark_cb.isChecked()
    finally:
        b.shutdown()


def test_window_has_transcode_tab(qapp: QApplication, tmp_path) -> None:
    set_app_settings(make_ini_settings(str(tmp_path / "ui.ini")))
    win = MainWindow()
    try:
        assert win._tabs.count() == 4
        assert win._tabs.widget(TRANSCODE_TAB_INDEX) is win._transcode_tab
        assert win._tabs.tabText(TRANSCODE_TAB_INDEX) == "Transcode"
        win._tabs.setCurrentIndex(TRANSCODE_TAB_INDEX)
        assert not win._go.isEnabled()
        (tmp_path / "a.mov").write_bytes(b"x")
        win._transcode_tab.add_paths([str(tmp_path / "a.mov")])
        assert _wait_until(win._go.isEnabled)  # once the clip's space is detected
        assert win._after_row_widget.isHidden()
        assert win._transcode_tab.dst_btn.displayed_space()  # OCIO menus populated
        win.apply_startup(mode="transcode")
        assert win._tabs.currentIndex() == TRANSCODE_TAB_INDEX
    finally:
        win.close()
        set_app_settings(None)


@pytest.mark.integration
def test_batch_transcodes_and_marks_items(tab: TranscodeTab, tmp_path) -> None:
    write_synthetic_video(tmp_path / "a.mp4", frames=3)
    write_synthetic_video(tmp_path / "b.mp4", frames=3)
    _load_ocio(tab)
    tab.add_paths([str(tmp_path)])
    assert _detected(tab)
    _select(tab.codec_combo, "h264")
    assert tab.start_batch() == 2
    assert _wait_until(lambda: not tab.is_busy() and tab.queue.completed == 2)
    assert (tmp_path / "a_H264.mp4").is_file()
    assert (tmp_path / "b_H264.mp4").is_file()
    texts = [tab.clip_tree.status_text(c) for c in tab.clips()]
    assert all(t.startswith("✓ ") for t in texts), texts
    # Skip by default: a second run queues nothing.
    assert tab.start_batch() == 0


@pytest.mark.integration
def test_failed_clip_is_marked(tab: TranscodeTab, tmp_path) -> None:
    bad = tmp_path / "broken.mov"
    bad.write_bytes(b"not a video")
    _load_ocio(tab)
    tab.add_paths([str(bad)])
    assert _detected(tab)
    assert tab.start_batch() == 1
    assert _wait_until(lambda: not tab.is_busy())
    assert tab.queue.failed == 1
    assert tab.clip_tree.status_text(str(bad)).startswith("✗ ")
