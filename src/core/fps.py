"""Frame-rate parsing shared by the CLI, settings and the encoders.

Accepts decimals (``"24"``, ``"23.976"``) and exact rationals
(``"24000/1001"``, also ``"24000:1001"`` as FFmpeg writes them). Rationals stay
:class:`~fractions.Fraction` so the encoder can use them exactly; decimals stay
``float`` and are snapped to the usual NTSC rationals at encode time.
"""

from __future__ import annotations

import argparse
import math
from fractions import Fraction

FpsValue = float | Fraction


def parse_fps(value: object) -> FpsValue:
    """Parse a frame rate. Raises :class:`ValueError` when it is not a positive rate."""
    if isinstance(value, bool):
        raise ValueError(f"invalid frame rate: {value!r}")
    if isinstance(value, Fraction):
        result: FpsValue = value
    elif isinstance(value, int | float):
        result = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if text.lower().endswith("fps"):
            text = text[:-3].strip()
        sep = "/" if "/" in text else (":" if ":" in text else "")
        if sep:
            num_s, _, den_s = text.partition(sep)
            if any(c in num_s + den_s for c in "/:"):
                raise ValueError(f"invalid frame rate: {value!r}")
            try:
                num = Fraction(num_s.strip())
                den = Fraction(den_s.strip())
            except (ValueError, ZeroDivisionError) as exc:
                raise ValueError(f"invalid frame rate: {value!r}") from exc
            if den == 0:
                raise ValueError(f"invalid frame rate: {value!r} (zero denominator)")
            result = num / den
        else:
            try:
                result = float(text)
            except ValueError as exc:
                raise ValueError(f"invalid frame rate: {value!r}") from exc
    else:
        raise ValueError(f"invalid frame rate: {value!r}")

    if isinstance(result, float) and not math.isfinite(result):
        raise ValueError(f"invalid frame rate: {value!r}")
    if result <= 0:
        raise ValueError(f"frame rate must be positive: {value!r}")
    return result


def fps_arg(text: str) -> FpsValue:
    """``argparse`` type for ``--fps`` (decimal or N/D)."""
    try:
        return parse_fps(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{exc}. Use a number such as 24 or 23.976, or a ratio such as 24000/1001."
        ) from exc


def fps_to_float(value: object, default: float = 24.0) -> float:
    """Lenient float for settings / probe values; *default* when unparsable."""
    try:
        return float(parse_fps(value))
    except ValueError:
        return default


def format_fps(value: FpsValue) -> str:
    """Short display form: ``24``, ``23.976`` or ``24000/1001``."""
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return str(value.numerator)
        return f"{value.numerator}/{value.denominator}"
    if value == int(value):
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")
