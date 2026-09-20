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

# Never copy SDK headers / docs / static libs into the user-facing bundle.
_FORBIDDEN_FILE_SUFFIXES = {".h", ".hpp", ".hh", ".hxx", ".idl", ".a", ".lib"}
_FORBIDDEN_FILE_NAMES = {
    "blackmagicrawapi.h",
    "blackmagicrawapidispatch.cpp",
    "blackmagicrawapidispatch.mm",
    "blackmagicrawapidispatch.h",
}
_FORBIDDEN_DIR_NAMES = {"include", "documents", "headers", "samples"}


def _is_forbidden_file(path: Path) -> bool:
    if path.suffix.lower() in _FORBIDDEN_FILE_SUFFIXES:
        return True
    return path.name.lower() in _FORBIDDEN_FILE_NAMES


def _is_forbidden_dir_name(name: str) -> bool:
    return name.lower() in _FORBIDDEN_DIR_NAMES


def _assert_runtime_only(dest: Path) -> None:
    """Refuse headers / static libs / SDK docs under the private braw/ dir."""
    leaked: list[Path] = []
    for p in dest.rglob("*"):
        if p.is_file() and _is_forbidden_file(p):
            leaked.append(p)
        elif p.is_dir() and _is_forbidden_dir_name(p.name):
            leaked.append(p)
    if leaked:
        preview = "\n".join(str(p) for p in leaked[:20])
        raise SystemExit(f"ERROR: forbidden SDK artifacts under {dest}:\n{preview}")


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
            if _is_forbidden_file(item):
                continue
            shutil.copy2(item, dest / item.name)
        elif item.is_dir():
            if _is_forbidden_dir_name(item.name):
                continue
            shutil.copytree(item, dest / item.name, ignore=ignore_macos_junk)

    # Framework trees sometimes carry a Headers/ symlink — strip it.
    for headers in list(dest.rglob("Headers")):
        if headers.is_dir() or headers.is_symlink():
            if headers.is_symlink() or headers.is_file():
                headers.unlink(missing_ok=True)
            else:
                shutil.rmtree(headers)

    _assert_runtime_only(dest)
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
