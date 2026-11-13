"""Pure rules for the media browser dialogs: layout sizes, in/out text (no Qt, no I/O)."""

from __future__ import annotations


def outer_split_ok(sizes: list[int] | None) -> bool:
    """True when saved *sizes* can restore the tree | content splitter."""
    return sizes is not None and len(sizes) == 2 and all(s > 0 for s in sizes)


def content_split_ok(sizes: list[int] | None) -> bool:
    """True when saved *sizes* can restore the content | Metadata splitter."""
    return sizes is not None and len(sizes) == 2 and sizes[0] > 0 and sizes[1] >= 0


def metadata_split(
    total: int,
    saved: list[int] | None,
    *,
    content_min: int,
    default_width: int = 260,
    min_saved_width: int = 120,
) -> list[int]:
    """Content | Metadata sizes when the Metadata pane opens.

    Reuses the last saved Metadata width when it is at least *min_saved_width*;
    the content side never drops below *content_min*.
    """
    meta_w = default_width
    if saved is not None and len(saved) == 2 and saved[1] >= min_saved_width:
        meta_w = saved[1]
    return [max(total - meta_w, content_min), meta_w]


def frame_range_text(rng: tuple[int, int] | None) -> str:
    """``"first-last"`` for an in/out range, or ``""`` without one."""
    return f"{rng[0]}-{rng[1]}" if rng else ""


def in_out_status_text(rng: tuple[int, int]) -> str:
    """Footer status line after the user sets Preview in/out points."""
    return f"In {rng[0]} · Out {rng[1]} ({rng[1] - rng[0] + 1} frames) — Open uses this range"


# Same values as browser_state.VIEW_* (that module imports Qt; this one must not).
VIEW_LIST = "list"
VIEW_GRID = "grid"


def initial_browse_mode(saved_view: str, saved_last_browse: str) -> str:
    """List or Grid to show first, from the saved (coerced) view modes.

    *saved_view* may be Preview; Preview then opens later, over the browse
    mode that was in use before it (*saved_last_browse*).
    """
    last_browse = saved_last_browse
    if saved_view == VIEW_GRID:
        last_browse = VIEW_GRID
    elif saved_view == VIEW_LIST:
        last_browse = VIEW_LIST
    return last_browse if last_browse in (VIEW_LIST, VIEW_GRID) else VIEW_LIST
