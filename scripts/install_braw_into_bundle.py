#!/usr/bin/env python3
"""Copy optional BRAW bridge + Blackmagic runtime libs into a Nuitka dist.

Layout written (private app directory)::

  <bundle>/braw/libbraw_bridge.{dylib,so,dll}
  <bundle>/braw/libBlackmagicRawAPI.* …
  <bundle>/braw/… (other SDK Libraries runtime files)

On macOS app bundles, *bundle* is ``Contents/MacOS`` (next to the executable).

Usage::

  python3 scripts/install_braw_into_bundle.py "dist/EXR Converter.app"
  python3 scripts/install_braw_into_bundle.py dist/main.dist

If ``build/braw/libbraw_bridge.*`` is missing, exits 0 with a skip message
(optional feature).
"""

from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from packaging_util import ignore_macos_junk, is_macos_junk_name, safe_print  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "braw"


def _bridge_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "libbraw_bridge.dylib"
    if system == "Windows":
        return "libbraw_bridge.dll"
    return "libbraw_bridge.so"


def _macos_exec_dir(app: Path) -> Path:
    mac_os = app / "Contents" / "MacOS"
    if mac_os.is_dir():
        return mac_os
    return app


def resolve_install_root(target: Path) -> Path:
    target = target.resolve()
    if target.suffix == ".app" or target.name.endswith(".app"):
        return _macos_exec_dir(target)
    return target


def install(target: Path, build_dir: Path = BUILD) -> Path | None:
    bridge = build_dir / _bridge_name()
    if not bridge.is_file():
        alt = build_dir / "braw_bridge.dll"
        if alt.is_file():
            bridge = alt
    if not bridge.is_file():
        safe_print(f"skip: no bridge at {bridge} (BRAW optional)", file=sys.stderr)
        return None

    redist = build_dir / "redistributable"
    if not redist.is_dir():
        safe_print(f"ERROR: missing redistributable dir {redist}", file=sys.stderr)
        raise SystemExit(2)

    root = resolve_install_root(target)
    dest = root / "braw"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    shutil.copy2(bridge, dest / bridge.name)
    for item in redist.iterdir():
        if is_macos_junk_name(item.name):
            continue
        if item.is_file():
            shutil.copy2(item, dest / item.name)
        elif item.is_dir():
            shutil.copytree(item, dest / item.name, ignore=ignore_macos_junk)

    safe_print(f"Installed BRAW runtime -> {dest}")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path, help="App bundle, main.dist, or install root")
    ap.add_argument(
        "--build-dir",
        type=Path,
        default=BUILD,
        help=f"bridge build output (default {BUILD})",
    )
    args = ap.parse_args()
    if not args.bundle.exists():
        raise SystemExit(f"bundle path not found: {args.bundle}")
    install(args.bundle, args.build_dir.resolve())


if __name__ == "__main__":
    main()
