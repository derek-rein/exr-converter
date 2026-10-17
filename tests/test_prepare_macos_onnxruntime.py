"""macOS ONNX Runtime install-id fix used before Nuitka."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_macos_onnxruntime import (  # noqa: E402
    aligned_install_id,
    real_onnxruntime_dylibs,
    soname_symlinks,
)


def test_install_id_tracks_the_real_filename() -> None:
    # onnxruntime 1.31 arm64: id is the soname, file is the full version.
    updated = aligned_install_id(
        "libonnxruntime.1.31.0.dylib",
        "@rpath/libonnxruntime.1.dylib",
    )
    assert updated == "@rpath/libonnxruntime.1.31.0.dylib"
    # 1.23.x already matches, which is what Intel Macs install.
    assert (
        aligned_install_id(
            "libonnxruntime.1.23.2.dylib",
            "@rpath/libonnxruntime.1.23.2.dylib",
        )
        is None
    )
    assert (
        aligned_install_id(
            "/tmp/libonnxruntime.1.31.0.dylib",
            "@loader_path/libonnxruntime.1.dylib",
        )
        == "@loader_path/libonnxruntime.1.31.0.dylib"
    )


def test_symlinks_are_not_treated_as_the_library(tmp_path: Path) -> None:
    real = tmp_path / "libonnxruntime.1.31.0.dylib"
    real.write_bytes(b"mach-o")
    link = tmp_path / "libonnxruntime.1.dylib"
    link.symlink_to(real.name)
    assert real_onnxruntime_dylibs(tmp_path) == [real]
    assert soname_symlinks(tmp_path) == [link]


def test_prepare_script_is_wired_before_nuitka() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "scripts/prepare_macos_onnxruntime.py" in release
    assert "scripts/prepare_macos_onnxruntime.py" in makefile
    nuitka_at = release.index("python -m nuitka")
    prepare_at = release.index("scripts/prepare_macos_onnxruntime.py")
    assert prepare_at < nuitka_at
    assert "platform_machine != 'x86_64'" in pyproject
    assert "onnxruntime>=1.20,<1.24" in pyproject
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "onnxruntime"\nversion = "1.23.2"' in lock
    assert 'name = "onnxruntime"\nversion = "1.31.0"' in lock
    assert "platform_machine == 'x86_64' and sys_platform == 'darwin'" in lock


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS early exit")
def test_script_exits_quietly_off_macos() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "prepare_macos_onnxruntime.py")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "not macOS" in proc.stdout
