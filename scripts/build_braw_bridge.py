#!/usr/bin/env python3
"""Build the optional Blackmagic RAW C ABI bridge shared library.

Requires a local copy of the official Blackmagic RAW SDK (headers + runtime
Libraries). The SDK is proprietary — do not commit it.

Usage:
  BRAW_SDK_ROOT=/path/to/slim python3 scripts/build_braw_bridge.py
  # slim/ layout: Linux/Include/BlackmagicRawAPI.h + Linux/Libraries/*.so
  # or conventional: /usr/lib64/blackmagic/BlackmagicRAWSDK
  # or: python3 scripts/fetch_braw_sdk.py && python3 scripts/build_braw_bridge.py

Outputs:
  build/braw/libbraw_bridge.{dylib,so,dll}
  build/braw/redistributable/   (copied Blackmagic runtime libs)

See docs/braw.md for license and packaging notes.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from packaging_util import ignore_macos_junk, is_macos_junk_name, safe_print  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "native" / "braw" / "braw_bridge.cpp"
HDR_DIR = ROOT / "native" / "braw"
OUT_DIR = ROOT / "build" / "braw"


def _header_ok(include: Path) -> bool:
    """Linux/macOS ship BlackmagicRawAPI.h; Windows ships the IDL + dispatch."""
    if (include / "BlackmagicRawAPI.h").is_file():
        return True
    return (include / "BlackmagicRawAPI.idl").is_file() and (
        include / "BlackmagicRawAPIDispatch.h"
    ).is_file()


def _prepare_windows_include(include: Path, out_dir: Path) -> Path:
    """Generate BlackmagicRawAPI.h from the official IDL when missing (Win SDK)."""
    if (include / "BlackmagicRawAPI.h").is_file():
        return include
    idl = include / "BlackmagicRawAPI.idl"
    if not idl.is_file():
        raise SystemExit(
            f"Windows BRAW SDK missing BlackmagicRawAPI.h and .idl under {include}"
        )
    gen = out_dir / "win_include"
    gen.mkdir(parents=True, exist_ok=True)
    for name in (
        "BlackmagicRawAPIDispatch.h",
        "BlackmagicRawAPIDispatch.cpp",
        "BlackmagicRawAPI.idl",
    ):
        src = include / name
        if src.is_file():
            shutil.copy2(src, gen / name)
    generated = gen / "BlackmagicRawAPI.h"
    if not generated.is_file():
        cmd = [
            "midl.exe",
            "/nologo",
            "/W1",
            "/char",
            "signed",
            "/env",
            "x64",
            "/Oicf",
            "/out",
            str(gen),
            "/h",
            "BlackmagicRawAPI.h",
            str(gen / "BlackmagicRawAPI.idl"),
        ]
        safe_print("Generating BlackmagicRawAPI.h via midl ...", file=sys.stderr)
        subprocess.check_call(cmd)
    if not generated.is_file():
        raise SystemExit("midl did not produce BlackmagicRawAPI.h")
    return gen


def _find_sdk(explicit: str | None) -> tuple[Path, Path, Path]:
    """Return (sdk_root, include_dir, libraries_dir)."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    env = os.environ.get("BRAW_SDK_ROOT", "").strip()
    if env:
        candidates.append(Path(env).expanduser().resolve())

    candidates.extend(
        [
            (Path.home() / ".braw-sdk" / "slim").resolve(),
            (Path.home() / ".braw-sdk").resolve(),
            (ROOT / ".braw-sdk" / "slim").resolve(),
            (ROOT / ".braw-sdk").resolve(),
            Path("/usr/lib64/blackmagic/BlackmagicRAWSDK").resolve(),
            Path("/usr/lib/blackmagic/BlackmagicRAWSDK").resolve(),
            Path("/opt/blackmagic/BlackmagicRAWSDK").resolve(),
            (Path.home() / "sdk" / "BlackmagicRAWSDK").resolve(),
            (Path.home() / "code" / "BlackmagicRAWSDK").resolve(),
            (ROOT / "BlackmagicRAWSDK").resolve(),
            (ROOT / "slim").resolve(),
        ]
    )

    system = platform.system()
    plat_dirs = {
        "Linux": ("Linux",),
        "Darwin": ("Mac", "macOS", "MacOS"),
        "Windows": ("Win", "Windows"),
    }.get(system, ("Linux",))

    for c in candidates:
        if not c.is_dir():
            continue
        include_opts = [c / "Include"]
        lib_opts = [c / "Libraries"]
        for plat in plat_dirs:
            include_opts.append(c / plat / "Include")
            lib_opts.append(c / plat / "Libraries")
        include_opts.append(c)
        for inc in include_opts:
            if not _header_ok(inc):
                continue
            for lib in lib_opts:
                if lib.is_dir() and (
                    any(lib.glob("libBlackmagicRawAPI*")) or any(lib.glob("BlackmagicRawAPI*"))
                ):
                    return c, inc, lib
    raise SystemExit(
        "Blackmagic RAW SDK not found. Set BRAW_SDK_ROOT to the unpacked SDK "
        "(e.g. slim/ with Linux/Include + Linux/Libraries), or place it at "
        "~/.braw-sdk or /usr/lib64/blackmagic/BlackmagicRAWSDK, or run: "
        "python3 scripts/fetch_braw_sdk.py  (private CI feed). "
        "See docs/braw.md."
    )


