#!/usr/bin/env python3
"""Copy optional BRAW bridge + Blackmagic runtime libs into a Nuitka dist.

Layout written (private app directory)::

  <bundle>/braw/libbraw_bridge.{dylib,so,dll}
  <bundle>/braw/libBlackmagicRawAPI.* …
  <bundle>/braw/… (other SDK Libraries runtime files)

On macOS app bundles:

  Contents/MacOS/braw/libbraw_bridge.dylib     (next to the executable, like R3D)
  Contents/Frameworks/BlackmagicRawAPI.framework
  Contents/Frameworks/<other *.framework>      (GPU decoder runtime stays intact)

A nested ``.framework`` under ``Contents/MacOS`` makes ``codesign --deep``
report ``bundle format is ambiguous (could be app or framework)``.

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


def _is_macos_app(target: Path) -> bool:
    return target.suffix == ".app" or target.name.endswith(".app")


def _macos_exec_dir(app: Path) -> Path:
    mac_os = app / "Contents" / "MacOS"
    if mac_os.is_dir():
        return mac_os
    return app


def _macos_frameworks_dir(app: Path) -> Path:
    return app / "Contents" / "Frameworks"


def resolve_install_root(target: Path) -> Path:
    target = target.resolve()
    if _is_macos_app(target):
        return _macos_exec_dir(target)
    return target


def _is_framework_dir(path: Path) -> bool:
    return path.is_dir() and path.name.endswith(".framework")


def _strip_framework_headers(root: Path) -> None:
    """Framework trees sometimes carry a Headers/ symlink — strip it."""
    for headers in list(root.rglob("Headers")):
        if headers.is_dir() or headers.is_symlink():
            if headers.is_symlink() or headers.is_file():
                headers.unlink(missing_ok=True)
            else:
                shutil.rmtree(headers)


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

    fw_root: Path | None = None
    if _is_macos_app(target):
        fw_root = _macos_frameworks_dir(target)
        fw_root.mkdir(parents=True, exist_ok=True)

    shutil.copy2(bridge, dest / bridge.name)
    installed_frameworks: list[Path] = []
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
            # Nested .framework under Contents/MacOS confuses codesign --deep.
            target_dir = fw_root if fw_root is not None and _is_framework_dir(item) else dest
            dest_item = target_dir / item.name
            if dest_item.exists():
                shutil.rmtree(dest_item)
            shutil.copytree(item, dest_item, ignore=ignore_macos_junk)
            if _is_framework_dir(dest_item):
                installed_frameworks.append(dest_item)

    _strip_framework_headers(dest)
    if fw_root is not None:
        _strip_framework_headers(fw_root)
        # Refuse to leave a framework next to the executable (Release re-sign).
        leftover = [p for p in dest.iterdir() if _is_framework_dir(p)]
        if leftover:
            preview = "\n".join(str(p) for p in leftover)
            raise SystemExit(
                f"ERROR: .framework must live under Contents/Frameworks, not {dest}:\n{preview}"
            )

    _assert_runtime_only(dest)
    for fw in installed_frameworks:
        _assert_runtime_only(fw)
    safe_print(f"Installed BRAW runtime -> {dest}")
    for fw in installed_frameworks:
        safe_print(f"Installed BRAW framework -> {fw}")
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
