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
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import PyOpenColorIO as OCIO
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
from .quicklook_lut import (
    INPUTS_FILENAME,
    LUT_BUNDLE_FILENAME,
    LUT_FILENAME,
    bake_input_table,
    bake_view_lut,
    bake_view_lut_bundle,
    write_view_lut,
)

_log = logging.getLogger(__name__)
_lut_lock = threading.Lock()
_lut_generation = 0

__all__ = [
    "EXTENSION_BUNDLE_IDS",
    "QUICKLOOK_FORMATS",
    "QuickLookFormat",
    "apply_default_install_state",
    "extensions_enabled_in_system",
    "application_support_json",
    "app_group_container_dir",
    "clear_quicklook_cache",
    "default_publish_paths",
    "format_enabled",
    "load_enabled_map",
    "open_extension_settings",
    "publish_quicklook_preferences",
    "quicklook_installed",
    "schedule_quicklook_view_lut",
    "set_format_enabled",
    "set_quicklook_installed",
    "view_lut_destinations",
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


def view_lut_destinations() -> list[Path]:
    """Where the running app writes ``view.lut`` (group container, then fallback)."""
    paths: list[Path] = []
    group = app_group_container_dir()
    if group is not None:
        paths.append(group / LUT_FILENAME)
    paths.append(application_support_json().with_name(LUT_FILENAME))
    return paths


def _sibling_paths(name: str) -> list[Path]:
    return [path.with_name(name) for path in view_lut_destinations()]


def schedule_quicklook_view_lut(config: object) -> None:
    """Bake the active config's views for Quick Look on a background thread.

    Writes ``view.lut`` (default view, used by Finder thumbnails), ``views.lut``
    (every SDR view of the sRGB and Display P3 displays, for the preview's view
    menu) and ``inputs.json`` (input color spaces the preview can detect).

    No-op off macOS and while pytest is running, so a GUI test does not write
    the runner's Application Support folder. A newer OCIO reload supersedes an
    in-flight bake.
    """
    if sys.platform != "darwin" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if not isinstance(config, OCIO.Config):
        return
    global _lut_generation
    with _lut_lock:
        _lut_generation += 1
        token = _lut_generation

    def run() -> None:
        try:
            data = bake_view_lut(config)
        except Exception:
            _log.warning("Could not bake the Quick Look view LUT", exc_info=True)
            return
        try:
            bundle: bytes | None = bake_view_lut_bundle(config)
        except Exception:
            _log.warning("Could not bake the Quick Look view menu", exc_info=True)
            bundle = None
        try:
            inputs: bytes | None = bake_input_table(config).encode("utf-8")
        except Exception:
            _log.warning("Could not bake the Quick Look input table", exc_info=True)
            inputs = None
        with _lut_lock:
            if token != _lut_generation:
                return
        write_view_lut(data, view_lut_destinations())
        if bundle is not None:
            write_view_lut(bundle, _sibling_paths(LUT_BUNDLE_FILENAME))
        if inputs is not None:
            write_view_lut(inputs, _sibling_paths(INPUTS_FILENAME))

    threading.Thread(target=run, name="quicklook-view-lut", daemon=True).start()


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


# Bundle ids of the embedded appexes (scripts/build_quicklook_appex.py).
EXTENSION_BUNDLE_IDS: tuple[str, ...] = (
    "com.vfxtools.exrconverter.quicklook-preview",
    "com.vfxtools.exrconverter.quicklook-thumbnail",
)
_INSTALLED_KEY = "quicklook/installed"


def quicklook_installed(settings: QSettings) -> bool:
    """Whether the user turned the extensions on. Off until they opt in."""
    return _as_bool(settings.value(_INSTALLED_KEY, False), False)


def _pluginkit_elect(enabled: bool) -> bool:
    """``pluginkit -e use|ignore`` for both appexes. True when every call worked."""
    if sys.platform != "darwin" or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    election = "use" if enabled else "ignore"
    ok = True
    for bundle_id in EXTENSION_BUNDLE_IDS:
        try:
            result = subprocess.run(
                ["pluginkit", "-e", election, "-i", bundle_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            ok = False
            continue
        ok = ok and result.returncode == 0
    return ok


def set_quicklook_installed(settings: QSettings, enabled: bool) -> bool:
    """Save the choice and elect the appexes on or off with ``pluginkit``.

    Returns whether ``pluginkit`` accepted the change. A source checkout has no
    registered appex, so it returns False there; the setting is still saved.
    """
    settings.setValue(_INSTALLED_KEY, bool(enabled))
    return _pluginkit_elect(bool(enabled))


def apply_default_install_state(settings: QSettings) -> None:
    """Keep the extensions off until the user turns them on.

    macOS enables an embedded Quick Look appex as soon as the app is in
    /Applications. On each launch this re-applies the saved choice (off by
    default), so a fresh install or an update that re-registers the appex
    does not take over Finder previews. Runs on a background thread so
    ``pluginkit`` does not delay the window.
    """
    if sys.platform != "darwin" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    enabled = quicklook_installed(settings)
    threading.Thread(
        target=_pluginkit_elect, args=(enabled,), name="quicklook-elect", daemon=True
    ).start()


def extensions_enabled_in_system() -> bool | None:
    """``True`` when pluginkit lists both appexes as enabled, ``None`` if unknown."""
    if sys.platform != "darwin":
        return None
    states: list[bool] = []
    for bundle_id in EXTENSION_BUNDLE_IDS:
        try:
            out = subprocess.run(
                ["pluginkit", "-m", "-i", bundle_id],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        line = next((ln for ln in out.splitlines() if bundle_id in ln), "")
        if not line:
            return None
        states.append(line.lstrip().startswith("+"))
    return all(states)


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


def _objc_msg(objc: ctypes.CDLL, restype: type, *argtypes: type):
    """``objc_msgSend`` looked up with this exact signature.

    Setting ``argtypes`` on the shared library symbol and then casting that
    function keeps the two-argument call frame. A string argument then arrives
    as NULL, ``NSString stringWithUTF8String:`` raises, and the process aborts
    before the window opens.
    """
    return ctypes.CFUNCTYPE(restype, *argtypes)(("objc_msgSend", objc))


def _container_url(group_id: str) -> str | None:
    """``NSFileManager.containerURLForSecurityApplicationGroupIdentifier:``.

    Returns ``None`` when the process is not signed with the App Group
    entitlement. That is expected for ``uv run`` and ad-hoc bundles.
    """
    if not group_id:
        return None
    try:
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    except OSError:
        return None

    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    def sel(name: bytes) -> int:
        value = objc.sel_registerName(name)
        if not value:
            raise OSError(f"unknown selector {name!r}")
        return value

    send2 = _objc_msg(objc, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    send3 = _objc_msg(objc, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    send_str = _objc_msg(objc, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)
    send_utf8 = _objc_msg(objc, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)

    pool_cls = objc.objc_getClass(b"NSAutoreleasePool")
    if not pool_cls:
        return None
    pool = send2(send2(pool_cls, sel(b"alloc")), sel(b"init"))
    try:
        manager_cls = objc.objc_getClass(b"NSFileManager")
        nsstring = objc.objc_getClass(b"NSString")
        if not manager_cls or not nsstring:
            return None
        manager = send2(manager_cls, sel(b"defaultManager"))
        # Keep the buffer alive for the call. A temporary ``bytes`` can be
        # collected before libffi reads it inside a frozen binary.
        encoded = ctypes.create_string_buffer(group_id.encode("utf-8"))
        group = send_str(nsstring, sel(b"stringWithUTF8String:"), encoded)
        if not group:
            return None
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
    except (OSError, UnicodeError):
        return None
    finally:
        if pool:
            send2(pool, sel(b"drain"))
