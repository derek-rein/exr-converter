"""Cheap filesystem checks that do **not** open file contents.

Used by the in-app browsers so Dropbox / iCloud / OneDrive “online-only”
placeholders can be listed by name without hydrating (downloading) them.
``stat`` / ``lstat`` / ``scandir`` read metadata only; ``open`` / OIIO / PyAV
are what trigger a cloud recall.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

# Shown in the video-browser Codec column when a matching file is a stub.
ONLINE_ONLY_LABEL = "Online-only"

# Darwin vnode flags (sys/stat.h). Dataless = File Provider / iCloud evicted.
_UF_DATALESS = 0x00004000
_SF_DATALESS = 0x40000000
_UF_COMPRESSED = 0x00000020

# Windows GetFileAttributesW bits that mean “opening will recall from the cloud”.
# Do **not** treat generic REPARSE_POINT as offline (that includes local junctions).
_FILE_ATTRIBUTE_OFFLINE = 0x00001000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_WIN_CLOUD_ATTRS = (
    _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

# Cloud-client bookkeeping dirs (search must not walk these).
CLOUD_SKIP_DIR_NAMES = frozenset(
    {
        ".dropbox",
        ".dropbox.cache",
        ".dropbox.device",
        ".dropbox.attr",
    }
)

# Incomplete client downloads / sidecar names that are never media.
_INCOMPLETE_SUFFIXES = frozenset({".partial", ".crdownload", ".download"})
_CLOUD_SIDECAR_NAMES = frozenset({".dropbox", ".dropbox.attr"})


def path_is_dir(path: str | os.PathLike[str] | Path) -> bool:
    """``Path.is_dir()`` that treats metadata errors as “not a directory”."""
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def path_is_file(path: str | os.PathLike[str] | Path) -> bool:
    """``Path.is_file()`` that treats metadata errors as “not a file”."""
    try:
        return Path(path).is_file()
    except OSError:
        return False


def entry_is_dir(entry: os.DirEntry[str], *, follow_symlinks: bool = False) -> bool:
    """``DirEntry.is_dir`` that swallows placeholder / permission errors."""
    try:
        return entry.is_dir(follow_symlinks=follow_symlinks)
    except OSError:
        return False


def entry_is_file(entry: os.DirEntry[str], *, follow_symlinks: bool = False) -> bool:
    """``DirEntry.is_file`` that swallows placeholder / permission errors."""
    try:
        return entry.is_file(follow_symlinks=follow_symlinks)
    except OSError:
        return False


def is_incomplete_download_name(name: str) -> bool:
    """True for ``*.partial`` / browser download leftovers (basename only)."""
    base = Path(name).name
    if base in _CLOUD_SIDECAR_NAMES:
        return True
    lower = base.lower()
    return any(lower.endswith(suf) for suf in _INCOMPLETE_SUFFIXES)


def should_skip_search_dir(name: str) -> bool:
    """True when a directory name is cloud-client cache / bookkeeping."""
    return name in CLOUD_SKIP_DIR_NAMES


def iter_dir_entries(directory: str | os.PathLike[str] | Path) -> Iterator[os.DirEntry[str]]:
    """Yield ``scandir`` entries; skip the whole dir on ``OSError``.

    Per-entry ``OSError`` (broken reparse points) is skipped, not raised.
    """
    try:
        scanner = os.scandir(os.fspath(directory))
    except OSError:
        return
    with scanner:
        while True:
            try:
                entry = next(scanner)
            except StopIteration:
                return
            except OSError:
                continue
            yield entry


def lstat_nofollow(path: str | os.PathLike[str] | Path) -> os.stat_result | None:
    """``lstat`` or ``None`` — never follows links, never opens contents."""
    try:
        return os.lstat(os.fspath(path))
    except OSError:
        return None


def _windows_file_attributes(path: str) -> int | None:
    """Win32 attributes, or ``None`` when unavailable / call failed.

    Uses ``GetFileAttributesW`` (metadata only). Extracted so tests can stub it.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        get_attrs = ctypes.windll.kernel32.GetFileAttributesW
        get_attrs.argtypes = [ctypes.c_wchar_p]
        get_attrs.restype = ctypes.c_uint32
        attrs = int(get_attrs(path))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if attrs == _INVALID_FILE_ATTRIBUTES:
        return None
    return attrs


def _stat_blocks(st: os.stat_result) -> int | None:
    blocks = getattr(st, "st_blocks", None)
    if blocks is None:
        return None
    try:
        return int(blocks)
    except (TypeError, ValueError):
        return None


def _looks_dataless_stat(st: os.stat_result) -> bool:
    """True when vnode flags or allocated size say the payload is not local."""
    flags = int(getattr(st, "st_flags", 0) or 0)
    if flags & (_UF_DATALESS | _SF_DATALESS):
        return True
    # Compressed local files also have a small st_blocks; do not treat as cloud.
    if flags & _UF_COMPRESSED:
        return False
    size = int(getattr(st, "st_size", 0) or 0)
    if size <= 0:
        return False
    blocks = _stat_blocks(st)
    if blocks is None:
        return False
    # 0 allocated blocks + advertised size: classic iCloud / Smart Sync stub.
    return blocks == 0


def is_cloud_placeholder(
    path: str | os.PathLike[str] | Path,
    *,
    stat_result: os.stat_result | None = None,
    entry: os.DirEntry[str] | None = None,
) -> bool:
    """True when *path* looks like an online-only / File Provider stub.

    Never opens the file. False on ordinary local files (including sparse EXRs
    that actually have allocated blocks).
    """
    raw = os.fspath(path)
    if is_incomplete_download_name(raw):
        return True

    st = stat_result
    if st is None and entry is not None:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            return True
    if st is None:
        st = lstat_nofollow(raw)
    if st is None:
        return True

    if _looks_dataless_stat(st):
        return True

    # Symlink: the link inode is local; the target may be a cloud stub.
    if stat.S_ISLNK(st.st_mode):
        try:
            target_st = os.stat(raw)
        except OSError:
            return True
        if _looks_dataless_stat(target_st):
            return True
        raw = os.path.realpath(raw)

    win_attrs = _windows_file_attributes(raw)
    if win_attrs is not None and win_attrs & _WIN_CLOUD_ATTRS:
        return True
    return False


def can_probe_media(
    path: str | os.PathLike[str] | Path,
    *,
    stat_result: os.stat_result | None = None,
    entry: os.DirEntry[str] | None = None,
) -> bool:
    """True when *path* is a non-empty regular file that is safe to open.

    Returns False for missing paths, directories, zero-byte files, and cloud
    placeholders. Used before OIIO / PyAV / R3D so listing never hydrates.
    """
    raw = os.fspath(path)
    if not raw or is_incomplete_download_name(raw):
        return False

    st = stat_result
    if st is None and entry is not None:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            return False
    if st is None:
        st = lstat_nofollow(raw)
    if st is None:
        return False

    if stat.S_ISLNK(st.st_mode):
        try:
            st = os.stat(raw)
        except OSError:
            return False

    if not stat.S_ISREG(st.st_mode):
        return False
    if int(getattr(st, "st_size", 0) or 0) <= 0:
        return False
    if is_cloud_placeholder(raw, stat_result=st):
        return False
    return True
