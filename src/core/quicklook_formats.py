"""macOS Quick Look format catalog shared by Preferences and the appex build.

The Swift extension in ``packaging/macos/quicklook`` mirrors ``key`` and
``default_enabled``. A unit test fails if those drift.

``QLSupportedContentTypes`` is fixed when the extension is built, so per-format
toggles cannot edit Info.plist. The app writes a JSON document the extension
reads at preview time and declines types the user turned off.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# macOS-style App Group. Team-prefixed ids are not the iOS ``group.`` form, so
# Developer ID signing does not need a provisioning profile for this entitlement.
# Keep in sync with the appex entitlements plist and the Swift reader.
APP_GROUP_ID = "83546T4BT8.com.vfxtools.exrconverter"
JSON_FILENAME = "quicklook.json"
APP_SUPPORT_FOLDER = "EXR Converter"
PREFS_VERSION = 1
SETTINGS_PREFIX = "quicklook/format/"

# Home-relative path the sandboxed appex is allowed to read when the App Group
# container is not available (unsigned ``uv run``, ad-hoc bundle).
FALLBACK_HOME_RELATIVE = f"/Library/Application Support/{APP_SUPPORT_FOLDER}/{JSON_FILENAME}"


@dataclass(frozen=True)
class QuickLookFormat:
    """One Finder type the extension may claim."""

    key: str
    label: str
    detail: str
    extensions: tuple[str, ...]
    content_types: tuple[str, ...]
    default_enabled: bool
    # True when the preview should tone-map scene-linear data (EXR, and TIFF
    # when the pixels are float / linear). DPX decides from its header.
    tone_map_linear: bool = False
    export_type: bool = False
    type_description: str = ""
    conforms_to: tuple[str, ...] = ("public.image",)


QUICKLOOK_FORMATS: tuple[QuickLookFormat, ...] = (
    QuickLookFormat(
        key="exr",
        label="OpenEXR (.exr)",
        detail="Scene-linear stills and numbered frames. On by default.",
        extensions=("exr",),
        content_types=("com.ilm.openexr-image", "public.exr"),
        default_enabled=True,
        tone_map_linear=True,
        type_description="OpenEXR image",
    ),
    QuickLookFormat(
        key="dpx",
        label="DPX (.dpx)",
        detail="Uncompressed 8-bit, 10-bit filled, and 16-bit RGB. On by default.",
        extensions=("dpx",),
        content_types=("com.vfxtools.dpx-image",),
        default_enabled=True,
        export_type=True,
        type_description="DPX image",
        conforms_to=("public.image", "public.data"),
    ),
    QuickLookFormat(
        key="tiff",
        label="TIFF (.tif, .tiff)",
        detail="Off by default so Finder keeps the system TIFF preview.",
        extensions=("tif", "tiff"),
        content_types=("public.tiff",),
        default_enabled=False,
        tone_map_linear=True,
    ),
    QuickLookFormat(
        key="png",
        label="PNG (.png)",
        detail="Off by default. Turn on for a sequence label on numbered frames.",
        extensions=("png",),
        content_types=("public.png",),
        default_enabled=False,
    ),
    QuickLookFormat(
        key="jpeg",
        label="JPEG (.jpg, .jpeg)",
        detail="Off by default. macOS already previews JPEG.",
        extensions=("jpg", "jpeg"),
        content_types=("public.jpeg",),
        default_enabled=False,
    ),
    QuickLookFormat(
        key="webp",
        label="WebP (.webp)",
        detail="Off by default. macOS already previews WebP.",
        extensions=("webp",),
        content_types=("public.webp", "org.webmproject.webp"),
        default_enabled=False,
    ),
    QuickLookFormat(
        key="dng",
        label="DNG / CinemaDNG (.dng)",
        detail="Off by default. Uses the system RAW decoder, not a camera SDK.",
        extensions=("dng",),
        content_types=("com.adobe.raw-image",),
        default_enabled=False,
    ),
)


def format_by_key(key: str) -> QuickLookFormat | None:
    for fmt in QUICKLOOK_FORMATS:
        if fmt.key == key:
            return fmt
    return None


def default_enabled_map() -> dict[str, bool]:
    return {fmt.key: fmt.default_enabled for fmt in QUICKLOOK_FORMATS}


def supported_content_types() -> list[str]:
    """UTI identifiers to list in ``QLSupportedContentTypes`` (stable order)."""
    out: list[str] = []
    seen: set[str] = set()
    for fmt in QUICKLOOK_FORMATS:
        for uti in fmt.content_types:
            if uti in seen:
                continue
            seen.add(uti)
            out.append(uti)
    return out


def _type_dict(fmt: QuickLookFormat) -> dict[str, object]:
    tags: dict[str, object] = {"public.filename-extension": list(fmt.extensions)}
    if fmt.key == "exr":
        tags["public.mime-type"] = ["image/x-exr", "image/aces"]
    return {
        "UTTypeIdentifier": fmt.content_types[0],
        "UTTypeDescription": fmt.type_description or fmt.label,
        "UTTypeConformsTo": list(fmt.conforms_to),
        "UTTypeTagSpecification": tags,
    }


def exported_type_declarations() -> list[dict[str, object]]:
    """Types the system does not already define (DPX)."""
    return [_type_dict(fmt) for fmt in QUICKLOOK_FORMATS if fmt.export_type]


def imported_type_declarations() -> list[dict[str, object]]:
    """OpenEXR, so ``.exr`` resolves even when the OS has no UTI for it."""
    return [_type_dict(fmt) for fmt in QUICKLOOK_FORMATS if fmt.key == "exr"]


def _merge_declarations(
    existing: object,
    additions: list[dict[str, object]],
) -> list[dict[str, object]]:
    order: list[str] = []
    by_id: dict[str, dict[str, object]] = {}
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, dict):
                continue
            ident = item.get("UTTypeIdentifier")
            if not isinstance(ident, str) or not ident:
                continue
            if ident not in by_id:
                order.append(ident)
            by_id[ident] = item
    for item in additions:
        ident = item["UTTypeIdentifier"]
        if not isinstance(ident, str):
            continue
        if ident not in by_id:
            order.append(ident)
        by_id[ident] = item
    return [by_id[ident] for ident in order]


def merge_type_declarations(info: dict[str, object]) -> dict[str, object]:
    """Add DPX / OpenEXR type declarations to a host or appex Info.plist dict."""
    merged = dict(info)
    merged["UTExportedTypeDeclarations"] = _merge_declarations(
        info.get("UTExportedTypeDeclarations"),
        exported_type_declarations(),
    )
    merged["UTImportedTypeDeclarations"] = _merge_declarations(
        info.get("UTImportedTypeDeclarations"),
        imported_type_declarations(),
    )
    return merged


def encode_preferences(enabled: dict[str, bool]) -> str:
    """JSON document the extension reads. Unknown keys are dropped."""
    flags = default_enabled_map()
    for fmt in QUICKLOOK_FORMATS:
        if fmt.key in enabled:
            flags[fmt.key] = bool(enabled[fmt.key])
    payload = {"version": PREFS_VERSION, "formats": flags}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def decode_preferences(text: str) -> dict[str, bool] | None:
    """Parse extension JSON. ``None`` means the document is unusable."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("formats")
    if not isinstance(raw, dict):
        return None
    flags = default_enabled_map()
    for fmt in QUICKLOOK_FORMATS:
        if fmt.key not in raw:
            continue
        value = raw[fmt.key]
        if isinstance(value, bool):
            flags[fmt.key] = value
    return flags
