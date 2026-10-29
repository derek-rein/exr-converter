"""Lazy, background-listed folder tree for the file browsers.

Replaces the ``QFileSystemModel`` proxy the browsers used before. That proxy
answered every ``index()`` / ``parent()`` / ``data()`` call by asking
``QFileSystemModel`` for a path, which can ``stat`` on the GUI thread, and it
re-laid out the whole tree each time any folder finished loading. On a slow
or cloud-backed volume that was the beachball.

This model follows what fast file managers do (Dolphin/KIO list jobs,
Nautilus' async directory loading, ``QFileSystemModel``'s own gatherer
thread):

* **Nothing touches the disk on the GUI thread.** Each folder is listed with
  ``os.scandir`` on a small pool of daemon threads. The GUI thread only
  inserts the finished rows (``beginInsertRows`` / ``endInsertRows``).
* **Lazy.** A folder is listed the first time it is expanded or revealed.
* **Cached.** Listings stay in the model while the dialog is open; a folder is
  listed again (and merged in place, keeping expansion) when the user opens it
  and the listing is a few seconds old.
* **Newest first.** The pool serves the most recent request first, so the
  folder you just clicked is not stuck behind a slow one.
* **Cheap ``data()``.** Every row is a plain Python object with its label,
  icon kind and path already computed, so painting never calls back into
  the file system.

Rows are folders plus, depending on *file_mode*, image sequences collapsed to
one row each (``plate.####.exr  1001-1100``) or video files.
"""

from __future__ import annotations

import itertools
import logging
import os
import re
import stat
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractItemModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFileIconProvider

from ..core.constants import is_image_sequence_ext
from .browser_volumes import VolumeInfo, _norm_mount_path, list_browser_volumes

log = logging.getLogger(__name__)

KIND_VOLUME = 0
KIND_DIR = 1
KIND_SEQUENCE = 2
KIND_FILE = 3

FILE_MODE_NONE = ""
FILE_MODE_SEQUENCES = "sequences"
FILE_MODE_VIDEOS = "videos"

# Custom roles
KindRole = Qt.ItemDataRole.UserRole + 1
FirstFrameRole = Qt.ItemDataRole.UserRole + 2

_UNLOADED, _LOADING, _LOADED = 0, 1, 2

# A folder clicked again after this long is listed again in the background.
_RELIST_AFTER_S = 3.0
_LIST_WORKERS = 4

# macOS packages Finder shows as files; never useful as plate folders.
_PACKAGE_SUFFIXES = (
    ".app",
    ".bundle",
    ".framework",
    ".plugin",
    ".kext",
    ".photoslibrary",
    ".musiclibrary",
    ".fcpbundle",
    ".rtfd",
)
_SKIP_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"})

_UF_HIDDEN = getattr(stat, "UF_HIDDEN", 0x8000)
_WIN_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2) | getattr(
    stat, "FILE_ATTRIBUTE_SYSTEM", 0x4
)

_DIGITS = re.compile(r"(\d+)")


def _natural_key(name: str) -> tuple:
    """Case-insensitive natural order: ``shot_2`` before ``shot_10``.

    Ties (``shot_010`` / ``shot_10``) fall back to the plain name so the
    order never depends on the order ``scandir`` returned.
    """
    folded = name.casefold()
    return (tuple(int(t) if t.isdigit() else t for t in _DIGITS.split(folded)), folded, name)


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


# ---------------------------------------------------------------------------
# Listing (worker threads)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ListedEntry:
    """One row produced by :func:`list_directory`."""

    name: str
    path: str
    kind: int
    label: str
    tooltip: str = ""
    first_frame: str = ""
    hidden: bool = False


def _entry_hidden(entry: os.DirEntry[str]) -> bool:
    """Finder / Explorer hidden flag (dot names are filtered by the caller)."""
    if sys.platform == "darwin":
        try:
            return bool(entry.stat(follow_symlinks=False).st_flags & _UF_HIDDEN)
        except (OSError, AttributeError):
            return False
    if sys.platform == "win32":
        try:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            return False
        return bool(attrs & _WIN_HIDDEN)
    return False


