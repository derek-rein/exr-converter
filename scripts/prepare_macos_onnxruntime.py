#!/usr/bin/env python3
"""Make the macOS ONNX Runtime wheel safe for Nuitka to copy.

``onnxruntime`` 1.24 and later ship ``libonnxruntime.<version>.dylib`` whose
install id is ``@rpath/libonnxruntime.1.dylib``. That shorter name is not a
file in the wheel. Nuitka resolves the id next to the binary, does not find
it, and aborts the macOS build.

This rewrites the install id so its basename is the real filename, and
rewrites any load command in the same directory that still uses the old id.
A soname symlink, if a future wheel adds one, is removed afterwards so
Nuitka does not copy the library twice. The bundle copy is still a normal
Mach-O, so ``scripts/macos_codesign.py`` signs it with the rest of the app.

Intel Macs do not reach this script with onnxruntime 1.24+ because those
wheels are arm64-only. The project pins an older wheel there. 1.23.x already
uses an install id that matches the filename, so this script leaves it alone.

Usage::

    uv run python scripts/prepare_macos_onnxruntime.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import onnxruntime

_ID_PREFIXES = ("@rpath/", "@loader_path/", "@executable_path/")


def aligned_install_id(filename: str, current_id: str) -> str | None:
    """Return a new install id whose basename is *filename*, or None.

    *filename* may be a path. *current_id* is the ``LC_ID_DYLIB`` string.
    """
    base = Path(filename).name
    current = current_id.strip()
    if not current:
        return f"@rpath/{base}"
    id_base = current.rsplit("/", 1)[-1]
    if id_base == base:
        return None
    prefix = current[: -len(id_base)] if current.endswith(id_base) else ""
    if prefix not in _ID_PREFIXES:
        prefix = "@rpath/"
    return prefix + base


def real_onnxruntime_dylibs(capi_dir: Path) -> list[Path]:
    """Versioned ONNX Runtime libraries, skipping soname symlinks."""
    return sorted(
        path
        for path in capi_dir.glob("libonnxruntime*.dylib")
        if path.is_file() and not path.is_symlink()
    )


def soname_symlinks(capi_dir: Path) -> list[Path]:
    """``libonnxruntime*.dylib`` symlinks sitting next to the real library."""
    return sorted(path for path in capi_dir.glob("libonnxruntime*.dylib") if path.is_symlink())


def _otool_entries(path: Path, flag: str) -> list[str]:
    proc = subprocess.run(
        ["otool", flag, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    entries: list[str] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith(":"):
            continue
        entries.append(stripped.split(" (", 1)[0].strip())
    return entries


def _unlock(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o200)
    # install_name_tool refuses to edit an ad-hoc signed wheel library.
    subprocess.run(
        ["codesign", "--remove-signature", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_install_name_tool(args: list[str]) -> None:
    proc = subprocess.run(
        ["install_name_tool", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"install_name_tool {' '.join(args)} failed: {detail}")


def prepare(capi_dir: Path) -> list[str]:
    """Rewrite libraries under *capi_dir*. Returns human-readable actions."""
    if not capi_dir.is_dir():
        raise RuntimeError(f"ONNX Runtime capi directory not found: {capi_dir}")

    dylibs = real_onnxruntime_dylibs(capi_dir)
    if not dylibs:
        raise RuntimeError(f"No libonnxruntime dylib in {capi_dir}")

    notes: list[str] = []
    id_map: dict[str, str] = {}
    for dylib in dylibs:
        current_ids = _otool_entries(dylib, "-D")
        current = current_ids[0] if current_ids else ""
        updated = aligned_install_id(dylib.name, current)
        if updated is None:
            notes.append(f"unchanged {dylib.name} ({current})")
            continue
        _unlock(dylib)
        _run_install_name_tool(["-id", updated, str(dylib)])
        id_map[current] = updated
        notes.append(f"install id {dylib.name}: {current} -> {updated}")

    if id_map:
        binaries = [
            path
            for path in sorted(capi_dir.iterdir())
            if path.is_file() and not path.is_symlink() and path.suffix in {".dylib", ".so"}
        ]
        for binary in binaries:
            loads = _otool_entries(binary, "-L")
            for old, new in id_map.items():
                if old in loads and old != new:
                    _unlock(binary)
                    _run_install_name_tool(["-change", old, new, str(binary)])
                    notes.append(f"load {binary.name}: {old} -> {new}")

    for link in soname_symlinks(capi_dir):
        link.unlink()
        notes.append(f"removed symlink {link.name}")
    return notes


def _capi_dir() -> Path:
    return Path(onnxruntime.__file__).resolve().parent / "capi"


def main() -> int:
    if sys.platform != "darwin":
        print("prepare_macos_onnxruntime: not macOS, nothing to do")
        return 0
    notes = prepare(_capi_dir())
    for note in notes:
        print(f"prepare_macos_onnxruntime: {note}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"prepare_macos_onnxruntime: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
