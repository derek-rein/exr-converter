from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import PyOpenColorIO as OCIO

from .constants import (
    BUNDLED_ACES_STUDIO_KEY,
    OCIO_SOURCE_BUNDLED,
    OCIO_SOURCE_ENV,
    OCIO_SOURCE_FILE,
)

if TYPE_CHECKING:
    import numpy as np


def list_builtin_configs() -> list[tuple[str, str, bool]]:
    """Return [(internal_name, display_label, is_recommended), ...]."""
    reg = OCIO.BuiltinConfigRegistry()
    results = []
    for entry in reg.getBuiltinConfigs():
        name, label = entry[0], entry[1]
        recommended = entry[2] if len(entry) > 2 else False
        results.append((name, label, recommended))
    return results


def get_bundled_aces_studio_path() -> Path | None:
    """Locate the bundled 'super awesome' ACES Studio config (v4 / ACES 2.0).

    This is the official AcademySoftwareFoundation/OpenColorIO-Config-ACES
    studio config. It is a single small .ocio file (uses OCIO built-in
    transforms) containing a wide variety of camera input transforms/IDTs
    (ARRI Alexa, RED, Sony Venice, Canon, DJI, etc.) plus modern ACES
    Output Transforms. It is legally redistributable (BSD-3-Clause).

    Tries several locations to work in:
    - source tree dev runs
    - Nuitka standalone / onefile bundles
    - macOS .app bundles
    """
    filename = "aces-studio-v4.ocio"
    rel_path = Path("resources") / "ocio" / filename

    # 1. Direct relative to CWD (most dev runs and some bundles)
    cand = Path.cwd() / rel_path
    if cand.is_file():
        return cand

    # 2. Walk up from this module file (handles running from src/ or installed layouts)
    here = Path(__file__).resolve()
    for _ in range(6):
        cand = here.parent / rel_path
        if cand.is_file():
            return cand
        if here.parent == here:
            break
        here = here.parent

    # 3. Nuitka / frozen executable layouts
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        for extra in ("", "Contents/Resources", "resources", "."):
            p = base / extra / rel_path if extra else base / rel_path
            if p.is_file():
                return p
        # macOS app bundle Resources next to MacOS/
        macos_dir = base if base.name == "MacOS" else (base / "Contents" / "MacOS")
        if macos_dir.exists():
            res_dir = macos_dir.parent / "Resources"
            p = res_dir / rel_path
            if p.is_file():
                return p

    # 4. As package data (if someone moves the .ocio under the package)
    try:
        import importlib.resources as ir

        # Try several possible package locations
        for pkg in ("src.ocio_configs", "ocio_configs", "src"):
            try:
                if hasattr(ir, "files"):
                    root = ir.files(pkg)
                    if pkg.endswith("configs"):
                        p = root / filename
                    else:
                        p = root / "ocio_configs" / filename
                    if p.is_file():
                        return Path(str(p))
            except Exception:
                continue
    except Exception:
        pass

    return None


def list_app_configs() -> list[tuple[str, str, bool]]:
    """App-provided configs (our bundled super config first)."""
    p = get_bundled_aces_studio_path()
    if p:
        label = "ACES Studio Config (v4 • ACES 2.0 • cameras)"
        return [(BUNDLED_ACES_STUDIO_KEY, label, True)]
    # Fallback: if for some reason the file isn't there, surface nothing extra
    return []


def resolve_ocio_config(source: str, builtin_name: str = "", file_path: str = "") -> OCIO.Config:
    if source == OCIO_SOURCE_ENV:
        env = os.environ.get("OCIO", "")
        if env:
            p = Path(env).expanduser()
            if p.is_file():
                return OCIO.Config.CreateFromFile(str(p))
        raise RuntimeError("$OCIO environment variable is not set or not a valid file.")
    if source == OCIO_SOURCE_FILE:
        if not file_path:
            raise RuntimeError("No config file path specified.")
        p = Path(file_path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"OCIO config not found: {file_path}")
        return OCIO.Config.CreateFromFile(str(p))
    if source == OCIO_SOURCE_BUNDLED or source == BUNDLED_ACES_STUDIO_KEY:
        p = get_bundled_aces_studio_path()
        if p and p.is_file():
            try:
                return OCIO.Config.CreateFromFile(str(p))
            except Exception:
                pass  # version too old or corrupt; fall through to library fallback
        # Graceful fallback to the best available library studio config (the "awesome cameras" one)
        for candidate in (
            "studio-config-v2.2.0_aces-v1.3_ocio-v2.4",
            "studio-config-v2.1.0_aces-v1.3_ocio-v2.3",
            "studio-config-latest",
        ):
            try:
                return OCIO.Config.CreateFromBuiltinConfig(candidate)
            except Exception:
                continue
        raise RuntimeError(
            "Bundled ACES Studio config not found (requires OCIO 2.5+ at runtime) "
            "and no library fallback available."
        )
    return OCIO.Config.CreateFromBuiltinConfig(source or builtin_name)