def _find_dispatch(include: Path) -> Path | None:
    for name in (
        "BlackmagicRawAPIDispatch.cpp",
        "BlackmagicRawAPIDispatch.mm",
    ):
        p = include / name
        if p.is_file():
            return p
    return None


def _platform_bits() -> tuple[str, list[str]]:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        return "dylib", ["-framework", "CoreFoundation", "-lc++"]
    if system == "Linux":
        return "so", ["-ldl", "-lpthread", "-lstdc++"]
    if system == "Windows":
        return "dll", []
    raise SystemExit(f"Unsupported platform: {system} {machine}")


def build(include: Path, libraries: Path, out_dir: Path, verbose: bool) -> Path:
    ext, extra = _platform_bits()
    if not SRC.is_file():
        raise SystemExit(f"Missing bridge source: {SRC}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_lib = out_dir / f"libbraw_bridge.{ext}"

    system = platform.system()
    if system == "Windows":
        include = _prepare_windows_include(include, out_dir)
    dispatch = _find_dispatch(include)
    if system == "Windows":
        win_common = [
            "cl.exe",
            "/nologo",
            "/O2",
            "/MD",
            "/EHsc",
            "/std:c++17",
            f"/I{include}",
            f"/I{HDR_DIR}",
            "/DBRAW_BRIDGE_EXPORTS",
            "/c",
        ]
        objs: list[Path] = []
        sources = [SRC]
        if dispatch is not None:
            sources.append(dispatch)
        else:
            raise SystemExit(
                f"Missing BlackmagicRawAPIDispatch.cpp next to headers in {include}"
            )
        for src in sources:
            obj = out_dir / f"{src.stem}.obj"
            cmd = [*win_common, str(src), f"/Fo{obj}"]
            if verbose:
                print(" ".join(cmd), file=sys.stderr)
            subprocess.check_call(cmd)
            objs.append(obj)
        cmd = [
            "cl.exe",
            "/nologo",
            "/LD",
            "/MD",
            *[str(o) for o in objs],
            f"/Fe{out_lib}",
            "/link",
            "ole32.lib",
            "oleaut32.lib",
        ]
        if verbose:
            print(" ".join(cmd), file=sys.stderr)
        subprocess.check_call(cmd)
    else:
        cxx = os.environ.get("CXX") or ("g++" if system == "Linux" else "c++")
        common = [
            "-std=c++17",
            "-O2",
            "-fPIC",
            "-fvisibility=hidden",
            f"-I{include}",
            f"-I{HDR_DIR}",
            "-DBRAW_BRIDGE_EXPORTS",
        ]
        objs: list[Path] = []
        sources = [SRC]
        if dispatch is not None:
            sources.append(dispatch)
        elif system == "Linux":
            raise SystemExit(
                f"Missing BlackmagicRawAPIDispatch.cpp next to headers in {include}"
            )
        for src in sources:
            obj = out_dir / f"{src.stem}.o"
            compile_cpp = [cxx, *common, "-c", str(src), "-o", str(obj)]
            if verbose:
                print(" ".join(compile_cpp), file=sys.stderr)
            subprocess.check_call(compile_cpp)
            objs.append(obj)
        cmd = [
            cxx,
            "-shared",
            *[str(o) for o in objs],
            "-o",
            str(out_lib),
            *extra,
        ]
        if system == "Darwin":
            cmd.extend(
                [
                    "-Wl,-rpath,@loader_path",
                    "-Wl,-rpath,@loader_path/redistributable",
                ]
            )
        elif system == "Linux":
            cmd.extend(["-Wl,-rpath,$ORIGIN", "-Wl,-rpath,$ORIGIN/redistributable"])
        if verbose:
            print(" ".join(cmd), file=sys.stderr)
        subprocess.check_call(cmd)

    dest_redist = out_dir / "redistributable"
    if dest_redist.exists():
        shutil.rmtree(dest_redist)
    dest_redist.mkdir(parents=True)
    for item in libraries.iterdir():
        if is_macos_junk_name(item.name):
            continue
        if item.is_file():
            shutil.copy2(item, dest_redist / item.name)
        elif item.is_dir():
            shutil.copytree(item, dest_redist / item.name, ignore=ignore_macos_junk)

    for p in dest_redist.rglob("*"):
        if p.is_file() and is_macos_junk_name(p.name):
            p.unlink(missing_ok=True)

    safe_print(f"Built {out_lib}")
    safe_print(f"Runtime libraries -> {dest_redist}")
    safe_print(f"SDK include: {include}")
    return out_lib


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sdk",
        default=None,
        help="Path to unpacked Blackmagic RAW SDK root (or set BRAW_SDK_ROOT)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=OUT_DIR,
        help=f"Output directory (default: {OUT_DIR})",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    _root, include, libraries = _find_sdk(args.sdk)
    build(include, libraries, args.out.resolve(), args.verbose)


if __name__ == "__main__":
    main()
