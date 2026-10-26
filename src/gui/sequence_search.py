"""In-memory fuzzy search over image sequences for :class:`SequenceBrowserDialog`.

One dedicated ``threading.Thread`` owns an ``sqlite3`` ``:memory:`` database.
It walks folders the user navigates into (sequence-level rows, not frames),
serves fuzzy queries scored with rapidfuzz, and reports back through queued
Qt signals. The GUI thread only enqueues commands, so neither indexing nor
scoring blocks the event loop. The index lives only while the dialog is open.
"""

from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from rapidfuzz import fuzz, process, utils

from ..core.constants import is_image_sequence_ext
from ..core.local_fs import (
    entry_is_dir,
    is_cloud_placeholder,
    iter_dir_entries,
    should_skip_search_dir,
)
from ..core.sequence import find_image_sequences_in_list, sequence_pattern_name
from .browser_chrome import _SEARCH_SKIP_ABSPATHS, _SEARCH_SKIP_DIRS

log = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS = 180
SEARCH_LIMIT = 50
SEARCH_CUTOFF = 60.0

# Plate trees are deep but not unbounded; matches the folder search depth cap.
_MAX_INDEX_DEPTH = 20
# Hard ceiling so a walk from a volume root cannot grow without bound.
_MAX_INDEXED_SEQUENCES = 250_000
# Commit a batch after this many rows or this long, whichever comes first.
_INSERT_BATCH_ROWS = 500
_STEP_BUDGET_S = 0.03
_PROGRESS_INTERVAL_S = 0.2
# Render outputs often live in tmp / temp / build / dist folders; index them.
_INDEX_SKIP_DIRS = _SEARCH_SKIP_DIRS - {"tmp", "temp", "build", "dist"}

_SCHEMA = """
CREATE TABLE sequences (
    path TEXT PRIMARY KEY,
    parent TEXT NOT NULL,
    stem TEXT NOT NULL,
    pattern TEXT NOT NULL,
    first_frame TEXT NOT NULL,
    ext TEXT NOT NULL,
    frame_count INTEGER NOT NULL,
    frame_range TEXT NOT NULL
);
CREATE INDEX sequences_parent ON sequences(parent);
"""


@dataclass(frozen=True)
class SequenceHit:
    """One fuzzy match: enough to label a result row and jump to the sequence."""

    score: float
    stem: str
    pattern: str
    parent: str
    first_frame: str
    ext: str
    frame_count: int
    frame_range: str


def _norm(path: str) -> str:
    return os.path.normpath(path) if path else ""


def _is_under(path: str, root: str) -> bool:
    """True when *path* is *root* or inside it (both normalised)."""
    a = os.path.normcase(path)
    b = os.path.normcase(root)
    if a == b:
        return True
    return a.startswith(b.rstrip("\\/") + os.sep)


def _sequence_rows(directory: str, image_paths: list[str]) -> list[tuple]:
    rows: list[tuple] = []
    for s in find_image_sequences_in_list(image_paths):
        frame_set = s.frameSet()
        if not frame_set:
            continue
        frames = sorted(frame_set)
        pattern = sequence_pattern_name(s)
        rows.append(
            (
                os.path.join(directory, pattern),
                directory,
                s.basename().rstrip("._"),
                pattern,
                _norm(str(s.frame(frames[0]))),
                (s.extension() or "").lower(),
                len(frames),
                s.frameRange(),
            )
        )
    return rows


def _walk_sequences(root: str, cancel: threading.Event) -> Iterator[list[tuple]]:
    """Yield sequence rows one directory at a time under *root*.

    Metadata only (``scandir`` + ``lstat``): never opens a frame, so cloud
    placeholders are listed by name and never hydrated. Symlinked and junction
    directories are not followed (no loops), online-only folders are skipped
    like the folder search does, and *cancel* is checked per entry.
    """
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack:
        if cancel.is_set():
            return
        directory, depth = stack.pop()
        image_paths: list[str] = []
        for entry in iter_dir_entries(directory):
            if cancel.is_set():
                return
            name = entry.name
            if entry_is_dir(entry):
                if (
                    depth < _MAX_INDEX_DEPTH
                    and not name.startswith(".")
                    and name not in _INDEX_SKIP_DIRS
                    and not should_skip_search_dir(name)
                    and entry.path not in _SEARCH_SKIP_ABSPATHS
                    and not _is_junction(entry)
                    and not is_cloud_placeholder(entry.path, entry=entry)
                ):
                    stack.append((entry.path, depth + 1))
                continue
            if name.startswith("."):
                continue
            # Every non-directory name counts, as in ``findSequencesOnDisk``.
            if is_image_sequence_ext(os.path.splitext(name)[1]):
                image_paths.append(entry.path)
        yield _sequence_rows(directory, image_paths) if image_paths else []


