"""Optional ARRI plugin build script: quiet without the token, safe overlay."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "arri_plugin.py"


def _load():
    spec = importlib.util.spec_from_file_location("arri_plugin", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["arri_plugin"] = module
    spec.loader.exec_module(module)
    return module


def test_prepare_skips_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    monkeypatch.delenv(module.TOKEN_ENV, raising=False)
    monkeypatch.setattr(module, "REQUIRED", tmp_path / "required")
    module.prepare()
    assert "skipping" in capsys.readouterr().out
    assert not (tmp_path / "required").exists()


def test_failed_step_hides_tool_output(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    monkeypatch.delenv("ARRI_PLUGIN_VERBOSE", raising=False)
    cmd = [sys.executable, "-c", "import sys; print('SECRET HEADER LINE'); sys.exit(3)"]
    with pytest.raises(SystemExit) as exc:
        module._run("bridge build", cmd)
    msg = str(exc.value)
    assert "bridge build failed (3)" in msg
    assert "SECRET HEADER LINE" not in msg


def test_verbose_output_redacts_token(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load()
    monkeypatch.setenv("ARRI_PLUGIN_VERBOSE", "1")
    monkeypatch.setenv(module.TOKEN_ENV, "tok123")
    cmd = [sys.executable, "-c", "import sys; print('url tok123'); sys.exit(1)"]
    with pytest.raises(SystemExit) as exc:
        module._run("clone", cmd)
    assert "url ***" in str(exc.value)
    assert "tok123" not in str(exc.value)


@pytest.mark.parametrize(
    ("system", "arch", "expected"),
    [
        ("Windows", "", "windows-x64-msvc192"),
        ("Darwin", "arm64", "macos-arm64-clang1200"),
        ("Darwin", "x86_64", "macos-x64-clang1200"),
        ("Linux", "", "linux-x64-rhel8-gcc85"),
    ],
)
def test_sdk_platform(
    monkeypatch: pytest.MonkeyPatch, system: str, arch: str, expected: str
) -> None:
    module = _load()
    monkeypatch.delenv("ARRI_SDK_PLATFORM", raising=False)
    monkeypatch.setenv("ARRI_BRIDGE_ARCH", arch)
    monkeypatch.setattr(module.platform, "system", lambda: system)
    assert module.sdk_platform() == expected


def test_lfs_pulls_only_runtime_platform_and_mxf_source() -> None:
    module = _load()
    patterns = module.lfs_include_patterns("macos-arm64-clang1200")
    assert patterns == [
        "vendor/arri/image-sdk/9.2.0/macos-arm64-clang1200/**",
        "vendor/arri/mxf-library/**",
    ]
    assert not any("test-media" in p or "docs" in p for p in patterns)


def test_overlay_keeps_other_plugins_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    feed = tmp_path / "feed"
    (feed / "plugin" / "arri").mkdir(parents=True)
    (feed / "plugin" / "arri" / "__init__.py").write_text(
        "def get_source_plugin():\n    return None\n"
    )
    plugins = tmp_path / "app" / "src" / "plugins"
    (plugins / "camera_raw").mkdir(parents=True)
    (plugins / "__init__.py").write_text("")
    (plugins / "camera_raw" / "__init__.py").write_text("")
    module.overlay(feed, plugins)
    text = (plugins / "_enabled.py").read_text(encoding="utf-8")
    assert '"src.plugins.arri"' in text
    assert '"src.plugins.camera_raw"' in text
    ns: dict = {}
    exec(text.replace("from . import", "# from . import"), ns)
    assert ns["ENABLED"] == ("src.plugins.arri", "src.plugins.camera_raw")
    monkeypatch.setattr(module, "ENABLED", plugins / "_enabled.py")
    monkeypatch.setattr(module, "PLUGIN_DEST", plugins / "arri")
    assert module.nuitka_flag_lines() == [
        "--include-module=src.plugins._enabled",
        "--include-package=src.plugins.arri",
    ]


def test_install_skips_when_unprepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    monkeypatch.setattr(module, "FEED", tmp_path / "missing-feed")
    monkeypatch.setattr(module, "BUILD", tmp_path / "missing-build")
    monkeypatch.setattr(module, "REQUIRED", tmp_path / "not-required")
    module.install(tmp_path / "bundle")
    assert "not included" in capsys.readouterr().out


def test_install_fails_when_required_but_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    required = tmp_path / "required"
    required.write_text("required\n")
    monkeypatch.setattr(module, "FEED", tmp_path / "missing-feed")
    monkeypatch.setattr(module, "BUILD", tmp_path / "missing-build")
    monkeypatch.setattr(module, "REQUIRED", required)
    with pytest.raises(SystemExit, match="did not build"):
        module.install(tmp_path / "bundle")
