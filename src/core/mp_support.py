"""Multiprocessing set-up for source runs and Nuitka-frozen builds.

Call :func:`configure_multiprocessing` first thing in the entry point.

Frozen builds hit one problem in particular: Python 3.13.7+ rewrote how
``multiprocessing.resource_tracker`` builds its command line, so Nuitka's
build-time patch (``args += ['-c', cmd % r]`` →
``--multiprocessing-resource-tracker``) no longer matches. The tracker is then
launched as ``<app binary> -OO -c "from multiprocessing.resource_tracker
import main;main(N)"``, Nuitka's self-execution guard refuses it ("the program
tried to call itself with '-c' argument"), and the parent prints
"resource_tracker: process died unexpectedly, relaunching" for every pool.
:func:`_patch_resource_tracker_launch` rewrites that one command line at run
time to the form the Nuitka binary understands.
"""

from __future__ import annotations

import multiprocessing
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any

_RESOURCE_TRACKER_CMD = re.compile(
    r"^\s*from multiprocessing\.resource_tracker import main\s*;\s*main\((\d+)\)\s*$"
)
_PATCH_FLAG = "_exr_converter_resource_tracker_patch"


def is_frozen() -> bool:
    """True inside a Nuitka (or PyInstaller-style) packaged build."""
    return "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def frozen_resource_tracker_args(args: Sequence[Any]) -> list[Any] | None:
    """Rewrite a ``-c`` resource-tracker command line for a frozen binary.

    Returns ``[exe, "--multiprocessing-resource-tracker", fd]`` for the
    stdlib form, or ``None`` when *args* is something else (left untouched).
    """
    items = list(args)
    if "-c" not in items:
        return None
    i = items.index("-c")
    if i + 1 >= len(items):
        return None
    m = _RESOURCE_TRACKER_CMD.match(str(items[i + 1]))
    if m is None:
        return None
    return [items[0], "--multiprocessing-resource-tracker", m.group(1)]


def _patch_resource_tracker_launch() -> None:
    from multiprocessing import util

    original: Callable[..., int] = util.spawnv_passfds
    if getattr(original, _PATCH_FLAG, False):
        return

    def spawnv_passfds(path, args, passfds):
        rewritten = frozen_resource_tracker_args(args)
        return original(path, rewritten if rewritten is not None else args, passfds)

    setattr(spawnv_passfds, _PATCH_FLAG, True)
    util.spawnv_passfds = spawnv_passfds


def configure_multiprocessing() -> None:
    """Make worker pools start cleanly from source and from packaged builds.

    * ``freeze_support()`` so a frozen Windows child runs the worker, not the app.
    * ``spawn`` start method everywhere (fork in a threaded Qt process can
      deadlock; Python 3.14 would otherwise pick forkserver on Linux).
    * Frozen only: children start from the app binary (``set_executable``) and
      the resource tracker is launched the way Nuitka expects.
    """
    multiprocessing.freeze_support()
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass
    if not is_frozen():
        return
    exe = getattr(sys, "executable", "") or ""
    if exe:
        multiprocessing.set_executable(exe)
    if sys.platform != "win32":
        _patch_resource_tracker_launch()
