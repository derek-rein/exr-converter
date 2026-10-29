"""Store screenshot workflow stays on the native Windows Qt platform."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from store_screenshots import store_aspect_ok  # noqa: E402


def test_store_aspect_accepts_1080p_and_rejects_small_frames() -> None:
    assert store_aspect_ok(1920, 1080)
    assert store_aspect_ok(1366, 768)
    assert not store_aspect_ok(1024, 768)
    assert not store_aspect_ok(1920, 1200)
    assert not store_aspect_ok(1280, 720)


def test_store_screenshot_workflow_is_windows_desktop() -> None:
    workflow = (ROOT / ".github" / "workflows" / "store-screenshots.yml").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts" / "store_screenshots.py").read_text(encoding="utf-8")
    assert "on:\n  workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "schedule:" not in workflow
    assert "runs-on: windows-latest" in workflow
    assert "QT_QPA_PLATFORM: windows" in workflow
    assert "offscreen" not in workflow
    assert "scripts/store_screenshots.py" in workflow
    assert "store-screenshots/*.png" in workflow
    assert 'os.environ["QT_QPA_PLATFORM"] = "windows"' in script
    assert "offscreen" in script
    assert "Segoe UI" in script
    assert "windows11" in script
    assert "PrintWindow" in script
    assert "1920" in script and "1080" in script
