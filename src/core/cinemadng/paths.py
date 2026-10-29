"""Locate libcinemadng, the small CinemaDNG frame reader."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from ..app_paths import package_root_from_file, runtime_exe_dirs


def library_names() -> tuple[str, ...]:
    system = platform.system()
    if system == "Darwin":
        return ("libcinemadng.dylib",)
    if system == "Windows":
        return ("cinemadng.dll", "libcinemadng.dll")
    return ("libcinemadng.so",)


def library_candidates() -> list[Path]:
    """Search paths for the CinemaDNG reader shared library."""
    names = library_names()
    dirs: list[Path] = []
    files: list[Path] = []

    env = os.environ.get("EXR_CONVERTER_CINEMADNG_LIB", "").strip()
    if env:
        path = Path(env).expanduser()
        if path.suffix.lower() in {".dylib", ".so", ".dll"}:
            files.append(path)
        else:
            dirs.append(path)

    for exe_dir in runtime_exe_dirs():
        dirs.append(exe_dir)

    pkg_root = package_root_from_file(__file__, parents=4)
    dirs.append(pkg_root / "build" / "cinemadng")

    out: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            key = str(path.resolve()) if path.exists() else str(path)
        except OSError:
            key = str(path)
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    for file_path in files:
        _add(file_path)
    for directory in dirs:
        for name in names:
            _add(directory / name)
    return out