def list_directory(
    path: str,
    file_mode: str = FILE_MODE_NONE,
    file_exts: frozenset[str] = frozenset(),
    skip_paths: frozenset[str] = frozenset(),
) -> list[ListedEntry]:
    """List *path*: folders first, then sequences or files, natural order.

    Metadata only (``scandir`` and, for folders on macOS / Windows, ``lstat``
    for the hidden flag). Never opens a file, so cloud placeholders are not
    downloaded. *skip_paths* are normalised paths never shown (for example
    ``/Volumes`` on macOS, whose drives are already top-level rows).
    Raises ``OSError`` when the folder itself cannot be listed.
    """
    dirs: list[ListedEntry] = []
    images: list[str] = []
    files: list[ListedEntry] = []
    with os.scandir(path) as it:
        for entry in it:
            name = entry.name
            if name.startswith(".") or name in _SKIP_FILE_NAMES:
                continue
            full = entry.path
            if skip_paths and _path_key(full) in skip_paths:
                continue
            try:
                is_dir = entry.is_dir()  # follows links: linked folders are folders
            except OSError:
                continue
            if is_dir:
                if sys.platform == "darwin" and name.lower().endswith(_PACKAGE_SUFFIXES):
                    continue
                dirs.append(
                    ListedEntry(name, full, KIND_DIR, name, full, hidden=_entry_hidden(entry))
                )
                continue
            if not file_mode:
                continue
            ext = os.path.splitext(name)[1].lower()
            if file_mode == FILE_MODE_SEQUENCES:
                if is_image_sequence_ext(ext):
                    images.append(full)
            elif file_mode == FILE_MODE_VIDEOS and ext in file_exts:
                files.append(ListedEntry(name, full, KIND_FILE, name, full))

    out = sorted(dirs, key=lambda e: _natural_key(e.name))
    if images:
        out.extend(_sequence_entries(path, images))
    if files:
        out.extend(sorted(files, key=lambda e: _natural_key(e.name)))
    return out


def _sequence_entries(directory: str, image_paths: list[str]) -> list[ListedEntry]:
    from ..core.sequence import find_image_sequences_in_list, sequence_pattern_name

    rows: list[ListedEntry] = []
    for s in find_image_sequences_in_list(image_paths):
        frames = sorted(s.frameSet() or ())
        if not frames:
            continue
        pattern = sequence_pattern_name(s)
        frange = s.frameRange()
        first = os.path.normpath(str(s.frame(frames[0])))
        count = len(frames)
        rows.append(
            ListedEntry(
                pattern,
                os.path.join(directory, pattern),
                KIND_SEQUENCE,
                f"{pattern}  {frange}",
                f"{os.path.join(directory, pattern)}\n{count} frame(s)  {frange}",
                first_frame=first,
            )
        )
    rows.sort(key=lambda e: _natural_key(e.name))
    return rows


class _DaemonPool:
    """Tiny LIFO thread pool with daemon workers.

    ``ThreadPoolExecutor`` joins its threads at interpreter exit, so one
    ``scandir`` stuck on a dead network mount would hang quitting the app.
    Daemon threads do not. LIFO so the newest request (the folder the user
    just opened) runs first.
    """

    def __init__(self, workers: int) -> None:
        self._jobs: deque[Callable[[], None]] = deque()
        self._cv = threading.Condition()
        for i in range(workers):
            threading.Thread(target=self._loop, name=f"BrowserList-{i}", daemon=True).start()

    def submit(self, fn: Callable[[], None]) -> None:
        with self._cv:
            self._jobs.append(fn)
            self._cv.notify()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._jobs:
                    self._cv.wait()
                fn = self._jobs.pop()
            try:
                fn()
            except Exception:
                log.exception("Browser background job failed")


_POOL: _DaemonPool | None = None
_POOL_LOCK = threading.Lock()


