"""Polling watch folder that reports new video files once they stop growing.

Modelled on DaVinci Resolve's proxy watch folders: drop camera media into a
folder and a proxy is made for each new clip. A file only counts as ready when
its size and mtime have held steady for ``settle_seconds``, so clips still
being copied from a card or synced from a NAS are not picked up half-written.

Pure Python (no Qt) so both the GUI and the ``proxy --watch`` CLI daemon use it.
Polling instead of OS file events keeps it reliable on network shares and
removable volumes, where change notifications are often missing.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .local_fs import can_probe_media, should_skip_search_dir
from .proxy import PROXY_SUBFOLDER, is_proxy_source

DEFAULT_SETTLE_SECONDS = 3.0
DEFAULT_POLL_SECONDS = 2.0


class WatchFolder:
    """Track a folder and return each new, fully written video exactly once.

    Call :meth:`poll` periodically. Files already present when the watcher is
    created are ignored unless *include_existing* is true. A file that is
    later replaced (size or mtime changes) is reported again.
    """

    def __init__(
        self,
        folder: str | os.PathLike[str],
        *,
        recursive: bool = False,
        include_existing: bool = False,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
        exclude_dirs: tuple[str | os.PathLike[str], ...] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.folder = Path(folder)
        self.recursive = recursive
        self.settle_seconds = max(0.0, float(settle_seconds))
        self._clock = clock
        self._exclude = {self._norm(p) for p in exclude_dirs}
        # path → (size, mtime_ns, first time this signature was seen)
        self._pending: dict[str, tuple[int, int, float]] = {}
        # path → signature already reported
        self._done: dict[str, tuple[int, int]] = {}
        if not include_existing:
            for path, sig in self._scan():
                self._done[path] = sig

    @staticmethod
    def _norm(p: str | os.PathLike[str]) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(p)))

    def _skip_dir(self, path: str, name: str) -> bool:
        if name.startswith(".") or name == PROXY_SUBFOLDER or should_skip_search_dir(name):
            return True
        return self._norm(path) in self._exclude

    def _iter_files(self) -> Iterator[os.DirEntry[str]]:
        stack = [str(self.folder)]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if self.recursive and not self._skip_dir(entry.path, entry.name):
                            stack.append(entry.path)
                        continue
                    if entry.is_file():
                        yield entry
                except OSError:
                    continue

    def _scan(self) -> list[tuple[str, tuple[int, int]]]:
        out: list[tuple[str, tuple[int, int]]] = []
        for entry in self._iter_files():
            if not is_proxy_source(entry.name):
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            out.append((entry.path, (int(st.st_size), int(st.st_mtime_ns))))
        return out

    def poll(self) -> list[Path]:
        """Return files that became ready since the last poll."""
        now = self._clock()
        ready: list[Path] = []
        seen: set[str] = set()
        for path, sig in self._scan():
            seen.add(path)
            if self._done.get(path) == sig:
                continue
            prev = self._pending.get(path)
            if prev is None or (prev[0], prev[1]) != sig:
                self._pending[path] = (sig[0], sig[1], now)
                if self.settle_seconds > 0:
                    continue
                prev = self._pending[path]
            if sig[0] <= 0 or now - prev[2] < self.settle_seconds:
                continue
            if not can_probe_media(path):
                continue
            del self._pending[path]
            self._done[path] = sig
            ready.append(Path(path))
        # Forget files that disappeared so a re-copy is picked up again.
        for gone in [p for p in self._pending if p not in seen]:
            del self._pending[gone]
        for gone in [p for p in self._done if p not in seen]:
            del self._done[gone]
        return sorted(ready)

    @property
    def pending_count(self) -> int:
        """Files seen but still settling (being copied)."""
        return len(self._pending)