def resolve_ocio_for_cli(ocio_arg: str | None) -> OCIO.Config:
    if ocio_arg:
        p = Path(ocio_arg).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"OCIO config not found: {ocio_arg}")
        return OCIO.Config.CreateFromFile(str(p))
    env = os.environ.get("OCIO")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return OCIO.Config.CreateFromFile(str(p))
    # Prefer our bundled super config (rich cameras) when available
    app_cfgs = list_app_configs()
    if app_cfgs:
        key = app_cfgs[0][0]
        try:
            return resolve_ocio_config(key)
        except Exception:
            pass
    # Otherwise best library builtin
    builtins = list_builtin_configs()
    recommended = [b for b in builtins if b[2]]
    name = recommended[0][0] if recommended else builtins[-1][0]
    return OCIO.Config.CreateFromBuiltinConfig(name)


def color_space_families(config: OCIO.Config) -> dict[str, list[str]]:
    families: dict[str, list[str]] = defaultdict(list)
    for name in config.getColorSpaceNames():
        cs = config.getColorSpace(name)
        fam = cs.getFamily() or "Other"
        families[fam].append(name)
    return dict(families)


def resolve_alias(config: OCIO.Config, name: str) -> str:
    """Return the canonical color-space name for *name*, checking aliases.

    OCIO 2.x color spaces can have aliases (e.g. "ACEScg" might be aliased
    as "ACES - ACEScg" or "acescg").  ``config.getColorSpace(name)`` already
    resolves aliases, so if it returns a valid object the canonical name is
    ``cs.getName()``.

    We also apply a small set of app-level common-name fallbacks for popular
    camera logs (including Apple Log for iPhone cinematic footage) so users
    can type intuitive names like "apple log", "iphone log", "prores log", etc.
    """
    if not name:
        return ""
    # App-level convenience aliases for common camera encodings.
    # These help even if the active config uses slightly different naming.
    extra_aliases = {
        # Apple / iPhone
        "apple log": "Apple Log",
        "applelog": "Apple Log",
        "apple_log": "Apple Log",
        "iphone log": "Apple Log",
        "iphone": "Apple Log",
        "prores log": "Apple Log",
        "alog": "Apple Log",
        # Other popular shortcuts (extend as needed)
        "arri logc": "ARRI LogC3 (EI800)",
        "arri": "ARRI LogC3 (EI800)",
        "red": "Log3G10 REDWideGamutRGB",
        "red log3g10": "Log3G10 REDWideGamutRGB",
        "sony": "S-Log3 SGamut3.Cine",
        "venice": "S-Log3 SGamut3.Cine",
    }
    lowered = name.strip().lower()
    if lowered in extra_aliases:
        candidate = extra_aliases[lowered]
        try:
            cs = config.getColorSpace(candidate)
            if cs is not None:
                return cs.getName()
        except Exception:
            pass
        # If the preferred candidate isn't present, fall through to normal lookup
    try:
        cs = config.getColorSpace(name)
        if cs is not None:
            return cs.getName()
    except Exception:
        pass
    return ""


def make_cpu_processor(config: OCIO.Config, src: str, dst: str) -> OCIO.CPUProcessor:
    return config.getProcessor(src, dst).getDefaultCPUProcessor()


def get_working_space(config: OCIO.Config) -> str:
    """Return the canonical name of the OCIO ``scene_linear`` role.

    All compositing inside the conversion pipeline happens in this
    scene-linear "working" colorspace.  Falls back to a few common
    alternate role / colorspace names so this works on stock ACES,
    Studio, and CG-Config builds.
    """
    candidates = (
        OCIO.ROLE_SCENE_LINEAR,
        "scene_linear",
        "compositing_linear",
        "ACES - ACEScg",
        "ACEScg",
        "Linear Rec.709 (sRGB)",
        "lin_rec709",
    )
    for name in candidates:
        try:
            cs = config.getColorSpace(name)
            if cs is not None:
                return cs.getName()
        except Exception:
            continue
    raise RuntimeError("Could not resolve a scene-linear working colorspace from the OCIO config.")


def get_overlay_authoring_space(config: OCIO.Config) -> str:
    """Return the colorspace overlays (slate / burnin / watermark) are painted in.

    Overlays are authored in display-encoded sRGB (Qt's standard 8-bit
    rendering), so this resolves to whatever name the active config uses
    for sRGB / sRGB-Texture.
    """
    candidates = (
        "sRGB - Texture",
        "sRGB Texture",
        "Utility - sRGB - Texture",
        "sRGB",
        "Output - sRGB",
        "srgb",
    )
    for name in candidates:
        try:
            cs = config.getColorSpace(name)
            if cs is not None:
                return cs.getName()
        except Exception:
            continue
    return get_working_space(config)


