"""Recursive folder / file name search for the browsers' **Search folders** field.

Runs as a :class:`~src.core.bg_worker.BackgroundWorker` target (a child
process by default), so a deep walk never competes with the GUI thread for
the GIL. Results stream back in small batches; a newer query replaces the
running one between directories.

Commands: ``("search", token, root, query, ext_filter | None, dirs_only)``,
``("cancel",)``, ``("stop",)``.
Events: ``("batch", token, [(name, path, rel, is_dir), ...])``,
``("done", token, total)``, ``("stopped",)``.
"""

from __future__ import annotations

import os
import queue
from collections.abc import Iterator

from .fs_skip import SEARCH_SKIP_ABSPATHS, SEARCH_SKIP_DIRS, SEARCH_SKIP_FILES, SEARCH_SKIP_SUFFIXES
from .local_fs import is_cloud_placeholder_dir

MAX_SEARCH_DEPTH = 15
SEARCH_BATCH_SIZE = 60
MAX_SEARCH_RESULTS = 500


def iter_search(
    root: str,
    query: str,
    ext_filter: frozenset[str] | None = None,
    dirs_only: bool = False,
) -> Iterator[list[tuple[str, str, str, bool]]]:
    """Yield the matches found in each directory under *root* (depth-first).

    Matches are ``(name, full_path, path_relative_to_root, is_dir)``. Names
    match on a case-insensitive substring, or on the exact extension.
    Never follows symlinked folders and skips online-only (cloud) folders.
    """
    query_lower = query.lower()
    root_stripped = root.rstrip(os.sep) or root
    root_len = len(root_stripped) + 1
    stack: list[tuple[str, int]] = [(root_stripped, 0)]
    while stack:
        dirpath, depth = stack.pop()
        found: list[tuple[str, str, str, bool]] = []
        try:
            scanner = os.scandir(dirpath)
        except OSError:
            yield found
            continue
        with scanner:
            for entry in scanner:
                name = entry.name
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                name_lower = name.lower()
                ext_lower = os.path.splitext(name_lower)[1]
                if not is_dir and (
                    dirs_only
                    or name in SEARCH_SKIP_FILES
                    or name.startswith("._")
                    or ext_lower in SEARCH_SKIP_SUFFIXES
                    or (ext_filter is not None and ext_lower not in ext_filter)
                ):
                    pass
                elif query_lower in name_lower or query_lower == ext_lower:
                    found.append((name, entry.path, entry.path[root_len:], is_dir))
                if (
                    is_dir
                    and depth < MAX_SEARCH_DEPTH
                    and name not in SEARCH_SKIP_DIRS
                    and not name.startswith(".")
                    and entry.path not in SEARCH_SKIP_ABSPATHS
                    and not is_cloud_placeholder_dir(entry.path, entry=entry)
                ):
                    stack.append((entry.path, depth + 1))
        yield found


def run_dir_search_process(cmd_q, evt_q) -> None:  # noqa: ANN001 — process or thread queues
    """BackgroundWorker target: one search at a time, newest query wins."""
    from .bg_worker import wait_for_command

    current: Iterator[list[tuple[str, str, str, bool]]] | None = None
    token = 0
    total = 0
    batch: list[tuple[str, str, str, bool]] = []

    def finish() -> None:
        nonlocal current, batch
        if batch:
            evt_q.put(("batch", token, batch))
            batch = []
        evt_q.put(("done", token, total))
        current = None

    try:
        while True:
            try:
                cmd = wait_for_command(cmd_q, None if current is None else 0)
            except queue.Empty:
                cmd = None
            if cmd is not None:
                kind = cmd[0]
                if kind == "stop":
                    return
                if kind == "cancel":
                    current = None
                    batch = []
                    continue
                if kind == "search":
                    _, token, root, query, exts, dirs_only = cmd
                    ext_filter = frozenset(exts) if exts is not None else None
                    current = iter_search(root, query, ext_filter, bool(dirs_only))
                    total = 0
                    batch = []
                continue
            if current is None:
                continue
            try:
                found = next(current)
            except StopIteration:
                finish()
                continue
            for item in found:
                batch.append(item)
                total += 1
                if len(batch) >= SEARCH_BATCH_SIZE:
                    evt_q.put(("batch", token, batch))
                    batch = []
                if total >= MAX_SEARCH_RESULTS:
                    break
            if total >= MAX_SEARCH_RESULTS:
                finish()
    finally:
        try:
            evt_q.put(("stopped",))
        except Exception:
            pass
