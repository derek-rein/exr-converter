"""Optional media source plugins (generic, vendor-neutral hook).

The public application ships **no** plugins. Private builds may drop
additional camera-RAW / container decoders into the ``src.plugins`` package
and list them in an optional generated module ``src/plugins/_enabled.py``::

    ENABLED = ("src.plugins.example",)

Each listed module must expose ``get_source_plugin()`` returning an object
that satisfies :class:`SourcePlugin`. Developers can also set
``EXR_CONVERTER_SOURCE_PLUGINS`` to a comma-separated list of importable module
names. Plugin import or probe failures are logged and never break the app.

Dispatch order everywhere is: OS-sidecar rejection → source plugins → built-in
RAW bridges → PyAV. A plugin *claims* a path via :meth:`SourcePlugin.claims`,
which may sniff the file header (e.g. to tell one essence type in a shared
container such as ``.mxf`` from another) but must stay cheap.
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

    from .frame_source import IngestSource, PreviewDecoder

log = logging.getLogger(__name__)

ENV_PLUGINS = "EXR_CONVERTER_SOURCE_PLUGINS"
ENABLED_MODULE = "src.plugins._enabled"


@runtime_checkable
class SourcePlugin(Protocol):
    """Contract for an optional media source plugin."""

    #: Short display name (e.g. used in logs).
    name: str
    #: Lower-case file suffixes (with dot) this plugin may claim.
    suffixes: frozenset[str]

    def claims(self, path: str) -> bool:
        """True if this plugin decodes *path* (suffix check + optional cheap sniff)."""
        ...

    def is_available(self) -> bool:
        """True when the plugin's native runtime loaded and initialized."""
        ...

    def unavailable_reason(self) -> str: ...

    def codec_label(self, path: str) -> str:
        """Short codec label for browser rows (e.g. ``"RAW"``)."""
        ...

    def probe(self, path: str) -> tuple[int, int, float, int]:
        """``(width, height, fps, frame_count)`` of the full-size image."""
        ...

    def probe_metadata(self, path: str) -> dict[str, str]:
        """Human-readable metadata for the Inspect panel."""
        ...

    def colorspace_candidates(self, path: str) -> list[str]:
        """Ranked OCIO source color space names for decoded frames."""
        ...

    def preview_scale(self) -> float:
        """Decode ladder scale the preview decoder returns (1.0 = full)."""
        ...

    def open_preview(self, path: str, *, fps: float = 0.0) -> PreviewDecoder: ...

    def open_ingest(self, path: str, *, scale: float = 1.0) -> IngestSource: ...

    def thumbnail_rgb(self, path: str) -> np.ndarray | None:
        """Float32 display-referred RGB (0–1) for a browser thumbnail, or None."""
        ...

    def about_lines(self) -> list[str]:
        """Plain-text lines for Help → About (only shown when non-empty)."""
        ...

    def notices(self) -> list[tuple[str, str]]:
        """``(button title, plain-text notice)`` pairs shown from About."""
        ...


# Optional plugin attributes (looked up with ``getattr``):
#   sequence_key(path) -> str   frame-per-file formats: rows sharing a key are
#                               listed once in the video browser (display name).
# Optional ingest-source attribute:
#   output_stem: str            default EXR sequence name when none is given.


_lock = threading.Lock()
_plugins: list[Any] | None = None


def _module_names() -> list[str]:
    names: list[str] = []
    try:
        enabled = importlib.import_module(ENABLED_MODULE)
        names.extend(str(n) for n in getattr(enabled, "ENABLED", ()) if n)
    except ModuleNotFoundError:
        pass
    except Exception:
        log.warning("Failed to read %s", ENABLED_MODULE, exc_info=True)
    env = os.environ.get(ENV_PLUGINS, "").strip()
    if env:
        names.extend(n.strip() for n in env.split(",") if n.strip())
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def _load(names: Sequence[str]) -> list[Any]:
    loaded: list[Any] = []
    for name in names:
        try:
            mod = importlib.import_module(name)
            factory = getattr(mod, "get_source_plugin", None)
            if factory is None:
                log.warning("Source plugin %s has no get_source_plugin()", name)
                continue
            plugin = factory()
            if plugin is None:
                continue
            loaded.append(plugin)
            log.info("Loaded source plugin %s (%s)", getattr(plugin, "name", name), name)
        except Exception:
            log.warning("Failed to load source plugin %s", name, exc_info=True)
    return loaded


def plugins() -> list[Any]:
    """Return loaded source plugins (discovered once, thread-safe)."""
    global _plugins
    if _plugins is not None:
        return _plugins
    with _lock:
        if _plugins is None:
            _plugins = _load(_module_names())
    return _plugins


def reset_for_tests(loaded: Sequence[Any] | None = None) -> None:
    """Replace the plugin list (``None`` → rediscover on next call)."""
    global _plugins
    with _lock:
        _plugins = list(loaded) if loaded is not None else None


def plugin_suffixes() -> frozenset[str]:
    out: set[str] = set()
    for p in plugins():
        try:
            out.update(s.lower() for s in p.suffixes)
        except Exception:
            continue
    return frozenset(out)


def plugin_for_path(path: str | Path) -> Any | None:
    """First plugin that claims *path*, else None. Never raises."""
    found = plugins()
    if not found:
        return None
    p = str(path)
    if Path(p).name.startswith("._"):
        return None
    suffix = Path(p).suffix.lower()
    for plugin in found:
        try:
            if suffix in plugin.suffixes and plugin.claims(p):
                return plugin
        except Exception:
            log.debug("plugin %s claims() failed for %s", plugin, p, exc_info=True)
    return None


def require_available(plugin: Any) -> None:
    """Raise RuntimeError with the plugin's reason when its runtime is missing."""
    if not plugin.is_available():
        reason = ""
        try:
            reason = plugin.unavailable_reason()
        except Exception:
            pass
        raise RuntimeError(reason or f"{getattr(plugin, 'name', 'plugin')} support unavailable")


def about_lines() -> list[str]:
    lines: list[str] = []
    for p in plugins():
        try:
            lines.extend(str(x) for x in p.about_lines() if x)
        except Exception:
            log.debug("plugin about_lines failed", exc_info=True)
    return lines


def notices() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in plugins():
        try:
            out.extend((str(t), str(body)) for t, body in p.notices() if t and body)
        except Exception:
            log.debug("plugin notices failed", exc_info=True)
    return out
