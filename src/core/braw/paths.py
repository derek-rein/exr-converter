"""Locate libbraw_bridge and Blackmagic RAW runtime libraries."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from ..app_paths import package_root_from_file, runtime_exe_dirs


def bridge_names() -> tuple[str, ...]:
    system = platform.system()
    if system == "Darwin":
        return ("libbraw_bridge.dylib",)
    if system == "Windows":
        return ("libbraw_bridge.dll", "braw_bridge.dll")
    return ("libbraw_bridge.so",)


# Official macOS SDK ships a framework (not libBlackmagicRawAPI.dylib).
MACOS_API_FRAMEWORK = "BlackmagicRawAPI.framework"


def redistributable_marker() -> str:
    """Primary runtime library filename that must sit next to decoder .so/.dylib/.dll."""
    system = platform.system()
    if system == "Darwin":
        return "libBlackmagicRawAPI.dylib"
    if system == "Windows":
        return "BlackmagicRawAPI.dll"
    return "libBlackmagicRawAPI.so"


def _has_runtime_api(folder: Path) -> bool:
    """True if *folder* contains the Blackmagic RAW API library or framework."""
    if not folder.is_dir():
        return False
    if (folder / redistributable_marker()).is_file():
        return True
    if (folder / MACOS_API_FRAMEWORK).is_dir():
        return True
    return any(folder.glob("libBlackmagicRawAPI*")) or any(folder.glob("BlackmagicRawAPI*"))


def bridge_candidates() -> list[Path]:
    """Search paths for libbraw_bridge.*"""
    names = bridge_names()
    dirs: list[Path] = []
    files: list[Path] = []

    env = os.environ.get("EXR_CONVERTER_BRAW_BRIDGE", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.suffix.lower() in {".dylib", ".so", ".dll"}:
            files.append(p)
        else:
            dirs.append(p)

    for exe_dir in runtime_exe_dirs():
        dirs.append(exe_dir / "braw")
        dirs.append(exe_dir)

    pkg_root = package_root_from_file(__file__, parents=4)
    dirs.extend(
        [
            pkg_root / "build" / "braw",
            pkg_root / "native" / "braw",
            pkg_root / "resources" / "braw",
        ]
    )

    out: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            key = str(p.resolve()) if p.exists() else str(p)
        except OSError:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    for f in files:
        _add(f)
    for d in dirs:
        for name in names:
            _add(d / name)
    return out


def redistributable_candidates(bridge_path: Path | None) -> list[Path]:
    """Folders that may contain libBlackmagicRawAPI.* or the macOS framework."""
    roots: list[Path] = []
    env = os.environ.get("EXR_CONVERTER_BRAW_LIBS", "").strip()
    if env:
        roots.append(Path(env).expanduser().resolve())
    env2 = os.environ.get("BRAW_SDK_LIBS", "").strip()
    if env2:
        roots.append(Path(env2).expanduser().resolve())
    sdk = os.environ.get("BRAW_SDK_ROOT", "").strip()
    if sdk:
        root = Path(sdk).expanduser().resolve()
        roots.append(root / "Linux" / "Libraries")
        roots.append(root / "Mac" / "Libraries")
        roots.append(root / "Win" / "Libraries")
        roots.append(root / "Libraries")

    if bridge_path is not None:
        roots.append(bridge_path.parent)
        roots.append(bridge_path.parent / "redistributable")

    for exe_dir in runtime_exe_dirs():
        roots.append(exe_dir / "braw")
        roots.append(exe_dir)

    pkg_root = package_root_from_file(__file__, parents=4)
    roots.append(pkg_root / "build" / "braw" / "redistributable")
    roots.append(pkg_root / "resources" / "braw")

    for extra in (
        Path("/usr/lib64/blackmagic/BlackmagicRAWSDK/Linux/Libraries"),
        Path("/usr/lib/blackmagic/BlackmagicRAWSDK/Linux/Libraries"),
        Path("/opt/blackmagic/BlackmagicRAWSDK/Linux/Libraries"),
        Path.home() / ".braw-sdk" / "slim" / "Linux" / "Libraries",
        Path.home() / ".braw-sdk" / "Linux" / "Libraries",
    ):
        roots.append(extra)

    out: list[Path] = []
    seen: set[Path] = set()
    for r in roots:
        try:
            rp = r.resolve()
        except OSError:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        if _has_runtime_api(rp) or rp.is_dir():
            out.append(rp)
    return out


def find_redistributable_dir(bridge_path: Path | None) -> Path | None:
    """Return the first folder that contains the Blackmagic RAW API library."""
    for cand in redistributable_candidates(bridge_path):
        if _has_runtime_api(cand):
            return cand
    return None
