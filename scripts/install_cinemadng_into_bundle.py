#!/usr/bin/env python3
"""Copy libcinemadng next to the frozen executable so codesign picks it up.

On a macOS app bundle the library goes in Contents/MacOS, beside the
executable. Elsewhere it goes in the Nuitka dist directory.

Usage::

  python3 scripts/install_cinemadng_into_bundle.py "dist/EXR Converter.app"
  python3 scripts/install_cinemadng_into_bundle.py dist/main.dist
"""

from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "cinemadng"


def _library_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "libcinemadng.dylib"
    if system == "Windows":
        return "cinemadng.dll"
    return "libcinemadng.so"


def _install_root(target: Path) -> Path:
    target = target.resolve()
    if target.suffix == ".app" or target.name.endswith(".app"):
        mac_os = target / "Contents" / "MacOS"
        if mac_os.is_dir():
            return mac_os
    return target


def install(target: Path) -> Path:
    library = BUILD / _library_name()
    if not library.is_file():
        alt = BUILD / "libcinemadng.dll"
        if alt.is_file():
            library = alt
    if not library.is_file():
        print(f"ERROR: missing {library}. Run scripts/build_cinemadng.py first.", file=sys.stderr)
        raise SystemExit(1)
    root = _install_root(target)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / library.name
    shutil.copy2(library, dest)
    print(f"installed {dest} ({dest.stat().st_size} bytes)")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="Nuitka dist directory or .app bundle")
    args = parser.parse_args()
    install(args.target)


if __name__ == "__main__":
    main()