def background_pool() -> _DaemonPool:
    """Process-wide pool shared by every browser dialog."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = _DaemonPool(_LIST_WORKERS)
        return _POOL


# ---------------------------------------------------------------------------
# Volume polling (worker thread)
# ---------------------------------------------------------------------------


class VolumePoller(QObject):
    """Lists mounted volumes off the GUI thread every few seconds.

    ``QStorageInfo`` asks every mount for its size and state; a hung network
    share blocks that call, so it must not run on the GUI thread. Emits
    ``volumes_changed`` only when the list actually changed.
    """

    volumes_changed = Signal(list)
    _polled = Signal(int, list)

    def __init__(self, parent: QObject | None = None, *, interval_ms: int = 3000) -> None:
        super().__init__(parent)
        self._volumes: list[VolumeInfo] | None = None
        self._busy = False
        self._gen = 0
        self._polled.connect(self._on_polled, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        self.poll()

    def volumes(self) -> list[VolumeInfo] | None:
        """Last known list, or ``None`` before the first poll returns."""
        return None if self._volumes is None else list(self._volumes)

    def stop(self) -> None:
        self._timer.stop()
        self._gen += 1

    def poll(self) -> None:
        if self._busy:
            return
        self._busy = True
        gen = self._gen
        signal = self._polled

        def job() -> None:
            try:
                vols = list_browser_volumes()
            except Exception:
                log.exception("Listing volumes failed")
                vols = []
            try:
                signal.emit(gen, vols)
            except RuntimeError:
                pass  # poller deleted with its dialog

        threading.Thread(target=job, name="BrowserVolumes", daemon=True).start()

    def _on_polled(self, gen: int, vols: list) -> None:
        self._busy = False
        if gen != self._gen or not vols:
            return
        if vols == self._volumes:
            return
        self._volumes = list(vols)
        self.volumes_changed.emit(list(vols))


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------

_ICONS: dict[int, QIcon] = {}


def _sequence_icon() -> QIcon:
    """Three stacked frames: reads as "image sequence" at 16 px."""
    icon = QIcon()
    for edge in (16, 32):
        pm = QPixmap(QSize(edge, edge))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        s = edge / 16.0
        pen = QPen(QColor(150, 190, 230))
        pen.setWidthF(max(1.0, s))
        p.setPen(pen)
        for i, alpha in enumerate((90, 150, 255)):
            c = QColor(70, 120, 170)
            c.setAlpha(alpha)
            p.setBrush(c)
            off = int((2 - i) * 2.5 * s)
            p.drawRoundedRect(
                QRect(int(1 * s) + off, int(5 * s) - off, int(9 * s), int(9 * s)), s, s
            )
        p.end()
        icon.addPixmap(pm)
    return icon


def _icon_for(kind: int, is_system_root: bool = False) -> QIcon:
    key = -1 if is_system_root else kind
    icon = _ICONS.get(key)
    if icon is not None:
        return icon
    provider = QFileIconProvider()
    if key == -1:
        icon = provider.icon(QFileIconProvider.IconType.Computer)
        if icon.isNull():
            icon = provider.icon(QFileIconProvider.IconType.Drive)
    elif kind == KIND_VOLUME:
        icon = provider.icon(QFileIconProvider.IconType.Drive)
    elif kind == KIND_DIR:
        icon = provider.icon(QFileIconProvider.IconType.Folder)
    elif kind == KIND_SEQUENCE:
        icon = _sequence_icon()
    else:
        icon = provider.icon(QFileIconProvider.IconType.File)
    _ICONS[key] = icon
    return icon


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

_NODE_IDS = itertools.count(1)


class _Node:
    __slots__ = (
        "children",
        "first_frame",
        "hidden",
        "id",
        "kind",
        "label",
        "listed_at",
        "name",
        "parent",
        "path",
        "row",
        "state",
        "system_root",
        "tooltip",
    )

    def __init__(
        self,
        name: str,
        path: str,
        kind: int,
        label: str,
        tooltip: str = "",
        first_frame: str = "",
        system_root: bool = False,
    ) -> None:
        self.id = next(_NODE_IDS)
        self.name = name
        self.path = path
        self.kind = kind
        self.label = label
        self.tooltip = tooltip or path
        self.first_frame = first_frame
        self.system_root = system_root
        self.parent: _Node | None = None
        self.children: list[_Node] = []
        # Hidden folders (UF_HIDDEN / Windows hidden) are kept here so a pasted
        # path like /private/tmp can still be revealed; they are not rows.
        self.hidden: dict[str, ListedEntry] = {}
        self.row = 0
        self.state = _LOADED if kind in (KIND_SEQUENCE, KIND_FILE) else _UNLOADED
        self.listed_at = 0.0

    def is_container(self) -> bool:
        return self.kind in (KIND_VOLUME, KIND_DIR)

    @classmethod
    def from_entry(cls, e: ListedEntry) -> _Node:
        return cls(e.name, e.path, e.kind, e.label, e.tooltip, e.first_frame)


@dataclass
class _Reveal:
    target: str
    callback: Callable[[QModelIndex], None]
    relisted: set[str]


class BrowserTreeModel(QAbstractItemModel):
    """Volumes as top-level rows; folders (and sequences / files) below.

    Keeps the subset of the ``QFileSystemModel`` API the browsers use
    (``index(path)``, ``filePath``, ``fileName``, ``isDir``) and adds
    :meth:`reveal` to load a path's ancestors in the background.
    """

    volumes_changed = Signal()
    directory_loaded = Signal(str)
    _listed = Signal(int, str, object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        file_mode: str = FILE_MODE_NONE,
        file_exts: Iterable[str] = (),
        poller: VolumePoller | None = None,
    ) -> None:
        super().__init__(parent)
        self._file_mode = file_mode
        self._file_exts = frozenset(e.lower() for e in file_exts)
        self._root = _Node("", "", KIND_DIR, "")
        self._root.state = _LOADED
        self._by_id: dict[int, _Node] = {}
        self._by_path: dict[str, _Node] = {}
        self._volumes: list[VolumeInfo] = []
        self._volumes_known = False
        self._gen = 0
        self._inflight: set[str] = set()
        self._reveals: list[_Reveal] = []
        self._empty_loaded: set[int] = set()
        self._empty_timer = QTimer(self)
        self._empty_timer.setSingleShot(True)
        self._empty_timer.setInterval(60)
        self._empty_timer.timeout.connect(self._flush_empty)
        self._listed.connect(self._on_listed, Qt.ConnectionType.QueuedConnection)
        self._skip_paths = (
            frozenset({_path_key("/Volumes")}) if sys.platform == "darwin" else frozenset()
        )

        self._poller = poller if poller is not None else VolumePoller(self)
        self._poller.volumes_changed.connect(self.set_volumes)
        known = self._poller.volumes()
        if known is not None:
            self.set_volumes(known)

    # -- volumes --------------------------------------------------------------

    def poller(self) -> VolumePoller:
        return self._poller

    def volumes(self) -> list[VolumeInfo]:
        return list(self._volumes)

    def refresh_volumes(self) -> None:
        """Ask the poller for a fresh list (result arrives asynchronously)."""
        self._poller.poll()

    def set_volumes(self, vols: list[VolumeInfo]) -> None:
        """Merge a new volume list into the top-level rows (keeps expansion)."""
        first = not self._volumes_known
        self._volumes_known = True
        if vols == self._volumes and not first:
            return
        self._volumes = list(vols)
        entries = [ListedEntry(v.name, v.path, KIND_VOLUME, v.name, v.path) for v in self._volumes]
        system_paths = {_path_key(v.path) for v in self._volumes if v.is_system_root}
        self._merge_children(self._root, entries)
        for n in self._root.children:
            n.system_root = _path_key(n.path) in system_paths
        self._advance_reveals()
        if not first:
            self.volumes_changed.emit()

    # -- node helpers ---------------------------------------------------------

    def _node(self, index: QModelIndex | QPersistentModelIndex) -> _Node:
        if not index.isValid():
            return self._root
        return self._by_id.get(int(index.internalId()), self._root)

    def _index_of(self, node: _Node) -> QModelIndex:
        if node is self._root or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.row, 0, node.id)

    def _register(self, node: _Node) -> None:
        self._by_id[node.id] = node
        self._by_path.setdefault(_path_key(node.path), node)

    def _unregister_tree(self, node: _Node) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            self._by_id.pop(n.id, None)
            key = _path_key(n.path)
            if self._by_path.get(key) is n:
                del self._by_path[key]
            self._inflight.discard(key)
            stack.extend(n.children)

    def node_for_path(self, path: str) -> _Node | None:
        return self._by_path.get(_path_key(path))

    def _volume_node_for(self, path: str) -> _Node | None:
        norm = _norm_mount_path(path)
        best: _Node | None = None
        best_len = -1
        for n in self._root.children:
            vp = _norm_mount_path(n.path)
            if sys.platform == "win32":
                a, b = norm.lower(), vp.lower()
                ok = a == b or a.startswith(b if b.endswith("\\") else b + "\\")
            else:
                ok = norm == vp or norm.startswith(vp.rstrip("/") + "/")
            if ok and len(vp) > best_len:
                best, best_len = n, len(vp)
        return best

    # -- QFileSystemModel-compatible API --------------------------------------

    def index(self, *args):  # type: ignore[override]
        """``index(row, column, parent)`` or ``index(path)`` (loaded rows only)."""
        if len(args) == 1 and isinstance(args[0], str):
            node = self.node_for_path(args[0])
            if node is None or node.parent is None:
                return QModelIndex()
            return self._index_of(node)
        row = int(args[0]) if args else 0
        column = int(args[1]) if len(args) > 1 else 0
        parent = args[2] if len(args) > 2 else QModelIndex()
        if column != 0 or row < 0:
            return QModelIndex()
        pnode = self._node(parent)
        if row >= len(pnode.children):
            return QModelIndex()
        return self.createIndex(row, 0, pnode.children[row].id)

    def filePath(self, index: QModelIndex | QPersistentModelIndex) -> str:  # noqa: N802
        if not index.isValid():
            return ""
        return self._node(index).path

    def fileName(self, index: QModelIndex | QPersistentModelIndex) -> str:  # noqa: N802
        if not index.isValid():
            return ""
        return self._node(index).name

    def isDir(self, index: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        return index.isValid() and self._node(index).is_container()

    def kind(self, index: QModelIndex | QPersistentModelIndex) -> int:
        return self._node(index).kind if index.isValid() else -1

    def first_frame(self, index: QModelIndex | QPersistentModelIndex) -> str:
        return self._node(index).first_frame if index.isValid() else ""

    # -- QAbstractItemModel ---------------------------------------------------

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: ARG002, N802
        return 1

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802
        if parent is None:
            parent = QModelIndex()
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self._node(parent).children)

    def parent(self, index=None):  # type: ignore[override]
        if index is None:
            return super().parent()
        if not index.isValid():
            return QModelIndex()
        node = self._by_id.get(int(index.internalId()))
        if node is None or node.parent is None:
            return QModelIndex()
        return self._index_of(node.parent)

    def hasChildren(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> bool:  # noqa: N802
        if parent is None:
            parent = QModelIndex()
        node = self._node(parent)
        if not node.is_container():
            return False
        if node.state == _LOADED:
            return bool(node.children)
        return True

    def canFetchMore(self, parent: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        if not parent.isValid():
            return False
        node = self._node(parent)
        return node.is_container() and node.state == _UNLOADED

    def fetchMore(self, parent: QModelIndex | QPersistentModelIndex) -> None:  # noqa: N802
        if parent.isValid():
            self._request_listing(self._node(parent))

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if not index.isValid():
            return None
        node = self._by_id.get(int(index.internalId()))
        if node is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return node.label
        if role == Qt.ItemDataRole.DecorationRole:
            return _icon_for(node.kind, node.system_root)
        if role == Qt.ItemDataRole.ToolTipRole:
            return node.tooltip
        if role == KindRole:
            return node.kind
        if role == FirstFrameRole:
            return node.first_frame
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        )

    def mimeTypes(self) -> list[str]:  # noqa: N802
        return ["text/uri-list"]

    def mimeData(self, indexes) -> QMimeData:  # type: ignore[override]  # noqa: N802
        """Folder paths as ``file://`` URLs for drag → Favorites."""
        md = QMimeData()
        urls: list[QUrl] = []
        seen: set[str] = set()
        for idx in indexes:
            if not idx.isValid() or idx.column() != 0:
                continue
            node = self._node(idx)
            path = node.first_frame or node.path
            if path and path not in seen:
                seen.add(path)
                urls.append(QUrl.fromLocalFile(path))
        if urls:
            md.setUrls(urls)
        return md

    # -- background listing ---------------------------------------------------

    def refresh(self, path: str, *, max_age: float = _RELIST_AFTER_S) -> None:
        """List *path* again in the background if its listing is older than *max_age*."""
        node = self.node_for_path(path)
        if node is None or not node.is_container():
            return
        if node.state == _LOADED and time.monotonic() - node.listed_at < max_age:
            return
        self._request_listing(node, force=True)

    def _request_listing(self, node: _Node, *, force: bool = False) -> None:
        if not node.is_container() or node is self._root:
            return
        key = _path_key(node.path)
        if key in self._inflight:
            return
        if node.state == _LOADED and not force:
            return
        if node.state == _UNLOADED:
            node.state = _LOADING
        self._inflight.add(key)
        gen = self._gen
        path = node.path
        mode, exts, skip = self._file_mode, self._file_exts, self._skip_paths
        signal = self._listed

        def job() -> None:
            try:
                result: object = list_directory(path, mode, exts, skip)
            except OSError as e:
                result = e
            except Exception as e:  # never lose the reply: the row would spin forever
                log.exception("Listing %s failed", path)
                result = OSError(str(e))
            try:
                signal.emit(gen, path, result)
            except RuntimeError:
                pass  # model deleted with its dialog

        background_pool().submit(job)

    def _on_listed(self, gen: int, path: str, result: object) -> None:
        key = _path_key(path)
        self._inflight.discard(key)
        if gen != self._gen:
            return
        node = self._by_path.get(key)
        if node is None or not node.is_container():
            return
        entries: list[ListedEntry] = [] if isinstance(result, BaseException) else list(result)  # type: ignore[arg-type]
        visible = [e for e in entries if not e.hidden]
        node.hidden = {e.name: e for e in entries if e.hidden}
        # Keep hidden folders that were revealed (promoted) as rows.
        promoted = {c.name for c in node.children if c.name in node.hidden}
        if promoted:
            visible.extend(node.hidden.pop(n) for n in promoted)
            visible.sort(key=lambda e: (e.kind != KIND_DIR, _natural_key(e.name)))
        was_loaded = node.state == _LOADED
        node.state = _LOADED
        node.listed_at = time.monotonic()
        self._merge_children(node, visible)
        if not node.children and not was_loaded:
            self._empty_loaded.add(node.id)
            self._empty_timer.start()
        self.directory_loaded.emit(path)
        self._advance_reveals()

    def _flush_empty(self) -> None:
        # An expanded folder that turned out empty keeps its arrow until the
        # view re-reads hasChildren(); one coalesced layout pass does that.
        ids = self._empty_loaded
        self._empty_loaded = set()
        if any(i in self._by_id for i in ids):
            self.layoutAboutToBeChanged.emit()
            self.layoutChanged.emit()

    def _merge_children(self, node: _Node, entries: list[ListedEntry]) -> None:
        """Make *node*'s rows match *entries* (same order), keeping kept rows' subtrees.

        Rows are matched by path, so a re-listing only inserts and removes the
        rows that changed and expanded folders stay expanded.
        """
        parent_index = self._index_of(node)
        new_keys = [_path_key(e.path) for e in entries]
        new_set = set(new_keys)
        old = node.children
        # 1. Remove rows that disappeared (contiguous runs, bottom up).
        i = len(old) - 1
        while i >= 0:
            if _path_key(old[i].path) in new_set:
                i -= 1
                continue
            end = i
            while i - 1 >= 0 and _path_key(old[i - 1].path) not in new_set:
                i -= 1
            self._remove_rows(node, parent_index, i, end)
            i -= 1
        # 2. Kept rows must already be in the new order; otherwise start over.
        by_key = {_path_key(n.path): n for n in old}
        if [_path_key(n.path) for n in old] != [k for k in new_keys if k in by_key]:
            if old:
                self._remove_rows(node, parent_index, 0, len(old) - 1)
            by_key = {}
        # 3. Walk the new order; insert missing runs, update changed labels.
        pos = 0
        changed_lo, changed_hi = -1, -1
        while pos < len(entries):
            e = entries[pos]
            existing = by_key.get(new_keys[pos])
            if existing is not None:
                if existing.label != e.label or existing.first_frame != e.first_frame:
                    existing.label = e.label
                    existing.tooltip = e.tooltip or e.path
                    existing.first_frame = e.first_frame
                    changed_lo = pos if changed_lo < 0 else changed_lo
                    changed_hi = pos
                pos += 1
                continue
            run_end = pos
            while run_end < len(entries) and new_keys[run_end] not in by_key:
                run_end += 1
            new_nodes = [_Node.from_entry(x) for x in entries[pos:run_end]]
            self.beginInsertRows(parent_index, pos, run_end - 1)
            for n in new_nodes:
                n.parent = node
                self._register(n)
            old[pos:pos] = new_nodes
            for r in range(pos, len(old)):
                old[r].row = r
            self.endInsertRows()
            pos = run_end
        if changed_lo >= 0:
            self.dataChanged.emit(
                self.index(changed_lo, 0, parent_index), self.index(changed_hi, 0, parent_index)
            )

    def _remove_rows(self, node: _Node, parent_index: QModelIndex, start: int, end: int) -> None:
        old = node.children
        self.beginRemoveRows(parent_index, start, end)
        for n in old[start : end + 1]:
            self._unregister_tree(n)
        del old[start : end + 1]
        for r in range(start, len(old)):
            old[r].row = r
        self.endRemoveRows()

    # -- reveal ---------------------------------------------------------------

    def reveal(self, path: str, callback: Callable[[QModelIndex], None] | None = None) -> None:
        """Load every ancestor of *path* in the background, then call *callback*.

        *callback* gets the index for *path*, or for the deepest ancestor that
        exists if *path* is gone. Runs immediately when the chain is already
        loaded.
        """
        if not path:
            return
        r = _Reveal(os.path.normpath(path), callback or (lambda _i: None), set())
        self._reveals.append(r)
        self._advance_reveals()

    def cancel_reveals(self) -> None:
        self._reveals.clear()

    def _advance_reveals(self) -> None:
        if not self._reveals or not self._volumes_known:
            return
        pending: list[_Reveal] = []
        done: list[tuple[_Reveal, QModelIndex]] = []
        for r in self._reveals:
            idx = self._step_reveal(r)
            if idx is None:
                pending.append(r)
            else:
                done.append((r, idx))
        self._reveals = pending
        for r, idx in done:
            try:
                r.callback(idx)
            except RuntimeError:
                pass  # receiver deleted
            except Exception:
                log.exception("Reveal callback failed for %s", r.target)

    def _step_reveal(self, r: _Reveal) -> QModelIndex | None:
        """Return the final index, or ``None`` while a listing is pending."""
        vol = self._volume_node_for(r.target)
        if vol is None:
            return QModelIndex()
        node = vol
        rel = (
            os.path.relpath(r.target, vol.path)
            if _path_key(r.target) != _path_key(vol.path)
            else "."
        )
        parts = [] if rel in (".", "") else [p for p in rel.split(os.sep) if p and p != "."]
        for part in parts:
            if node.state != _LOADED:
                self._request_listing(node)
                return None
            child = next((c for c in node.children if c.name == part), None)
            if child is None and sys.platform in ("darwin", "win32"):
                child = next((c for c in node.children if c.name.lower() == part.lower()), None)
            if child is None and part in node.hidden:
                child = self._promote_hidden(node, part)
            if child is None:
                key = _path_key(node.path)
                if key not in r.relisted:
                    # Might be new since the last listing: list once more.
                    r.relisted.add(key)
                    self._request_listing(node, force=True)
                    return None
                return self._index_of(node)
            node = child
        return self._index_of(node)

    def _promote_hidden(self, node: _Node, name: str) -> _Node | None:
        e = node.hidden.pop(name, None)
        if e is None:
            return None
        keys = [(c.kind != KIND_DIR, _natural_key(c.name)) for c in node.children]  # sorted
        key = (False, _natural_key(name))
        pos = 0
        while pos < len(keys) and keys[pos] < key:
            pos += 1
        child = _Node.from_entry(e)
        child.parent = node
        self.beginInsertRows(self._index_of(node), pos, pos)
        self._register(child)
        node.children.insert(pos, child)
        for r in range(pos, len(node.children)):
            node.children[r].row = r
        self.endInsertRows()
        return child

    def shutdown(self) -> None:
        """Drop pending work (dialog closing)."""
        self._gen += 1
        self._reveals.clear()
        self._inflight.clear()
        self._poller.stop()


# Old name kept for imports elsewhere.
MultiRootDirModel = BrowserTreeModel

__all__ = [
    "FILE_MODE_NONE",
    "FILE_MODE_SEQUENCES",
    "FILE_MODE_VIDEOS",
    "KIND_DIR",
    "KIND_FILE",
    "KIND_SEQUENCE",
    "KIND_VOLUME",
    "BrowserTreeModel",
    "FirstFrameRole",
    "KindRole",
    "ListedEntry",
    "MultiRootDirModel",
    "VolumePoller",
    "background_pool",
    "list_directory",
]