def linearize_overlay(
    config: OCIO.Config,
    overlay_u8_rgba: np.ndarray,
    src_space: str = "",
    working_space: str = "",
) -> np.ndarray:
    """Convert an sRGB-encoded RGBA overlay (uint8) into working-space float32.

    Alpha is preserved unchanged (the OCIO transform only touches RGB).
    """
    import numpy as np

    if not src_space:
        src_space = get_overlay_authoring_space(config)
    if not working_space:
        working_space = get_working_space(config)

    rgb = overlay_u8_rgba[..., :3].astype(np.float32) / 255.0
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    cpu = make_cpu_processor(config, src_space, working_space)
    cpu.apply(OCIO.PackedImageDesc(rgb, w, h, 3))

    out = np.empty(overlay_u8_rgba.shape, dtype=np.float32)
    out[..., :3] = rgb
    out[..., 3] = overlay_u8_rgba[..., 3].astype(np.float32) / 255.0
    return out


def list_displays(config: OCIO.Config) -> list[str]:
    """Return the display names defined in *config*."""
    return list(config.getDisplays())


def list_views(config: OCIO.Config, display: str) -> list[str]:
    """Return the view names available for *display*."""
    return list(config.getViews(display))


def make_display_processor(
    config: OCIO.Config,
    src_space: str,
    display: str,
    view: str,
    exposure: float = 0.0,
    gamma: float = 1.0,
) -> OCIO.CPUProcessor:
    """Build a CPUProcessor for OCIO DisplayViewTransform with exposure/gamma.

    The resulting processor converts from *src_space* through the given
    display/view pair, with exposure (in stops) and gamma applied via
    ``ExposureContrastTransform``.
    """
    group = OCIO.GroupTransform()

    if exposure != 0.0 or gamma != 1.0:
        ec = OCIO.ExposureContrastTransform()
        ec.setExposure(exposure)
        ec.setGamma(gamma)
        ec.setPivot(0.18)
        group.appendTransform(ec)

    dvt = OCIO.DisplayViewTransform()
    dvt.setSrc(src_space)
    dvt.setDisplay(display)
    dvt.setView(view)
    group.appendTransform(dvt)

    return config.getProcessor(group).getDefaultCPUProcessor()


def make_viewer_display_processor(
    config: OCIO.Config,
    working_space: str,
    display: str,
    view: str,
) -> tuple[OCIO.CPUProcessor | None, object | None, object | None]:
    """Build a CPUProcessor for working → display/view with a *dynamic* ExposureContrastTransform.

    The returned EC transform is configured for live viewer controls (gain/gamma).
    Callers can retrieve the dynamic properties and mutate them cheaply without
    rebuilding the processor — this is the pattern used by RV, xStudio, and other
    professional OCIO viewers for responsive exposure/gain/gamma adjustments.

    Returns (cpu_proc, exposure_prop, gamma_prop) or (None, None, None) on failure.
    The props are obtained via DYNAMIC_PROPERTY_EXPOSURE / DYNAMIC_PROPERTY_GAMMA.
    """
    group = OCIO.GroupTransform()

    # Viewer adjustment transform — placed *before* the display curve.
    # LINEAR style is appropriate when working_space is scene-linear.
    ec = OCIO.ExposureContrastTransform()
    ec.setStyle(OCIO.EXPOSURE_CONTRAST_STYLE_LINEAR)
    ec.setExposure(0.0)
    ec.setGamma(1.0)
    ec.setPivot(0.18)
    ec.makeDynamic()
    group.appendTransform(ec)

    dvt = OCIO.DisplayViewTransform()
    dvt.setSrc(working_space)
    dvt.setDisplay(display)
    dvt.setView(view)
    group.appendTransform(dvt)

    try:
        proc = config.getProcessor(group).getDefaultCPUProcessor()
        exp_prop = proc.getDynamicProperty(OCIO.DYNAMIC_PROPERTY_EXPOSURE)
        gamma_prop = proc.getDynamicProperty(OCIO.DYNAMIC_PROPERTY_GAMMA)
        return proc, exp_prop, gamma_prop
    except Exception:
        return None, None, None


def config_source_info(source_key: str, file_path: str = "") -> tuple[str, str]:
    """Return (config_source, config_path) suitable for pickling to worker processes.

    *config_source* is either a builtin config name or an empty string.
    *config_path* is a file path when the source is a file or $OCIO env.

    For our bundled ACES studio we resolve to the real on-disk path so that
    worker processes (which do not share our Python import context) can simply
    load it via CreateFromFile — exactly like a user custom config.
    """
    if source_key == OCIO_SOURCE_FILE:
        return ("", file_path)
    if source_key == OCIO_SOURCE_ENV:
        env = os.environ.get("OCIO", "")
        return ("", env)
    if source_key == OCIO_SOURCE_BUNDLED or source_key == BUNDLED_ACES_STUDIO_KEY:
        p = get_bundled_aces_studio_path()
        if p and p.is_file():
            return ("", str(p))
        # If we couldn't find it, fall back to a library builtin name
        return ("studio-config-v2.2.0_aces-v1.3_ocio-v2.4", "")
    return (source_key, "")
