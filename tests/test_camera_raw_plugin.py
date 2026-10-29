"""Optional camera RAW plugin fetch stays quiet when the token is absent."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "camera_raw_plugin.py"


def _load():
    spec = importlib.util.spec_from_file_location("camera_raw_plugin", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["camera_raw_plugin"] = module
    spec.loader.exec_module(module)
    return module


def test_redact_splits_the_private_name() -> None:
    module = _load()
    hidden = "can" + "on"
    assert hidden not in module.redact(f"prefix-{hidden}-sdk")
    assert module.redact(hidden) == "vendor"


def test_prepare_skips_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    monkeypatch.delenv(module.TOKEN_ENV, raising=False)
    monkeypatch.setattr(module, "REQUIRED", tmp_path / "required")
    module.prepare()
    assert "skipping" in capsys.readouterr().out
    assert not (tmp_path / "required").exists()


def test_overlay_and_nuitka_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    feed = tmp_path / "feed"
    package = feed / "plugin" / "privatepkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "def get_source_plugin():\n    return None\n", encoding="utf-8"
    )
    (package / "plugin.py").write_text("VALUE = 1\n", encoding="utf-8")
    dest_root = tmp_path / "app" / "src" / "plugins"
    dest_root.mkdir(parents=True)
    monkeypatch.setattr(module, "FEED", feed)
    monkeypatch.setattr(module, "PLUGIN_DEST", dest_root / "camera_raw")
    monkeypatch.setattr(module, "ENABLED", dest_root / "_enabled.py")
    module._overlay()
    assert (dest_root / "camera_raw" / "plugin.py").is_file()
    text = (dest_root / "_enabled.py").read_text(encoding="utf-8")
    assert "src.plugins.camera_raw" in text
    assert "privatepkg" not in text
    module.nuitka_flags()
    flags = capsys.readouterr().out
    assert "--include-package=src.plugins.camera_raw" in flags
    assert "--include-module=src.plugins._enabled" in flags


def test_install_skips_when_unprepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    monkeypatch.setattr(module, "FEED", tmp_path / "missing-feed")
    monkeypatch.setattr(module, "BUILD", tmp_path / "missing-build")
    monkeypatch.setattr(module, "REQUIRED", tmp_path / "not-required")
    module.install(tmp_path / "bundle")
    assert "not included" in capsys.readouterr().out
