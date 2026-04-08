from __future__ import annotations

APP_ORG = "VFXTools"
APP_NAME = "EXRConverter"
APP_VERSION = "0.1.16"

DEFAULT_SRC_V2E = "Output - Rec.709"
DEFAULT_DST_V2E = "ACEScg"
DEFAULT_SRC_E2V = "scene_linear"
DEFAULT_DST_E2V = "Output - Rec.709"

COMMON_FPS = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60]

OCIO_SOURCE_ENV = "__env__"
OCIO_SOURCE_FILE = "__file__"

EXR_COMPRESSIONS = [
    "none",
    "rle",
    "zip",
    "zips",
    "piz",
    "pxr24",
    "b44",
    "b44a",
    "dwaa",
    "dwab",
]
DEFAULT_EXR_COMPRESSION = "dwaa"

DEFAULT_FRAME_PADDING = 4
DEFAULT_START_FRAME = 1001

SCALE_OPTIONS = [
    (1.0, "100%"),
    (0.75, "75%"),
    (0.5, "50%"),
    (0.25, "25%"),
]
DEFAULT_SCALE = 1.0

VIDEO_CODECS: list[tuple[str, str, str, str]] = [
    # (key, display_name, libav_codec, pix_fmt)
    ("prores", "Apple ProRes 422 HQ", "prores_ks", "yuv422p10le"),
    ("prores_4444", "Apple ProRes 4444", "prores_ks", "yuva444p10le"),
    ("h264", "H.264", "libx264", "yuv420p"),
    ("dnxhr_hq", "DNxHR HQ", "dnxhd", "yuv422p"),
    ("dnxhr_hqx", "DNxHR HQX (10-bit)", "dnxhd", "yuv422p10le"),
    ("ffv1", "FFV1 (lossless)", "ffv1", "yuv444p10le"),
]
DEFAULT_VIDEO_CODEC = "prores"
