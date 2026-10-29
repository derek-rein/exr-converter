"""Mounted volumes for file browsers (cross-platform).

``QFileSystemModel`` with the usual ``Dirs | NoDotAndDotDot`` filter does **not**
show macOS ``/Volumes`` (it is treated as a hidden directory), so external
drives never appear under ``/``. On Windows, ``QDir.rootPath()`` is typically
only the system drive (``C:/``), so ``D:`` / USB sticks are also invisible.

This module:

* lists **user-facing volumes** via :class:`~PySide6.QtCore.QStorageInfo`
* feeds :class:`~src.gui.browser_tree.BrowserTreeModel`, where each volume is
  a **top-level** tree row
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QDir, QStorageInfo

# Pseudo / system filesystems that are not useful as browser roots.
_SKIP_FS_TYPES = frozenset(
    {
        "autofs",
        "devfs",
        "devtmpfs",
        "proc",
        "sysfs",
        "tmpfs",
        "cgroup",
        "cgroup2",
        "squashfs",
        "overlay",
        "rpc_pipefs",
        "fusectl",
        "debugfs",
        "tracefs",
        "configfs",
        "pstore",
        "securityfs",
        "mqueue",
        "hugetlbfs",
        "binfmt_misc",
        "bpf",
        "nsfs",
        "ramfs",
        "rootfs",
    }
)

# Path prefixes that are never useful browser roots (platform-specific).
_SKIP_PATH_PREFIXES_UNIX = (
    "/System/Volumes",  # macOS APFS helpers (Data, Preboot, VM, …)
    "/private/var/vm",
    "/dev",
    "/proc",
    "/sys",
    "/run/user",  # often per-session bus mounts; real media is under /run/media
)


@dataclass(frozen=True, slots=True)
class VolumeInfo:
    """One user-facing mount the browser should treat as a top-level root."""

    path: str
    """Absolute mount path (``/``, ``/Volumes/KINGSTON``, ``C:/``, …)."""

    name: str
    """Label for the tree / places sidebar (volume name or drive letter)."""

    is_system_root: bool = False
    """True for the OS boot volume (``/`` or ``C:/``)."""


def _norm_mount_path(path: str) -> str:
    """Normalize a mount path for comparison (stable separators, no trailing slash)."""
    raw = (path or "").strip()
    if not raw:
        return ""
    # QStorageInfo / QDir use forward slashes on Windows; Path handles either.
    p = Path(raw)
    try:
        # Prefer absolute form without resolving symlinks (keeps /Volumes/Name).
        s = os.path.abspath(str(p))
    except (OSError, RuntimeError, ValueError):
        s = str(p)
    if sys.platform == "win32":
        s = s.replace("/", "\\")
        # Drive roots: keep trailing backslash form as ``C:\`` style → strip to ``C:``
        if len(s) == 3 and s[1] == ":" and s[2] in "\\/":
            return s[:2] + "\\"
        if len(s) == 2 and s[1] == ":":
            return s + "\\"
        return s.rstrip("\\") or s
    # Unix: keep ``/`` as-is; strip trailing slashes from others.
    if s != "/":
        s = s.rstrip("/")
    return s or "/"


def _fs_type_str(info: QStorageInfo) -> str:
    raw = info.fileSystemType()
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace").lower()
    return str(raw or "").lower()


def _should_skip_mount(info: QStorageInfo) -> bool:
    if not info.isValid() or not info.isReady():
        return True
    path = _norm_mount_path(info.rootPath())
    if not path:
        return True

    fs = _fs_type_str(info)
    if fs in _SKIP_FS_TYPES:
        return True

    if sys.platform != "win32":
        for prefix in _SKIP_PATH_PREFIXES_UNIX:
            if path == prefix or path.startswith(prefix + "/"):
                return True
        # Linux session garbage under /run except conventional media mounts.
        if path.startswith("/run/") and not path.startswith("/run/media"):
            return True

    # Zero-size pseudo mounts (skip unless it is the system root).
    if not info.isRoot() and info.bytesTotal() <= 0:
        return True

    return False


def _display_name_for(info: QStorageInfo, path: str) -> str:
    name = (info.displayName() or info.name() or "").strip()
    if name:
        return name
    if sys.platform == "win32":
        # ``C:\`` → ``C:``
        drive = path.rstrip("\\/")
        return drive or path
    base = Path(path).name
    return base or path


def list_browser_volumes() -> list[VolumeInfo]:
    """Return mounted volumes suitable as browser roots (deduped, stable order).

    Order: system root first, then other volumes by display name (case-insensitive).
    """
    found: list[VolumeInfo] = []
    seen_paths: set[str] = set()
    # Device identity for dedupe (``/`` vs ``/Volumes/Macintosh HD``).
    seen_devices: set[str] = set()

    for info in QStorageInfo.mountedVolumes():
        if _should_skip_mount(info):
            continue
        path = _norm_mount_path(info.rootPath())
        if path in seen_paths:
            continue

        device_raw = info.device()
        if isinstance(device_raw, (bytes, bytearray)):
            device = bytes(device_raw).decode("utf-8", "replace")
        else:
            device = str(device_raw or "")

        # Prefer the true system root over firmlink duplicates (macOS).
        if device and device in seen_devices and not info.isRoot():
            continue

        name = _display_name_for(info, path)
        found.append(
            VolumeInfo(
                path=path,
                name=name,
                is_system_root=bool(info.isRoot()),
            )
        )
        seen_paths.add(path)
        if device:
            seen_devices.add(device)

    # Windows fallback: ensure every drive letter from QDir.drives() is present
    # even if QStorageInfo briefly omits a ready stick.
    if sys.platform == "win32":
        for drive in QDir.drives():
            path = _norm_mount_path(drive.absolutePath())
            if not path or path in seen_paths:
                continue
            # Only add if the path exists / is a directory.
            try:
                if not Path(path).exists():
                    continue
            except OSError:
                continue
            found.append(
                VolumeInfo(
                    path=path,
                    name=path.rstrip("\\/") or path,
                    is_system_root=False,
                )
            )
            seen_paths.add(path)

    if not found:
        # Absolute fallback so the tree is never empty.
        root = _norm_mount_path(QDir.rootPath() or ("C:\\" if sys.platform == "win32" else "/"))
        found.append(VolumeInfo(path=root, name=root, is_system_root=True))

    roots = [v for v in found if v.is_system_root]
    others = sorted(
        [v for v in found if not v.is_system_root],
        key=lambda v: (v.name.lower(), v.path.lower()),
    )
    # At most one system root (first).
    ordered = (roots[:1] if roots else []) + others
    if not roots and others:
        return others
    return ordered
