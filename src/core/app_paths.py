"""Shared discovery of app roots (source checkout vs frozen / Nuitka binary)."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def is_frozen_app() -> bool:
    """True inside a Nuitka / PyInstaller-style binary (not a source venv run)."""
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return True
    # Nuitka marks compiled modules; caller modules may not share globals.
    return False


def package_root_from_file(file_path: str | Path, *, parents: int = 2) -> Path:
    """Repo / install root from a module ``__file__`` (``parents=2`` → src/core → root)."""
    try:
        p = Path(file_path).resolve()
        for _ in range(max(0, parents)):
            p = p.parent
        return p
    except OSError:
        return Path.cwd()


def runtime_exe_dirs() -> list[Path]:
    """Directories that may sit next to the real application binary.

    Always consider ``sys.executable`` / ``argv[0]`` — Nuitka standalone often
    does **not** set ``sys.frozen``, so frozen-only checks miss private app
    data folders (e.g. ``Contents/MacOS/r3d/``).
    """
    dirs: list[Path] = []
    for raw in (sys.executable, sys.argv[0] if sys.argv else ""):
        if not raw:
            continue
        try:
            p = Path(raw).resolve()
        except OSError:
            continue
        if p.is_file():
            dirs.append(p.parent)
        elif p.is_dir():
            dirs.append(p)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    # macOS .app: private data under Contents/MacOS, Resources, or Frameworks.
    # BRAW ships BlackmagicRawAPI.framework in Frameworks/ (not MacOS/) so
    # codesign --deep does not treat the outer .app as an ambiguous bundle.
    for d in list(dirs):
        if d.name == "MacOS" and d.parent.name == "Contents":
            dirs.append(d.parent / "Resources")
            dirs.append(d.parent / "Frameworks")
        elif (d / "MacOS").is_dir():
            dirs.append(d / "MacOS")
            dirs.append(d / "Resources")
            if (d / "Frameworks").is_dir() or d.name == "Contents":
                dirs.append(d / "Frameworks")
    # Deduplicate while preserving order.
    out: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            key = str(d.resolve())
        except OSError:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def is_msix_packaged() -> bool:
    """True when this process is an installed MSIX / Microsoft Store app.

    The package directory under ``WindowsApps`` is read-only. Unpackaged runs,
    including the Inno setup ``.exe``, return False.
    """
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        return False
    # Probe with a null buffer. ERROR_INSUFFICIENT_BUFFER (122) means the
    # process has a package identity. APPMODEL_ERROR_NO_PACKAGE (15700) does not.
    get_name = kernel32.GetCurrentPackageFullName
    get_name.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p]
    get_name.restype = ctypes.c_long
    length = ctypes.c_uint32(0)
    rc = int(get_name(ctypes.byref(length), None))
    return rc == 122


def _resolved(path: Path) -> str:
    try:
        text = str(path.resolve())
    except OSError:
        text = str(path)
    return os.path.normcase(text)


def is_under_install_dir(path: Path) -> bool:
    """True when *path* is the app binary's directory or a child of it.

    Relative paths resolve against the current working directory. An MSIX
    install lives there and is read-only, so writable files must not.
    """
    if not str(path):
        target = _resolved(Path.cwd())
    else:
        candidate = path if path.is_absolute() else Path.cwd() / path
        target = _resolved(candidate)
    for root in runtime_exe_dirs():
        root_text = _resolved(root)
        if target == root_text:
            return True
        prefix = root_text if root_text.endswith(os.sep) else root_text + os.sep
        if target.startswith(prefix):
            return True
    return False


def outside_install_dir(path: Path, fallback: Path) -> Path:
    """Return *path* when it is an absolute directory outside the install tree.

    Otherwise return *fallback*. Raise when both sit inside the install
    directory — writing there fails for an MSIX package.
    """
    if path.is_absolute() and not is_under_install_dir(path):
        return path
    if fallback.is_absolute() and not is_under_install_dir(fallback):
        return fallback
    raise RuntimeError(
        "No writable directory outside the application install folder. "
        "An MSIX install directory is read-only."
    )


def profile_data_dir(org: str, app: str) -> Path:
    """Per-user data root, aligned with Qt ``AppDataLocation`` when org and app are set.

    Used when Qt returns an empty or install-relative path. Never the install directory.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        path = root / org / app
        if not path.is_absolute() or is_under_install_dir(path):
            path = Path.home() / "AppData" / "Roaming" / org / app
        return path
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / org / app
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    path = root / org / app
    if not path.is_absolute() or is_under_install_dir(path):
        path = Path.home() / ".local" / "share" / org / app
    return path


def writable_app_subdir(base: str, *parts: str, fallback: Path) -> Path:
    """Directory under a Qt writable location, or *fallback* when that location is unusable.

    ``QStandardPaths`` returns an empty string until the application name is set,
    and a relative join would then create files in the process working directory.
    For a Store install that directory is the read-only package root.
    """
    candidate = Path(base).joinpath(*parts) if base.strip() else Path()
    if base.strip() and candidate.is_absolute() and not is_under_install_dir(candidate):
        chosen = candidate
    else:
        chosen = fallback.joinpath(*parts)
        if not chosen.is_absolute() or is_under_install_dir(chosen):
            raise RuntimeError(
                "No writable directory outside the application install folder. "
                "An MSIX install directory is read-only."
            )
    chosen.mkdir(parents=True, exist_ok=True)
    return chosen


def leave_read_only_install_cwd() -> Path | None:
    """Leave the MSIX package root so relative writes do not hit a read-only folder.

    Start-menu launch sets the working directory to the package. Returns the
    previous directory when it changes, otherwise None. Unpackaged runs
    (development, Inno setup) are left where they are.
    """
    if not is_msix_packaged():
        return None
    cwd = Path.cwd()
    if not is_under_install_dir(cwd):
        return None
    home = Path.home()
    if is_under_install_dir(home):
        return None
    os.chdir(home)
    return cwd
