"""The file types EXR Converter offers to open, and how each OS is told about them.

Registering is always an offer: the app appears under Open With (macOS
``LSHandlerRank = Alternate``, Windows ``OpenWithProgids``, a Linux
``MimeType`` entry) and never becomes anyone's default on its own. Only formats
that most systems have no good handler for are offered by default; formats other
apps own (photos, video players) stay opt-in.

One catalog feeds the macOS Info.plist, the Linux desktop entry and MIME XML, and
the Windows installer's checks, so they cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape


@dataclass(frozen=True)
class ViewerFileType:
    key: str
    label: str  # shown by the OS, e.g. "OpenEXR image"
    extensions: tuple[str, ...]  # without the dot, lower case
    mime: str  # Linux / freedesktop type
    uti: tuple[str, ...] = ()  # macOS content types; empty means match by extension
    mime_defined_by_us: bool = False  # True when shared-mime-info lacks the type
    offer_by_default: bool = True

    @property
    def dotted(self) -> tuple[str, ...]:
        return tuple(f".{e}" for e in self.extensions)


VIEWER_FILE_TYPES: tuple[ViewerFileType, ...] = (
    ViewerFileType(
        key="exr",
        label="OpenEXR image",
        extensions=("exr",),
        mime="image/x-exr",
        uti=("com.ilm.openexr-image", "public.exr"),
    ),
    ViewerFileType(
        key="dpx",
        label="DPX image",
        extensions=("dpx",),
        mime="image/x-dpx",
        uti=("com.vfxtools.dpx-image",),
        mime_defined_by_us=True,
    ),
    ViewerFileType(
        key="r3d",
        label="RED R3D clip",
        extensions=("r3d",),
        mime="video/x-red-r3d",
        mime_defined_by_us=True,
    ),
    ViewerFileType(
        key="braw",
        label="Blackmagic RAW clip",
        extensions=("braw",),
        mime="video/x-blackmagic-raw",
        mime_defined_by_us=True,
    ),
    # Opt-in: other apps own these, so they are never offered unless asked for.
    ViewerFileType(
        key="dng",
        label="CinemaDNG frame",
        extensions=("dng",),
        mime="image/x-adobe-dng",
        uti=("com.adobe.raw-image",),
        offer_by_default=False,
    ),
    ViewerFileType(
        key="tiff",
        label="TIFF image",
        extensions=("tif", "tiff"),
        mime="image/tiff",
        uti=("public.tiff",),
        offer_by_default=False,
    ),
    ViewerFileType(
        key="video",
        label="Video clip",
        extensions=("mov", "mp4", "mxf"),
        mime="video/quicktime",
        uti=("com.apple.quicktime-movie", "public.mpeg-4", "com.sony.mxf"),
        offer_by_default=False,
    ),
)


def offered_types() -> tuple[ViewerFileType, ...]:
    """The types registered without the user asking."""
    return tuple(t for t in VIEWER_FILE_TYPES if t.offer_by_default)


def offered_extensions() -> tuple[str, ...]:
    return tuple(e for t in offered_types() for e in t.extensions)


# -- macOS -----------------------------------------------------------------


def macos_document_types() -> list[dict[str, object]]:
    """``CFBundleDocumentTypes`` entries: viewer role, alternate rank, never the default."""
    entries: list[dict[str, object]] = []
    for t in offered_types():
        entry: dict[str, object] = {
            "CFBundleTypeName": t.label,
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
        }
        if t.uti:
            entry["LSItemContentTypes"] = list(t.uti)
        else:
            entry["CFBundleTypeExtensions"] = list(t.extensions)
        entries.append(entry)
    return entries


def merge_document_types(info: dict[str, object]) -> dict[str, object]:
    """Add the viewer's document types to a host Info.plist dict, replacing earlier ones."""
    merged = dict(info)
    ours = {str(d["CFBundleTypeName"]) for d in macos_document_types()}
    kept = [
        d
        for d in (info.get("CFBundleDocumentTypes") or [])  # type: ignore[union-attr]
        if isinstance(d, dict) and d.get("CFBundleTypeName") not in ours
    ]
    merged["CFBundleDocumentTypes"] = [*kept, *macos_document_types()]
    return merged


# -- Linux -----------------------------------------------------------------


def linux_mime_types() -> list[str]:
    """Values for the desktop entry's ``MimeType=`` line."""
    return [t.mime for t in offered_types()]


def linux_mime_xml() -> str:
    """shared-mime-info package for the types the system database lacks."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">',
    ]
    for t in VIEWER_FILE_TYPES:
        if not t.mime_defined_by_us:
            continue
        lines.append(f'  <mime-type type="{escape(t.mime)}">')
        lines.append(f"    <comment>{escape(t.label)}</comment>")
        for ext in t.extensions:
            lines.append(f'    <glob pattern="*.{escape(ext)}" weight="60"/>')
            lines.append(f'    <glob pattern="*.{escape(ext.upper())}" weight="60"/>')
        lines.append("  </mime-type>")
    lines.append("</mime-info>")
    return "\n".join(lines) + "\n"


# -- Windows ---------------------------------------------------------------


def windows_progid(ext: str) -> str:
    """The ProgID the installer registers for ``.ext``."""
    return f"EXRConverter.{ext.lower().lstrip('.')}"
