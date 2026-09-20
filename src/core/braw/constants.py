"""BRAW decode modes, metadata keys, and end-user redistributable notice."""

from __future__ import annotations

import sys

# Extension handled by the Blackmagic RAW SDK (not PyAV).
BRAW_SUFFIXES: frozenset[str] = frozenset({".braw"})

# Official OpenEXRTranscode default: Linear gamma + ACES AP0.
# Bundled ACES Studio v4 names this space ACES2065-1 (aliases include lin_ap0).
BRAW_SRC_COLORSPACE_CANDIDATES: tuple[str, ...] = (
    "ACES2065-1",
    "ACES - ACES2065-1",
    "lin_ap0",
    "lin_ap0_scene",
    "aces2065_1",
)

# Decode ladder (matches native/braw/braw_bridge.h).
DECODE_FULL = 0
DECODE_HALF = 1
DECODE_QUARTER = 2
DECODE_EIGHTH = 3

DECODE_PREVIEW = DECODE_HALF
DECODE_THUMBNAIL = DECODE_EIGHTH

DECODE_MODE_SCALE: dict[int, float] = {
    DECODE_FULL: 1.0,
    DECODE_HALF: 0.5,
    DECODE_QUARTER: 0.25,
    DECODE_EIGHTH: 0.125,
}

# Clip-level metadata keys we copy into EXR attributes when present.
CLIP_META_KEYS: tuple[str, ...] = (
    "camera_type",
    "camera_id",
    "filename",
    "firmware",
    "date_recorded",
    "iso",
    "shutter_value",
    "shutter_angle",
    "white_balance_kelvin",
    "white_balance_tint",
    "exposure",
    "analog_gain",
    "focal_length",
    "aperture",
    "lens_type",
    "crop_origin",
    "sensor_area_wh",
)

BMD_REDISTRIBUTABLE_NOTICE = """\
Blackmagic RAW decoding uses proprietary software from Blackmagic Design
(the "Blackmagic RAW SDK"). The SDK is free to download from Blackmagic
Design but is not open source.

When this application includes Blackmagic RAW runtime libraries, those
libraries remain the property of Blackmagic Design:

* You may use BRAW functionality solely as integrated in this application
  to decode .braw media for your own projects.
* You may not reverse engineer, decompile, or disassemble the Blackmagic
  libraries or attempt to derive their source or file formats.
* You may not redistribute the SDK headers, samples, documentation, or
  the SDK package as a whole. Obtain the SDK from Blackmagic Design.
* Runtime API libraries (libBlackmagicRawAPI and related decoder /
  instruction-set binaries) may be redistributed in object form with
  this application when the license that shipped with your SDK allows
  it (typical Developer License 1.1(d) — verify your copy).
* THE BLACKMAGIC LIBRARIES AND RELATED MATERIALS ARE PROVIDED "AS IS"
  WITHOUT WARRANTY OF ANY KIND.

Obtain the current license from the official Blackmagic RAW SDK package:
https://www.blackmagicdesign.com/developer/products/braw
"""


def decode_mode_for_scale(scale: float) -> int:
    """Map a convert *scale* factor to a native BRAW decode mode."""
    if scale >= 0.99:
        return DECODE_FULL
    if scale >= 0.49:
        return DECODE_HALF
    if scale >= 0.24:
        return DECODE_QUARTER
    return DECODE_EIGHTH


def scale_for_decode_mode(mode: int) -> float:
    """Linear resolution scale for *mode* (1.0 = full)."""
    return DECODE_MODE_SCALE.get(int(mode), 1.0)


def preferred_decoder_kinds() -> tuple[str, ...]:
    """Pipeline try-order (matches ``native/braw/braw_bridge.cpp``)."""
    if sys.platform == "darwin":
        return ("metal", "opencl", "cpu")
    return ("cuda", "opencl", "cpu")