def _is_junction(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_junction()
    except (AttributeError, OSError):
        return False


class _Walk:
    __slots__ = ("cancel", "it", "root")

    def __init__(self, root: str) -> None:
        self.root = root
        self.cancel = threading.Event()
        self.it = _walk_sequences(root, self.cancel)


class SequenceIndexSignals(QObject):
    """Emitted from the index thread; connect with ``QueuedConnection``."""

    # (indexed sequence count, indexing still running)
    index_changed = Signal(int, bool)
    # (generation, query, list[SequenceHit], indexed count, indexing still running)
    search_ready = Signal(int, str, object, int, bool)


class SequenceIndex:
    """Owns the index thread. Every public method is non-blocking except ``stop``.

    Commands go through a queue; the thread is the only code that touches the
    sqlite connection (``check_same_thread`` stays on). Walks are stepped a
    directory at a time with the newest root first, and pending searches are
    served between steps, so a query never waits for a large walk to finish.
    """

    def __init__(self) -> None:
        # Unparented on purpose: the thread holds a reference until it exits,
        # so a late emit never hits a deleted C++ object.
        self.signals = SequenceIndexSignals()
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._lock = threading.Lock()

    # -- GUI thread API -----------------------------------------------------

    def ensure_indexed(self, directory: str) -> None:
        """Index *directory*'s subtree unless an indexed or running root covers it."""
        if directory:
            self._post(("index", _norm(directory)))

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
        self._post(("clear",))

    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def stop(self, timeout: float = 2.0) -> bool:
        """Cancel walks, discard the database, and join the thread.

        Walks check for cancellation per directory entry, so the join is short.
        Returns False if the thread is still alive after *timeout* (it is a
        daemon and exits at its next check).
        """
        with self._lock:
            self._closed = True
            self._stop.set()
            t = self._thread
        if t is None:
            return True
        self._queue.put(("stop",))
        if t is not threading.current_thread():
            t.join(timeout)
        if t.is_alive():
            log.warning("Sequence index thread did not stop within %.1fs", timeout)
            return False
        return True

    def _post(self, cmd: tuple) -> None:
        with self._lock:
            if self._closed:
                return
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="SequenceIndex", daemon=True)
                self._thread.start()
        self._queue.put(cmd)

    # -- index thread -------------------------------------------------------

    def _emit_index_changed(self, count: int, busy: bool) -> None:
        if not self._stop.is_set():
            self.signals.index_changed.emit(count, busy)

    def _run(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(_SCHEMA)
            self._serve(conn)
        except Exception:
            log.exception("Sequence index thread failed")
        finally:
            conn.close()

    def _serve(self, conn: sqlite3.Connection) -> None:
        walks: list[_Walk] = []
        done_roots: list[str] = []
        stems: list[str] | None = None
        stem_paths: list[str] = []
        count = 0
        pending: list[tuple] = []
        last_progress = 0.0
        capped = False

        while not self._stop.is_set():
            # Block only when idle; otherwise drain whatever is waiting.
            cmds: list[tuple] = []
            try:
                cmds.append(self._queue.get(timeout=None if not walks else 0))
                while True:
                    cmds.append(self._queue.get_nowait())
            except queue.Empty:
                pass

            search_cmd: tuple | None = None
            for cmd in cmds:
                kind = cmd[0]
                if kind == "stop":
                    return
                if kind == "clear":
                    for w in walks:
                        w.cancel.set()
                    walks.clear()
                    done_roots.clear()
                    pending.clear()
                    conn.execute("DELETE FROM sequences")
                    conn.commit()
                    count = 0
                    stems = None
                    capped = False
                    self._emit_index_changed(0, False)
                elif kind == "index":
                    root = cmd[1]
                    if capped or any(_is_under(root, r) for r in done_roots):
                        continue
                    if any(_is_under(root, w.root) for w in walks):
                        continue
                    # A parent root supersedes running walks below it.
                    for w in [w for w in walks if _is_under(w.root, root)]:
                        w.cancel.set()
                        walks.remove(w)
                    walks.append(_Walk(root))
                    self._emit_index_changed(count, True)
                elif kind == "search":
                    search_cmd = cmd  # only the newest query matters

            if search_cmd is not None:
                if pending:
                    count = self._flush(conn, pending, count)
                    stems = None
                if stems is None:
                    rows = conn.execute("SELECT path, stem FROM sequences").fetchall()
                    stem_paths = [r[0] for r in rows]
                    stems = [r[1] for r in rows]
                _, query, gen, limit, cutoff = search_cmd
                hits = self._score(conn, query, stems, stem_paths, limit, cutoff)
                if not self._stop.is_set():
                    self.signals.search_ready.emit(gen, query, hits, count, bool(walks))

            if not walks:
                continue

            # Step the newest walk for a short slice, then go back to the queue.
            walk = walks[-1]
            deadline = time.monotonic() + _STEP_BUDGET_S
            finished = False
            while time.monotonic() < deadline and len(pending) < _INSERT_BATCH_ROWS:
                if self._stop.is_set() or walk.cancel.is_set():
                    break
                try:
                    pending.extend(next(walk.it))
                except StopIteration:
                    finished = True
                    break
            if pending and (
                finished
                or len(pending) >= _INSERT_BATCH_ROWS
                or time.monotonic() - last_progress >= _PROGRESS_INTERVAL_S
            ):
                count = self._flush(conn, pending, count)
                stems = None
            if count >= _MAX_INDEXED_SEQUENCES:
                log.warning("Sequence index capped at %d sequences", count)
                capped = True
                for w in walks:
                    w.cancel.set()
                walks.clear()
                finished = False
            if finished:
                walks.remove(walk)
                done_roots[:] = [r for r in done_roots if not _is_under(r, walk.root)]
                done_roots.append(walk.root)
            now = time.monotonic()
            if finished or not walks or now - last_progress >= _PROGRESS_INTERVAL_S:
                last_progress = now
                self._emit_index_changed(count, bool(walks))

    @staticmethod
    def _flush(conn: sqlite3.Connection, pending: list[tuple], count: int) -> int:
        conn.executemany(
            "INSERT OR REPLACE INTO sequences "
            "(path, parent, stem, pattern, first_frame, ext, frame_count, frame_range) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            pending,
        )
        conn.commit()
        pending.clear()
        return int(conn.execute("SELECT COUNT(*) FROM sequences").fetchone()[0])

    @staticmethod
    def _score(
        conn: sqlite3.Connection,
        query: str,
        stems: list[str],
        stem_paths: list[str],
        limit: int,
        cutoff: float,
    ) -> list[SequenceHit]:
        query = query.strip()
        if not query or not stems:
            return []
        matches = process.extract(
            query,
            stems,
            scorer=fuzz.WRatio,
            processor=utils.default_process,
            limit=limit,
            score_cutoff=cutoff,
        )
        if not matches:
            return []
        scores = {stem_paths[i]: float(score) for _, score, i in matches}
        marks = ",".join("?" * len(scores))
        rows = conn.execute(
            "SELECT path, stem, pattern, parent, first_frame, ext, frame_count, frame_range "
            f"FROM sequences WHERE path IN ({marks})",
            list(scores),
        ).fetchall()
        hits = [
            SequenceHit(scores[r[0]], r[1], r[2], r[3], r[4], r[5], int(r[6]), r[7]) for r in rows
        ]
        # Best score first; shorter names win ties (closer to the query), then path.
        hits.sort(key=lambda h: (-h.score, len(h.stem), h.parent, h.pattern))
        return hits
