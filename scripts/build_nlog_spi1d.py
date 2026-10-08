#!/usr/bin/env python3
"""Write the Nikon N-Log decode LUT used by the bundled OCIO config.

The curve is Nikon's published N-Log specification (version 1.0.0, 1 September
2018), not Nikon's downloadable cube. Full-range 10-bit code values are
normalized by 1023. ``y = 0.18`` is stop 0.

    if code < 452:  y = (code / 650) ** 3 - 0.0075
    else:           y = exp((code - 619) / 150)

Regenerate with ``python3 scripts/build_nlog_spi1d.py`` from the repo root.
"""

from __future__ import annotations

import math
from pathlib import Path

LENGTH = 4096
OUT = Path(__file__).resolve().parents[1] / "resources" / "ocio" / "luts" / "nlog_to_lin.spi1d"


def nlog_to_linear(normalized_code: float) -> float:
    """Decode a full-range normalized N-Log code value to scene reflectance."""
    code = normalized_code * 1023.0
    if code < 452.0:
        return (code / 650.0) ** 3 - 0.0075
    return math.exp((code - 619.0) / 150.0)


def render() -> str:
    lines = [
        "Version 1",
        "From 0.0 1.0",
        f"Length {LENGTH}",
        "Components 1",
        "{",
    ]
    last = LENGTH - 1
    for i in range(LENGTH):
        lines.append(f"{nlog_to_linear(i / last):.10f}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    mid = 650.0 * (0.18 + 0.0075) ** (1.0 / 3.0) / 1023.0
    print(f"wrote {OUT} ({LENGTH} samples); 18% code {mid:.8f}")


if __name__ == "__main__":
    main()
