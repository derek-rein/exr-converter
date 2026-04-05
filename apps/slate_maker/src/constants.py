from __future__ import annotations

# Overwritten at release time by CI (see .github/workflows/release-slate_maker.yml)
APP_VERSION = "dev"

RESOLUTIONS: dict[str, tuple[int, int]] = {
    # HD
    "720p  1280×720": (1280, 720),
    "HD  1920×1080": (1920, 1080),
    # 2K
    "2K  2048×1080": (2048, 1080),
    "2K Flat  1998×1080": (1998, 1080),
    "2K Scope  2048×858": (2048, 858),
    "2K Full  2048×1556": (2048, 1556),
    # UHD / 4K
    "UHD  3840×2160": (3840, 2160),
    "4K  4096×2160": (4096, 2160),
    "4K Flat  3996×2160": (3996, 2160),
    "4K Scope  4096×1716": (4096, 1716),
    "4K Full  4096×3112": (4096, 3112),
    # 8K
    "8K UHD  7680×4320": (7680, 4320),
    # Anamorphic
    "Ana 2K  2048×1536": (2048, 1536),
    "Ana 4K  4096×3072": (4096, 3072),
    # Square (texture / ACES)
    "Square 2K  2048×2048": (2048, 2048),
    "Square 4K  4096×4096": (4096, 4096),
}

COMMON_FPS: list[float] = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 60.0]

BIT_DEPTH_HALF = "half"
BIT_DEPTH_FLOAT = "float"
BIT_DEPTHS: list[str] = [BIT_DEPTH_HALF, BIT_DEPTH_FLOAT]

COLORSPACE_SRGB = "sRGB"
COLORSPACE_LINEAR = "Linear"
COLORSPACES: list[str] = [COLORSPACE_SRGB, COLORSPACE_LINEAR]

DEFAULT_FPS = 24.0
