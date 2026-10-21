"""Publish Quick Look format toggles for the macOS preview extension.

The extension's ``QLSupportedContentTypes`` list is compiled into Info.plist
and cannot change at runtime. Preferences are a JSON file:

* **App Group container** (primary). Group id
  ``83546T4BT8.com.vfxtools.exrconverter`` is a macOS-style, team-prefixed id.
  Apple treats that form as an unrestricted entitlement, so the Developer ID
  signature does not embed a provisioning profile. The host app is not
  sandboxed; ``containerURLForSecurityApplicationGroupIdentifier:`` returns a
  path only when this process's signature carries the entitlement (the
  notarized ``.app``). ``uv run`` and ad-hoc bundles get ``nil``.
* **Application Support fallback** so a sandboxed extension can still read
  toggles when the group container is missing. The appex has a home-relative
  read exception for that one file.

Missing JSON means the built-in defaults (OpenEXR and DPX on, everything else
off). Finder may keep a cached preview until the Quick Look cache is cleared.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QSettings

from .quicklook_formats import (
    APP_GROUP_ID,
    APP_SUPPORT_FOLDER,
    JSON_FILENAME,
    QUICKLOOK_FORMATS,
    SETTINGS_PREFIX,
    QuickLookFormat,
    default_enabled_map,
    encode_preferences,
    format_by_key,
)

__all__ = [
    "QUICKLOOK_FORMATS",
    "QuickLookFormat",
    "application_support_json",
    "app_group_container_dir",
    "clear_quicklook_cache",
    "default_publish_paths",
    "format_enabled",
    "load_enabled_map",
    "open_extension_settings",
    "publish_quicklook_preferences",
    "set_format_enabled",
]


def _as_bool(raw: object, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        return bool(raw)
    if isinstance(raw, float):
        return bool(raw)
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off", ""):
            return False
    return default


def format_enabled(settings: QSettings, key: str) -> bool:
    """Return the saved toggle, or the catalog default when unset."""
    fmt = format_by_key(key)
    if fmt is None:
        return False
    full = SETTINGS_PREFIX + key
    if not settings.contains(full):
        return fmt.default_enabled
    return _as_bool(settings.value(full), fmt.default_enabled)


def set_format_enabled(settings: QSettings, key: str, enabled: bool) -> None:
    if format_by_key(key) is None:
        raise KeyError(key)
    settings.setValue(SETTINGS_PREFIX + key, bool(enabled))


def load_enabled_map(settings: QSettings) -> dict[str, bool]:
    flags = default_enabled_map()
    for fmt in QUICKLOOK_FORMATS:
        flags[fmt.key] = format_enabled(settings, fmt.key)
    return flags


def application_support_json() -> Path:
    """Fallback JSON under ~/Library/Application Support/EXR Converter/."""
    return Path.home() / "Library" / "Application Support" / APP_SUPPORT_FOLDER / JSON_FILENAME


def app_group_container_dir() -> Path | None:
    """App Group container for this process, or ``None`` when unsigned / not macOS."""
    if sys.platform != "darwin":
        return None
    path = _container_url(APP_GROUP_ID)
    if not path:
        return None
    return Path(path)


def default_publish_paths() -> list[Path]:
    """Destinations the running app can actually write."""
    paths: list[Path] = []
    group = app_group_container_dir()
    if group is not None:
        paths.append(group / JSON_FILENAME)
    paths.append(application_support_json())
    return paths


def publish_quicklook_preferences(
    settings: QSettings,
    destinations: Sequence[Path] | None = None,
) -> list[Path]:
    """Write the extension JSON. Failures are skipped so launch still succeeds.

    Returns the paths that were written.
    """
    payload = encode_preferences(load_enabled_map(settings))
    targets = list(destinations) if destinations is not None else default_publish_paths()
    written: list[Path] = []
    for path in targets:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            continue
        written.append(path)
    return written


# macOS 13–14 named this pane Login Items; macOS 15 uses Extensions Preferences.
# ``open`` does not report which pane existed, so the newer URL is the one we launch.
_EXTENSIONS_URL = (
    "x-apple.systempreferences:com.apple.ExtensionsPreferences"
    "?extensionPointIdentifier=com.apple.quicklook.preview"
)


def open_extension_settings() -> None:
    """Open System Settings where Quick Look extensions are enabled."""
    subprocess.Popen(
        ["open", _EXTENSIONS_URL],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def clear_quicklook_cache() -> None:
    """Drop Finder's Quick Look cache so a format toggle is visible immediately."""
    subprocess.run(
        ["qlmanage", "-r", "cache"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )


def _container_url(group_id: str) -> str | None:
    """``NSFileManager.containerURLForSecurityApplicationGroupIdentifier:``.

    Returns ``None`` when the process is not signed with the App Group
    entitlement. That is expected for ``uv run`` and ad-hoc bundles.
    """
    try:
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    except OSError:
        return None

    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.objc_msgSend.restype = ctypes.c_void_p
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    def sel(name: bytes) -> ctypes.c_void_p:
        return objc.sel_registerName(name)

    send2 = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(objc.objc_msgSend)
    send3 = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        objc.objc_msgSend
    )
    send_str = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(
        objc.objc_msgSend
    )
    send_utf8 = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)(
        objc.objc_msgSend
    )

    pool_cls = objc.objc_getClass(b"NSAutoreleasePool")
    pool = send2(send2(pool_cls, sel(b"alloc")), sel(b"init"))
    try:
        manager_cls = objc.objc_getClass(b"NSFileManager")
        manager = send2(manager_cls, sel(b"defaultManager"))
        nsstring = objc.objc_getClass(b"NSString")
        group = send_str(nsstring, sel(b"stringWithUTF8String:"), group_id.encode("utf-8"))
        url = send3(manager, sel(b"containerURLForSecurityApplicationGroupIdentifier:"), group)
        if not url:
            return None
        path = send2(url, sel(b"path"))
        if not path:
            return None
        raw = send_utf8(path, sel(b"UTF8String"))
        if not raw:
            return None
        return raw.decode("utf-8")
    finally:
        send2(pool, sel(b"drain"))
