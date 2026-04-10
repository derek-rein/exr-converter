from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

import PyOpenColorIO as OCIO

from .constants import OCIO_SOURCE_ENV, OCIO_SOURCE_FILE


def list_builtin_configs() -> list[tuple[str, str, bool]]:
    """Return [(internal_name, display_label, is_recommended), ...]."""
    reg = OCIO.BuiltinConfigRegistry()
    results = []
    for entry in reg.getBuiltinConfigs():
        name, label = entry[0], entry[1]
        recommended = entry[2] if len(entry) > 2 else False
        results.append((name, label, recommended))
    return results


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
    """
    if not name:
        return ""
    try:
        cs = config.getColorSpace(name)
        if cs is not None:
            return cs.getName()
    except Exception:
        pass
    return ""


def make_cpu_processor(config: OCIO.Config, src: str, dst: str) -> OCIO.CPUProcessor:
    return config.getProcessor(src, dst).getDefaultCPUProcessor()


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


def config_source_info(source_key: str, file_path: str = "") -> tuple[str, str]:
    """Return (config_source, config_path) suitable for pickling to worker processes.

    *config_source* is either a builtin config name or an empty string.
    *config_path* is a file path when the source is a file or $OCIO env.
    """
    if source_key == OCIO_SOURCE_FILE:
        return ("", file_path)
    if source_key == OCIO_SOURCE_ENV:
        env = os.environ.get("OCIO", "")
        return ("", env)
    return (source_key, "")
