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


def redistributable_marker() -> str:
    """Primary runtime library filename that must sit next to decoder .so/.dylib/.dll."""
    system = platform.system()
    if system == "Darwin":
        # Official Mac SDK ships BlackmagicRawAPI.framework, not a loose dylib.
        return "libBlackmagicRawAPI.dylib"
    if system == "Windows":
        return "BlackmagicRawAPI.dll"
    return "libBlackmagicRawAPI.so"


def _macos_framework_roots(exe_dir: Path) -> list[Path]:
    """Contents/Frameworks — required codesign location for nested BRAW frameworks."""
    roots: list[Path] = []
    if exe_dir.name == "MacOS" and exe_dir.parent.name == "Contents":
        fw = exe_dir.parent / "Frameworks"
        roots.extend((fw, fw / "braw"))
    elif exe_dir.name == "Contents" and (exe_dir / "MacOS").is_dir():
        fw = exe_dir / "Frameworks"
        roots.extend((fw, fw / "braw"))
    elif (exe_dir / "Contents" / "Frameworks").is_dir():
        fw = exe_dir / "Contents" / "Frameworks"
        roots.extend((fw, fw / "braw"))
    return roots


def _looks_like_braw_runtime_dir(path: Path) -> bool:
    """True if *path* holds the Blackmagic RAW API library or framework."""
    marker = redistributable_marker()
    if (path / marker).is_file():
        return True
    if (path / "BlackmagicRawAPI.framework").is_dir():
        return True
    if path.name == "BlackmagicRawAPI.framework" and path.is_dir():
        return True
    return any(path.glob("libBlackmagicRawAPI*")) or any(path.glob("BlackmagicRawAPI*"))


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
    """Folders that may contain libBlackmagicRawAPI.* or BlackmagicRawAPI.framework."""
    marker = redistributable_marker()

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
        roots.extend(_macos_framework_roots(exe_dir))

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
        if (rp / marker).is_file() or rp.is_dir():
            out.append(rp)
    return out


def find_redistributable_dir(bridge_path: Path | None) -> Path | None:
    """Return the first folder that contains the Blackmagic RAW API library.

    On a packaged macOS app the factory path is ``Contents/Frameworks``
    (parent of ``BlackmagicRawAPI.framework``), not ``Contents/MacOS/braw``.
    """
    for cand in redistributable_candidates(bridge_path):
        if not cand.is_dir():
            continue
        if cand.name == "BlackmagicRawAPI.framework":
            return cand.parent
        if _looks_like_braw_runtime_dir(cand):
            return cand
    return None
