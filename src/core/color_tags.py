"""H.273 colour tags (primaries / transfer / matrix) for encoded video.

Video files carry three codes that tell players how to read the pixels. The
Y'CbCr matrix is always BT.709 here because that is the matrix the encoders use
(:mod:`src.core.yuv`, ``native/exr_prores``). Primaries and transfer have to
match the *destination* colour space of the convert. When the destination has
no H.273 code (ACEScct, ACEScg, camera logs, …) both are written as 2,
"unspecified", rather than claiming BT.709.

The lookup uses the OCIO 2.5 ``interop_id`` of the destination colour space
(``srgb_rec709_display``, ``g24_rec709_display``, ``lin_rec709_scene``,
``pq_rec2020_display``, …), so it works for any config that sets them, and
falls back to a few well-known names for older configs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import PyOpenColorIO as OCIO

UNSPECIFIED = 2
MATRIX_BT709 = 1


@dataclass(frozen=True)
class ColorTags:
    primaries: int = UNSPECIFIED
    transfer: int = UNSPECIFIED
    matrix: int = MATRIX_BT709

    @property
    def is_unspecified(self) -> bool:
        return self.primaries == UNSPECIFIED and self.transfer == UNSPECIFIED


# interop_id primaries token → H.273 ColourPrimaries
_PRIMARIES: dict[str, int] = {
    "rec709": 1,
    "srgb": 1,
    "rec2020": 9,
    "p3d65": 12,
    "p3dci": 11,
}

# interop_id transfer token → H.273 TransferCharacteristics
_TRANSFER: dict[str, int] = {
    "lin": 8,
    "srgb": 13,
    # BT.1886 (gamma 2.4) displays are tagged with the BT.709 code, as Apple,
    # Resolve and FFmpeg do for Rec.709 video.
    "g24": 1,
    "bt1886": 1,
    "rec709": 1,
    "g22": 4,
    "pq": 16,
    "st2084": 16,
    "hlg": 18,
}

# Names without an interop_id (older configs) → interop-style id.
_NAME_HINTS: tuple[tuple[str, str], ...] = (
    (r"^(output - )?rec\.?709$|rec\.?1886.*709|^rec709$|gamma ?2\.4 rec\.?709", "g24_rec709"),
    (r"^(output - )?srgb$|^srgb - (display|texture)$|^srgb encoded rec\.?709", "srgb_rec709"),
    (r"^gamma ?2\.2 rec\.?709", "g22_rec709"),
    (
        r"^(linear|lin_srgb|lin_rec709|linear rec\.?709.*|utility - linear - (srgb|rec\.?709))$",
        "lin_rec709",
    ),
    (r"^(linear rec\.?2020|lin_rec2020|utility - linear - rec\.?2020)$", "lin_rec2020"),
    (r"^(linear p3-d65|lin_p3d65|utility - linear - p3-d65)$", "lin_p3d65"),
    (r"rec\.?2100.*pq|st[- ]?2084.*rec\.?2020", "pq_rec2020"),
    (r"rec\.?2100.*hlg", "hlg_rec2020"),
    (r"^display p3", "srgb_p3d65"),
)


def tags_from_interop_id(interop_id: str) -> ColorTags:
    """Tags from an OCIO interop id such as ``g24_rec709_display``."""
    ident = (interop_id or "").strip().lower()
    if ident.startswith("ocio:"):
        ident = ident[5:]
    parts = ident.split("_")
    if len(parts) < 2:
        return ColorTags()
    transfer = _TRANSFER.get(parts[0], UNSPECIFIED)
    primaries = _PRIMARIES.get(parts[1], UNSPECIFIED)
    if primaries == UNSPECIFIED:
        # A transfer code with unknown primaries (ACEScct AP1, camera logs)
        # would make players assume BT.709 primaries. Leave both unspecified.
        return ColorTags()
    return ColorTags(primaries=primaries, transfer=transfer)


def _interop_id(config: OCIO.Config, cs_name: str) -> str:
    try:
        cs = config.getColorSpace(cs_name)
    except Exception:
        return ""
    if cs is None:
        return ""
    getter = getattr(cs, "getInteropID", None)
    if getter is not None:
        try:
            ident = str(getter() or "")
        except Exception:
            ident = ""
        if ident:
            return ident
    # Configs without interop_id often list the same ids as aliases.
    try:
        names = [cs.getName(), *list(cs.getAliases() or [])]
    except Exception:
        names = [cs_name]
    for alias in names:
        tags = tags_from_interop_id(str(alias))
        if not tags.is_unspecified:
            return str(alias)
    for alias in names:
        lowered = str(alias).strip().lower()
        for pattern, ident in _NAME_HINTS:
            if re.search(pattern, lowered):
                return ident
    return ""


def destination_colorspace(config: OCIO.Config, working_space: str, dst_space: str) -> str:
    """The colour space the encoded pixels end up in (resolves display / view)."""
    from .ocio_utils import resolve_export_display_view

    pair = resolve_export_display_view(config, working_space, dst_space)
    if pair is None:
        return dst_space
    display, view = pair
    try:
        name = str(config.getDisplayViewColorSpaceName(display, view) or "")
    except Exception:
        name = ""
    if not name or name == "<USE_DISPLAY_NAME>":
        return display
    return name


def color_tags_for_destination(
    config: OCIO.Config | None, working_space: str, dst_space: str
) -> ColorTags:
    """H.273 tags for video encoded into *dst_space* (unspecified when unknown)."""
    if config is None or not dst_space:
        return ColorTags()
    cs_name = destination_colorspace(config, working_space, dst_space)
    ident = _interop_id(config, cs_name)
    if not ident:
        return ColorTags()
    return tags_from_interop_id(ident)


def apply_to_codec_context(codec_context, tags: ColorTags) -> None:
    """Set colour tags on a PyAV encoder context (best effort)."""
    for attr, value in (
        ("colorspace", tags.matrix),
        ("color_primaries", tags.primaries),
        ("color_trc", tags.transfer),
        ("color_range", 1),  # MPEG / limited
    ):
        try:
            setattr(codec_context, attr, value)
        except Exception:
            pass
