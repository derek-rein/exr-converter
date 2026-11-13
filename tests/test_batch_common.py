"""Shared batch-tab plumbing: the Add Clips… / Add Folder… pickers."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from src.gui import batch_common
from src.gui.proxy_tab import ProxyTab
from src.gui.transcode_tab import TranscodeTab


@pytest.mark.parametrize(
    ("factory", "prefix"), [(ProxyTab, "video2proxy"), (TranscodeTab, "transcode")]
)
def test_pick_files_adds_clips_and_remembers_folder(
    qapp: QApplication, settings, tmp_path, monkeypatch, factory, prefix
) -> None:
    clip = tmp_path / "a.mov"
    clip.write_bytes(b"x")
    settings.setValue(f"{prefix}/last_dir", "/start")
    seen: list[tuple[str, str]] = []

    def fake_open(_parent, _title, start, filt):
        seen.append((start, filt))
        return [str(clip)], ""

    monkeypatch.setattr(batch_common.QFileDialog, "getOpenFileNames", fake_open)
    tab = factory(settings)
    try:
        tab._pick_files()
        assert tab.clips() == [str(clip)]
        assert seen[0][0] == "/start"
        assert seen[0][1].startswith("Video (") and "*.mov" in seen[0][1]
        assert settings.value(f"{prefix}/last_dir") == str(tmp_path)
    finally:
        tab.shutdown()
        tab.deleteLater()


@pytest.mark.parametrize(
    ("factory", "prefix"), [(ProxyTab, "video2proxy"), (TranscodeTab, "transcode")]
)
def test_pick_empty_folder_logs(
    qapp: QApplication, settings, tmp_path, monkeypatch, factory, prefix
) -> None:
    monkeypatch.setattr(batch_common.QFileDialog, "getExistingDirectory", lambda *_: str(tmp_path))
    tab = factory(settings)
    logs: list[str] = []
    tab.log_message.connect(logs.append)
    try:
        tab._pick_folder()
        assert logs == [f"No video clips found in {tmp_path}"]
        assert settings.value(f"{prefix}/last_dir") == str(tmp_path)
    finally:
        tab.shutdown()
        tab.deleteLater()
