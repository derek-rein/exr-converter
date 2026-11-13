"""Rules shared by the batch tabs (Video → Proxy, Transcode). No Qt here."""

from __future__ import annotations

from collections.abc import Iterable

# Output location choices. QSettings stores these strings.
LOCATION_BESIDE = "beside"
LOCATION_CUSTOM = "custom"


def custom_output_dir(location: str, folder_text: str) -> str | None:
    """The custom output folder, or ``None`` when outputs go beside each clip.

    A custom location with an empty folder also gives ``None``.
    """
    if location != LOCATION_CUSTOM:
        return None
    return folder_text.strip() or None


def watermark_payload(enabled: bool, text: str) -> str:
    """The invisible-watermark payload to embed (``""`` means none)."""
    return text.strip() if enabled else ""


def video_file_filter(suffixes: Iterable[str]) -> str:
    """The Add Clips… dialog filter for *suffixes* (``".mov"`` style), sorted."""
    exts = " ".join(f"*{e}" for e in sorted(suffixes))
    return f"Video ({exts});;All files (*)"
