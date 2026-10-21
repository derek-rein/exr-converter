"""Install-directory checks used so an MSIX package root stays read-only."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.core import app_paths
from src.core.app_paths import (
    is_msix_packaged,
    is_under_install_dir,
    leave_read_only_install_cwd,
    outside_install_dir,
    writable_app_subdir,
)
from src.core.trustmark import models as trustmark_models
from src.services import presets as presets_mod


def test_unpackaged_process_is_not_msix() -> None:
    # GitHub runners and local dev are not Store installs.
    assert is_msix_packaged() is False


def test_is_under_install_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install = tmp_path / "WindowsApps" / "EXRConverter"
    install.mkdir(parents=True)
    outside = tmp_path / "profile" / "presets"
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])

    assert is_under_install_dir(install)
    assert is_under_install_dir(install / "resources" / "ocio")
    assert not is_under_install_dir(outside)
    # A sibling whose name only shares a prefix must not match.
    sibling = tmp_path / "WindowsApps" / "EXRConverterExtra"
    assert not is_under_install_dir(sibling)


def test_outside_install_dir_uses_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install = tmp_path / "pkg"
    install.mkdir()
    home = tmp_path / "home"
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])
    chosen = outside_install_dir(install / "cache", home / "cache")
    assert chosen == home / "cache"


def test_outside_install_dir_raises_when_both_are_inside(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / "pkg"
    install.mkdir()
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])
    with pytest.raises(RuntimeError, match="read-only"):
        outside_install_dir(install / "a", install / "b")


def test_writable_app_subdir_rejects_empty_qt_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / "pkg"
    install.mkdir()
    fallback = tmp_path / "profile" / "VFXTools" / "EXRConverter"
    monkeypatch.chdir(install)
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])
    chosen = writable_app_subdir("", "presets", fallback=fallback)
    assert chosen == fallback / "presets"
    assert chosen.is_dir()
    assert not (install / "presets").exists()


def test_writable_app_subdir_keeps_user_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / "pkg"
    install.mkdir()
    profile = tmp_path / "Roaming" / "VFXTools" / "EXRConverter"
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])
    chosen = writable_app_subdir(str(profile), "presets", fallback=tmp_path / "unused")
    assert chosen == profile / "presets"
    assert chosen.is_dir()


def test_leave_read_only_install_cwd_only_when_packaged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / "pkg"
    home = tmp_path / "home"
    install.mkdir()
    home.mkdir()
    monkeypatch.chdir(install)
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])
    monkeypatch.setattr(app_paths.Path, "home", lambda: home)

    monkeypatch.setattr(app_paths, "is_msix_packaged", lambda: False)
    assert leave_read_only_install_cwd() is None
    assert Path.cwd() == install

    monkeypatch.setattr(app_paths, "is_msix_packaged", lambda: True)
    previous = leave_read_only_install_cwd()
    assert previous == install
    assert Path.cwd() == home


def test_preset_dir_does_not_fall_into_install_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qapp: object
) -> None:
    del qapp
    install = tmp_path / "pkg"
    install.mkdir()
    fallback = tmp_path / "VFXTools" / "EXRConverter"
    monkeypatch.chdir(install)
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])
    monkeypatch.setattr(presets_mod.QStandardPaths, "writableLocation", lambda *_a, **_k: "")
    monkeypatch.setattr(presets_mod, "profile_data_dir", lambda _org, _app: fallback)
    chosen = presets_mod._preset_dir()
    assert chosen == fallback / "presets"
    assert not (install / "presets").exists()


def test_trustmark_cache_leaves_install_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / "install"
    home = tmp_path / "home"
    install.mkdir()
    home.mkdir()
    monkeypatch.delenv("EXR_TRUSTMARK_CACHE", raising=False)
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])
    monkeypatch.setattr(trustmark_models, "_default_cache_dir", lambda: install / "trustmark")
    monkeypatch.setattr(trustmark_models.Path, "home", lambda: home)
    path = trustmark_models.cache_dir()
    if sys.platform == "win32":
        expected = home / "AppData" / "Local" / "VFXTools" / "EXRConverter" / "Cache" / "trustmark"
    elif sys.platform == "darwin":
        expected = home / "Library" / "Caches" / "VFXTools" / "EXRConverter" / "trustmark"
    else:
        expected = home / ".cache" / "exr-converter" / "trustmark"
    assert path == expected
    assert not app_paths.is_under_install_dir(path)


def test_trustmark_cache_override_rejects_install_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(app_paths, "runtime_exe_dirs", lambda: [install])
    monkeypatch.setenv("EXR_TRUSTMARK_CACHE", str(install / "models"))
    with pytest.raises(RuntimeError, match="read-only"):
        trustmark_models.cache_dir()
