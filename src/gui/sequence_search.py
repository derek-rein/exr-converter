"""In-memory fuzzy search over image sequences for :class:`SequenceBrowserDialog`.

The index itself (folder walk, frame grouping, sqlite ``:memory:`` table,
rapidfuzz scoring) lives in :mod:`src.core.sequence_index` and runs in a
**separate process** through :class:`~src.core.bg_worker.BackgroundWorker`.
A walk over a big plate tree is pure-Python work; on a thread it held the GIL
and every Qt → Python callback on the GUI thread (model ``data()``, paint,
timers) queued behind it, which is what made the browser stutter while
indexing. Now the GUI thread only enqueues commands and a relay thread turns
replies into queued Qt signals. If a process cannot be started, the same
server runs on a thread.

The index lives only while the dialog is open.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from ..core.bg_worker import BackgroundWorker
from ..core.sequence_index import (
    SEARCH_CUTOFF,
    SEARCH_LIMIT,
    SequenceHit,
    _norm,
    run_index_process,
)

log = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS = 180

__all__ = [
    "SEARCH_CUTOFF",
    "SEARCH_DEBOUNCE_MS",
    "SEARCH_LIMIT",
    "SequenceHit",
    "SequenceIndex",
    "SequenceIndexSignals",
]


class SequenceIndexSignals(QObject):
    """Emitted from the relay thread; connect with ``QueuedConnection``."""

    # (indexed sequence count, indexing still running)
    index_changed = Signal(int, bool)
    # (generation, query, list[SequenceHit], indexed count, indexing still running)
    search_ready = Signal(int, str, object, int, bool)


class SequenceIndex:
    """GUI-side handle. Every public method is non-blocking except ``stop``.

    The worker starts on the first command. Prefers a child process; pass
    ``use_process=False`` to run it on a thread instead.
    """

    def __init__(self, *, use_process: bool = True) -> None:
        # Unparented on purpose: the relay holds a reference until it exits,
        # so a late emit never hits a deleted C++ object.
        self.signals = SequenceIndexSignals()
        self._use_process = use_process
        self._worker: BackgroundWorker | None = None
        # Roots asked for so far: replayed if the worker has to be restarted.
        self._roots: list[str] = []
        self._closed = False
        self._lock = threading.Lock()

    # -- GUI thread API -----------------------------------------------------

    def ensure_indexed(self, directory: str) -> None:
        """Index *directory*'s subtree unless an indexed or running root covers it."""
        if directory:
            root = _norm(directory)
            if root not in self._roots:
                self._roots.append(root)
            self._post(("index", root))

    def search(
        self,
        query: str,
        generation: int,
        *,
        limit: int = SEARCH_LIMIT,
        cutoff: float = SEARCH_CUTOFF,
    ) -> None:
        self._post(("search", query, generation, limit, cutoff))

    def clear(self) -> None:
        """Drop every row and cancel walks (root set changed)."""
        self._roots.clear()
        self._post(("clear",))

    def is_running(self) -> bool:
        w = self._worker
        return w is not None and w.is_alive()

    def uses_process(self) -> bool:
        w = self._worker
        return w is not None and w.uses_process

    def stop(self, timeout: float = 2.0) -> bool:
        """Cancel walks, discard the index, and wait for the worker to exit."""
        with self._lock:
            self._closed = True
            worker = self._worker
        if worker is None:
            return True
        ok = worker.stop(timeout)
        if not ok:
            log.warning("Sequence index did not stop within %.1fs", timeout)
        return ok

    def _post(self, cmd: tuple) -> None:
        with self._lock:
            if self._closed:
                return
            replay: list[str] = []
            if self._worker is not None and self._worker.crashed:
                # The child died (e.g. could not start): carry on on a thread.
                log.warning("Sequence index process exited; continuing on a thread")
                self._use_process = False
                self._worker = None
                replay = [r for r in self._roots if cmd != ("index", r)]
            if self._worker is None:
                self._worker = BackgroundWorker(
                    run_index_process,
                    self._deliver,
                    name="SequenceIndex",
                    use_process=self._use_process,
                )
            worker = self._worker
        for root in replay:
            worker.post(("index", root))
        worker.post(cmd)

    # -- relay thread -------------------------------------------------------

    def _deliver(self, event: tuple) -> None:
        if self._closed:
            return
        kind = event[0]
        if kind == "index_changed":
            _, count, busy = event
            self.signals.index_changed.emit(int(count), bool(busy))
        elif kind == "search_ready":
            _, gen, query, hits, count, busy = event
            self.signals.search_ready.emit(int(gen), str(query), list(hits), int(count), bool(busy))
