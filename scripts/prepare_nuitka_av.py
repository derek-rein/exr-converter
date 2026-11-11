#!/usr/bin/env python3
"""Hide PyAV's Cython sources so Nuitka ships the compiled extensions.

PyAV 19 installs ``utils.py`` (Cython) next to ``utils.abi3.so``. Nuitka
follows the ``.py`` and either crashes in its optimizer or leaves the
extension out. Extension modules then fail at startup with
``No module named 'av.utils'`` because they import each other from C.

This renames those Cython ``.py`` files when a compiled extension with the
same module name sits beside them. Real Python files such as ``about.py``
and ``__init__.py`` stay. Run it after ``uv sync`` and before Nuitka, and
pass ``--include-package=av``.

Usage::

    uv run python scripts/prepare_nuitka_av.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import av

_EXT_SUFFIXES = {".so", ".pyd"}


def _extension_module_names(directory: Path) -> set[str]:
    names: set[str] = set()
    for path in directory.iterdir():
        if path.is_file() and path.suffix.lower() in _EXT_SUFFIXES:
            names.add(path.name.split(".", 1)[0])
    return names


def _is_cython_source(path: Path) -> bool:
    head = path.read_text(encoding="utf-8", errors="replace")[:800]
    return "import cython" in head or "cython.cimports" in head


def hide_cython_companions(root: Path) -> list[Path]:
    """Rename Cython ``.py`` files that shadow a compiled extension.

    Returns the paths they were moved to. ``root`` is the ``av`` package
    directory. Subpackages are included.
    """
    hidden: list[Path] = []
    directories = [root, *[path for path in root.rglob("*") if path.is_dir()]]
    for directory in directories:
        for name in sorted(_extension_module_names(directory)):
            source = directory / f"{name}.py"
            if not source.is_file() or not _is_cython_source(source):
                continue
            dest = source.with_name(source.name + ".cython-src")
            source.rename(dest)
            hidden.append(dest)
    return hidden


def main() -> None:
    root = Path(av.__file__).resolve().parent
    hidden = hide_cython_companions(root)
    print(f"prepare_nuitka_av: hid {len(hidden)} Cython source file(s) under {root}")
    for path in hidden:
        print(f"  {path.name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"prepare_nuitka_av: {exc}", file=sys.stderr)
        raise
