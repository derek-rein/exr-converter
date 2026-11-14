"""Optional proprietary decoder SDKs (R3D, BRAW, ARRI, camera RAW).

Release builds fetch those bridges when the private GitHub tokens are set.
A source checkout without them is normal: the app still runs; those files
cannot be decoded until a Release binary or a local `make *-bridge`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class OptionalSdkError(RuntimeError):
    """This build does not include the decoder this file needs."""


def missing_bridge_message(kind: str, docs: str) -> str:
    return f"{kind} is unavailable in this build (see {docs})."


def log_missing_bridge(
    log,
    *,
    kind: str,
    docs: str,
    candidates: Iterable[Path],
    frozen: bool,
) -> str:
    """Record a missing optional bridge. Returns the short user-facing reason."""
    msg = missing_bridge_message(kind, docs)
    paths = [str(p) for p in list(candidates)[:12]]
    if frozen:
        log.warning("%s", msg)
    else:
        log.debug("%s candidates=%s", msg, paths)
    return msg
