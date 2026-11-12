"""Optical squeeze and pixel aspect.

OpenEXR ``pixelAspectRatio`` is the width of one pixel divided by its height
(the same number Nuke shows). ``2.0`` means each stored pixel is twice as
wide on screen, so a squeezed anamorphic plate displays unsqueezed. The
stored raster is left squeezed unless a caller resamples on its own.

Broadcast sample aspect (ITU / SD tables, for example 720x576 with a 16:9
display aspect) is not a cinema lens squeeze. It is kept on video tags and
is not copied onto ``pixelAspectRatio``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

Kind = Literal["square", "lens", "broadcast", "override"]

SQUARE_FACTOR = 1.0
ATTR_PIXEL_ASPECT = "pixelAspectRatio"
ATTR_OPTICAL_SQUEEZE = "exrconverter:opticalSqueeze"
ATTR_SAMPLE_ASPECT = "exrconverter:sampleAspectRatio"

# Keys whose values are an optical squeeze (width/height of a pixel), not a
# display aspect such as 16:9. Matched on the normalised key and on the
# suffix after the last ":" so camera metadata copied into EXR attributes
# still hits.
_OPTICAL_KEYS = frozenset(
    {
        "pixelaspectratio",
        "pixel_aspect_ratio",
        "pixel_aspect",
        "pixelaspect",
        "optical_squeeze",
        "opticalsqueeze",
        "lens_squeeze",
        "lens_squeeze_factor",
        "lenssqueezefactor",
        "squeeze",
        "squeeze_factor",
        "squeezefactor",
        "anamorphic",
        "anamorphic_ratio",
        "anamorphic_factor",
        "anamorphicratio",
        "pasp",
        "default_scale",
        "defaultscale",
    }
)

# Known cinema lens squeezes, closest match within _LENS_TOL.
_LENS_LABELS: tuple[tuple[float, str], ...] = (
    (1.25, "1.25x anamorphic"),
    (1.3, "1.3x anamorphic"),
    (1.3333333333, "1.33x anamorphic"),
    (1.5, "1.5x anamorphic"),
    (1.65, "1.65x anamorphic"),
    (1.8, "1.8x anamorphic"),
    (2.0, "2x anamorphic"),
)
_LENS_TOL = 0.015

# ITU / MPEG sample-aspect ratios that mean "broadcast pixels", not a lens.
# HD broadcast anamorphic rasters (1440x1080 with 4:3) are included so a
# 1.33 sample aspect there is not treated as a 1.33x lens.
_BROADCAST_SAR: dict[tuple[int, int], tuple[Fraction, ...]] = {
    (720, 576): (Fraction(12, 11), Fraction(16, 11), Fraction(16, 15), Fraction(64, 45)),
    (704, 576): (Fraction(12, 11), Fraction(16, 11)),
    (720, 480): (Fraction(10, 11), Fraction(40, 33), Fraction(8, 9), Fraction(32, 27)),
    (720, 486): (Fraction(10, 11), Fraction(40, 33), Fraction(8, 9)),
    (704, 480): (Fraction(10, 11), Fraction(40, 33)),
    (352, 480): (Fraction(10, 11), Fraction(40, 33)),
    (352, 576): (Fraction(12, 11), Fraction(16, 11)),
    (352, 240): (Fraction(10, 11), Fraction(40, 33)),
    (352, 288): (Fraction(12, 11), Fraction(16, 11)),
    (1440, 1080): (Fraction(4, 3),),
    (960, 720): (Fraction(4, 3),),
    (1280, 1080): (Fraction(3, 2),),
}
_SD_WIDTHS = frozenset({352, 704, 720})
_SD_HEIGHTS = frozenset({240, 243, 288, 480, 486, 576})

# Manual override choices shared by the CLI and the combos.
PIXEL_ASPECT_CHOICES: tuple[tuple[str, str], ...] = (
    ("auto", "Auto"),
    ("1", "1.0 square"),
    ("1.25", "1.25x anamorphic"),
    ("1.3", "1.3x anamorphic"),
    ("1.3333333333", "1.33x anamorphic"),
    ("1.5", "1.5x anamorphic"),
    ("1.65", "1.65x anamorphic"),
    ("1.8", "1.8x anamorphic"),
    ("2", "2x anamorphic"),
)


@dataclass(frozen=True)
class PixelAspect:
    """How non-square pixels are tagged and displayed.

    ``factor`` is the display pixel aspect (pixel width / pixel height).
    ``kind`` says where it came from:

    * ``square``: factor 1
    * ``lens``: optical squeeze from camera metadata or a non-broadcast tag
    * ``broadcast``: ITU / SD (or HD broadcast anamorphic) sample aspect
    * ``override``: the user set the value
    """

    factor: float
    kind: Kind = "square"

    @property
    def exr_factor(self) -> float:
        """Value for OpenEXR ``pixelAspectRatio``. Broadcast SAR stays 1."""
        if self.kind == "broadcast":
            return SQUARE_FACTOR
        return self.factor

    @property
    def sample_factor(self) -> float:
        """Value for a video sample-aspect / pasp tag."""
        return self.factor

    def label(self) -> str:
        """Short HUD text, or ``""`` when the pixels are square."""
        if self.kind != "broadcast" and is_square(self.factor):
            return ""
        if self.kind in ("lens", "override"):
            named = lens_label(self.factor)
            if named:
                return named
        return f"PAR {format_factor(self.factor)}"


SQUARE = PixelAspect(SQUARE_FACTOR, "square")


def is_square(factor: float) -> bool:
    return math.isfinite(factor) and abs(factor - 1.0) <= 0.005


def lens_label(factor: float) -> str:
    """``"2x anamorphic"`` when *factor* is a known lens squeeze, else ``""``."""
    for value, text in _LENS_LABELS:
        if abs(factor - value) <= _LENS_TOL:
            return text
    return ""


def format_factor(factor: float) -> str:
    """``2``, ``1.3``, or ``16:11`` for a clean ratio."""
    if not math.isfinite(factor) or factor <= 0:
        return "1"
    frac = Fraction(factor).limit_denominator(100)
    if abs(float(frac) - factor) <= 0.001 and 1 < frac.denominator <= 100:
        return f"{frac.numerator}:{frac.denominator}"
    return f"{factor:.4f}".rstrip("0").rstrip(".")


def display_size(width: int, height: int, factor: float) -> tuple[int, int]:
    """Storage ``width`` x ``height`` mapped to square pixels for painting.

    Horizontal only: height stays the storage height. A factor of 2 paints
    twice as wide, then the caller squeezes back into the stored raster.
    """
    safe = factor if math.isfinite(factor) and factor > 0 else 1.0
    return max(1, int(round(int(width) * safe))), max(1, int(height))


def sample_aspect_fraction(factor: float) -> Fraction:
    """Encoder sample aspect. Square and invalid values become 1:1."""
    if not math.isfinite(factor) or factor <= 0:
        return Fraction(1, 1)
    frac = Fraction(factor).limit_denominator(10000)
    if frac <= 0:
        return Fraction(1, 1)
    return frac


def parse_pixel_aspect_choice(text: str | None) -> float | None:
    """``auto`` / empty → ``None`` (detect). Otherwise a positive factor.

    Accepts ``2``, ``1.33``, ``4/3`` and ``2:1``.
    """
    raw = (text or "").strip().lower()
    if raw in ("", "auto", "detect"):
        return None
    if raw in ("square", "spherical", "1x", "1.0x", "off", "none"):
        return 1.0
    value = _parse_factor_token(raw)
    if value is None or not math.isfinite(value) or value <= 0 or value > 8:
        raise ValueError(
            f"Pixel aspect must be auto or a positive number (for example 1, 1.3, 2), not {text!r}."
        )
    return float(value)


def describe(aspect: PixelAspect) -> str:
    """One log line. No em dash."""
    if aspect.kind == "broadcast":
        return (
            f"Pixel aspect: {aspect.label()}. Broadcast sample aspect, not a lens squeeze. "
            "The video tag is kept. OpenEXR pixelAspectRatio stays 1.0. "
            "Pixels are not resampled."
        )
    if is_square(aspect.exr_factor):
        return "Pixel aspect: 1.0 (square). Pixels are not squeezed."
    return (
        f"Pixel aspect: {aspect.label()}. "
        f"OpenEXR pixelAspectRatio {format_factor(aspect.exr_factor)}. "
        f"Video sample aspect {format_factor(aspect.sample_factor)}. "
        "Pixels stay squeezed."
    )


def apply_override(detected: PixelAspect, override: float | None) -> PixelAspect:
    """User choice wins. ``None`` keeps *detected*."""
    if override is None:
        return detected
    if not math.isfinite(override) or override <= 0:
        raise ValueError(f"Pixel aspect must be positive, not {override!r}.")
    if is_square(override):
        return PixelAspect(SQUARE_FACTOR, "override")
    return PixelAspect(float(override), "override")


def is_broadcast_sample_aspect(sar: float, width: int, height: int) -> bool:
    """True for ITU / SD (and HD broadcast anamorphic) sample aspect.

    A 2.0 tag on a cinema raster is not broadcast. 720x576 with the 16:9
    sample aspect is.
    """
    if is_square(sar) or width <= 0 or height <= 0 or not math.isfinite(sar):
        return False
    ratios = _BROADCAST_SAR.get((int(width), int(height)))
    if ratios is not None:
        for ratio in ratios:
            if abs(sar - float(ratio)) <= 0.015:
                return True
    if int(width) in _SD_WIDTHS and int(height) in _SD_HEIGHTS:
        dar = (width / height) * sar
        if abs(dar - (4.0 / 3.0)) <= 0.02 or abs(dar - (16.0 / 9.0)) <= 0.02:
            return True
    return False


def classify_sample_aspect(sar: float, width: int, height: int) -> PixelAspect:
    """Container sample aspect → lens squeeze, broadcast SAR, or square."""
    if not math.isfinite(sar) or sar <= 0 or is_square(sar):
        return SQUARE
    if is_broadcast_sample_aspect(sar, width, height):
        return PixelAspect(float(sar), "broadcast")
    return PixelAspect(float(sar), "lens")


def resolve_detected(
    optical: float | None,
    sar: float,
    width: int,
    height: int,
) -> PixelAspect:
    """Camera optical squeeze wins over a container sample aspect.

    An explicit square from camera metadata (``1`` / ``off``) does not become
    a lens. A broadcast sample aspect on the same file is still kept for the
    video tag.
    """
    if optical is not None and math.isfinite(optical) and optical > 0:
        if not is_square(optical):
            return PixelAspect(float(optical), "lens")
        classified = classify_sample_aspect(sar, width, height)
        if classified.kind == "broadcast":
            return classified
        return SQUARE
    return classify_sample_aspect(sar, width, height)


def tagged_sample_aspect(
    aspect: PixelAspect,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
) -> float:
    """Sample aspect written on the output.

    A manual override is used as given. Otherwise the display aspect is kept
    when a resize changes the storage shape (a uniform scale leaves it as-is).
    """
    factor = aspect.sample_factor
    if aspect.kind == "override":
        return factor
    if src_w <= 0 or src_h <= 0 or out_w <= 0 or out_h <= 0:
        return factor
    if src_w == out_w and src_h == out_h:
        return factor
    display = (src_w / src_h) * factor
    return display / (out_w / out_h)


def merge_for_write(aspect: PixelAspect | None, extra: dict | None) -> PixelAspect:
    """Combine a probed aspect with per-frame attributes.

    A manual override is left alone. An optical value in *extra* (the
    generic camera RAW plugin stores one as text) wins over square or
    broadcast, because that metadata is a lens squeeze.
    """
    base = aspect if aspect is not None else SQUARE
    if base.kind == "override":
        return base
    optical = optical_from_mapping(extra or {})
    if optical is not None and not is_square(optical) and base.kind in ("square", "broadcast"):
        return PixelAspect(optical, "lens")
    return base


def optical_from_mapping(meta: dict | None) -> float | None:
    """First optical-squeeze value in *meta*, or ``None`` when absent.

    Explicit square (``1``, ``off``) returns ``1.0`` so a later container
    sample aspect does not invent a lens. Missing keys return ``None``.
    """
    if not meta:
        return None
    found: float | None = None
    for key, raw in meta.items():
        if not _is_optical_key(str(key)):
            continue
        parsed = _parse_factor_token(str(raw))
        if parsed is None:
            continue
        if not is_square(parsed):
            return parsed
        found = SQUARE_FACTOR
    return found


def optical_from_default_scale(horizontal: float, vertical: float) -> float | None:
    """DNG DefaultScale (scale to square pixels) → pixel aspect.

    The DNG spec multiplies width by the first value and height by the
    second. The pixel is ``horizontal/vertical`` times as wide as it is tall.
    """
    if (
        not math.isfinite(horizontal)
        or not math.isfinite(vertical)
        or horizontal <= 0
        or vertical <= 0
    ):
        return None
    return float(horizontal / vertical)


def aspect_from_default_scale(
    horizontal: float,
    vertical: float,
    *,
    present: bool,
) -> PixelAspect:
    if not present:
        return SQUARE
    ratio = optical_from_default_scale(horizontal, vertical)
    if ratio is None or is_square(ratio):
        return SQUARE
    return PixelAspect(ratio, "lens")


def stream_sample_aspect(stream: object) -> float:
    """Sample aspect of a PyAV stream, or 1 when the tag is missing."""
    found = 1.0
    for obj in (stream, getattr(stream, "codec_context", None)):
        if obj is None:
            continue
        sar = getattr(obj, "sample_aspect_ratio", None)
        if sar is None:
            continue
        try:
            value = float(sar)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        if not is_square(value):
            return value
        found = value
    return found


def apply_stream_sample_aspect(stream: object, factor: float) -> None:
    """Set the stream sample aspect (QuickTime pasp, H.264/HEVC VUI)."""
    frac = sample_aspect_fraction(factor)
    try:
        stream.sample_aspect_ratio = frac  # type: ignore[attr-defined]
    except Exception:
        pass
    ctx = getattr(stream, "codec_context", None)
    if ctx is None:
        return
    try:
        ctx.sample_aspect_ratio = frac
    except Exception:
        pass


def aspect_from_video_file(path: str) -> PixelAspect:
    """Sample aspect and any optical key in a PyAV container."""
    import av

    with av.open(path) as container:
        streams = container.streams.video
        if not streams:
            return SQUARE
        stream = streams[0]
        ctx = stream.codec_context
        width = int(getattr(ctx, "width", 0) or 0)
        height = int(getattr(ctx, "height", 0) or 0)
        meta: dict[str, str] = {}
        try:
            meta.update({str(k): str(v) for k, v in (container.metadata or {}).items()})
        except Exception:
            pass
        try:
            meta.update({str(k): str(v) for k, v in (stream.metadata or {}).items()})
        except Exception:
            pass
        return resolve_detected(
            optical_from_mapping(meta), stream_sample_aspect(stream), width, height
        )


def read_spec_pixel_aspect(spec: object) -> float:
    """Float pixel aspect from an OIIO spec, or 1 when the header has none."""
    for name in ("pixelAspectRatio", "PixelAspectRatio"):
        getter = getattr(spec, "get_float_attribute", None)
        if not callable(getter):
            break
        try:
            value = float(getter(name))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0 and not is_square(value):
            return value
    return SQUARE_FACTOR


def aspect_from_still(path: str) -> PixelAspect:
    """OpenEXR / DPX / other OIIO still. Honours ``pixelAspectRatio``."""
    import OpenImageIO as oiio

    inp = oiio.ImageInput.open(path)
    if not inp:
        return SQUARE
    try:
        spec = inp.spec()
        par = read_spec_pixel_aspect(spec)
        width = int(spec.width or 0)
        height = int(spec.height or 0)
    finally:
        inp.close()
    if not math.isfinite(par) or par <= 0 or is_square(par):
        return SQUARE
    if is_broadcast_sample_aspect(par, width, height):
        return PixelAspect(par, "broadcast")
    return PixelAspect(par, "lens")


def probe_media_aspect(path: str) -> PixelAspect:
    """Best pixel aspect for *path*. Square when the file cannot be read."""
    from pathlib import Path

    if not path:
        return SQUARE
    try:
        from .braw import is_braw_path
        from .cinemadng import is_cinemadng_input
        from .r3d import is_r3d_path
        from .source_plugins import plugin_for_path
    except Exception:
        return SQUARE

    try:
        plugin = plugin_for_path(path)
    except Exception:
        plugin = None
    if plugin is not None:
        return _aspect_from_plugin(plugin, path)
    try:
        if is_cinemadng_input(path):
            return _aspect_from_cinemadng(path)
        if is_r3d_path(path):
            return _aspect_from_r3d(path)
        if is_braw_path(path):
            return _aspect_from_braw(path)
    except Exception:
        return SQUARE

    suffix = Path(path).suffix.lower()
    if suffix in {".exr", ".dpx", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}:
        try:
            return aspect_from_still(path)
        except Exception:
            return SQUARE
    try:
        return aspect_from_video_file(path)
    except Exception:
        try:
            return aspect_from_still(path)
        except Exception:
            return SQUARE


def pixel_aspect_of(source: object) -> PixelAspect:
    """Aspect attached to an ingest source, or square."""
    explicit = getattr(source, "pixel_aspect", None)
    if isinstance(explicit, PixelAspect):
        return explicit
    info = getattr(source, "info", None)
    if info is None:
        return SQUARE
    kind = str(getattr(info, "pixel_aspect_kind", "square") or "square")
    factor = float(getattr(info, "pixel_aspect", 1.0) or 1.0)
    if kind not in ("square", "lens", "broadcast", "override"):
        kind = "lens" if not is_square(factor) else "square"
    if kind == "square" and is_square(factor):
        return SQUARE
    return PixelAspect(factor, kind)  # type: ignore[arg-type]


def note_plugin_aspect(source: object, plugin: object, path: str) -> None:
    """Fill ``source.pixel_aspect`` from plugin metadata when it is still square."""
    if pixel_aspect_of(source).kind != "square":
        return
    try:
        found = _aspect_from_plugin(plugin, path)
    except Exception:
        return
    if found.kind == "square" and is_square(found.factor):
        return
    try:
        source.pixel_aspect = found  # type: ignore[attr-defined]
    except Exception:
        return


def _aspect_from_plugin(plugin: object, path: str) -> PixelAspect:
    info_fn = getattr(plugin, "pixel_aspect_info", None)
    if callable(info_fn):
        try:
            got = info_fn(path)
        except Exception:
            got = None
        if isinstance(got, PixelAspect):
            return got
    meta: dict = {}
    probe_meta = getattr(plugin, "probe_metadata", None)
    if callable(probe_meta):
        try:
            raw = probe_meta(path)
            if isinstance(raw, dict):
                meta = raw
        except Exception:
            meta = {}
    optical = optical_from_mapping({str(k): str(v) for k, v in meta.items()})
    if optical is not None and not is_square(optical):
        return PixelAspect(optical, "lens")
    return SQUARE


def _aspect_from_cinemadng(path: str) -> PixelAspect:
    from .cinemadng.input import frame_paths
    from .cinemadng.native import probe_meta

    meta = probe_meta(frame_paths(path)[0])
    return aspect_from_default_scale(
        meta.default_scale[0],
        meta.default_scale[1],
        present=meta.has_default_scale,
    )


def _aspect_from_r3d(path: str) -> PixelAspect:
    from .r3d import R3DClip
    from .r3d import is_available as r3d_available

    if not r3d_available():
        return SQUARE
    with R3DClip(path) as clip:
        optical = optical_from_mapping(clip.clip_metadata_dict())
        if optical is None or is_square(optical):
            return SQUARE
        return PixelAspect(optical, "lens")


def _aspect_from_braw(path: str) -> PixelAspect:
    from .braw import BRAWClip
    from .braw import is_available as braw_available

    if not braw_available():
        return SQUARE
    with BRAWClip(path) as clip:
        optical = optical_from_mapping(clip.clip_metadata_dict())
        if optical is None or is_square(optical):
            return SQUARE
        return PixelAspect(optical, "lens")


def _is_optical_key(key: str) -> bool:
    norm = _norm_key(key)
    if norm in _OPTICAL_KEYS:
        return True
    # "exrconverter:r3d:anamorphic" → anamorphic
    tail = norm.rsplit(":", 1)[-1]
    return tail in _OPTICAL_KEYS


def _norm_key(key: str) -> str:
    text = key.strip().lower().replace(" ", "_").replace("-", "_")
    # Human labels from Inspect ("Pixel aspect") collapse to pixel_aspect.
    return text


def _parse_factor_token(raw: str) -> float | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text in ("off", "none", "no", "square", "spherical", "n/a", "na"):
        return 1.0
    if text.endswith("x") and len(text) > 1:
        text = text[:-1].strip()
    text = text.replace(",", ".")
    for sep in ("/", ":"):
        if sep in text:
            left, _, right = text.partition(sep)
            try:
                num = float(left.strip())
                den = float(right.strip())
            except ValueError:
                return None
            if den == 0:
                return None
            return num / den
    try:
        return float(text)
    except ValueError:
        return None


__all__ = [
    "ATTR_OPTICAL_SQUEEZE",
    "ATTR_PIXEL_ASPECT",
    "ATTR_SAMPLE_ASPECT",
    "PIXEL_ASPECT_CHOICES",
    "SQUARE",
    "PixelAspect",
    "apply_override",
    "apply_stream_sample_aspect",
    "aspect_from_default_scale",
    "aspect_from_still",
    "aspect_from_video_file",
    "classify_sample_aspect",
    "describe",
    "display_size",
    "format_factor",
    "is_broadcast_sample_aspect",
    "is_square",
    "lens_label",
    "merge_for_write",
    "note_plugin_aspect",
    "optical_from_default_scale",
    "optical_from_mapping",
    "parse_pixel_aspect_choice",
    "pixel_aspect_of",
    "probe_media_aspect",
    "resolve_detected",
    "sample_aspect_fraction",
    "stream_sample_aspect",
    "tagged_sample_aspect",
]
