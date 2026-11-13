"""EXR prefetch service: worker slots across a browser-preview resize."""

from __future__ import annotations

import threading
import time

import numpy as np

import src.services.exr_prefetch as ep
from src.services.exr_prefetch import ExrPrefetchService
from src.services.frame_cache import FrameCache


class _Seq:
    def frame(self, n: int) -> str:
        return f"/fake/plate.{n:04d}.exr"


def _pump(qapp, until, steps: int = 200) -> None:
    for _ in range(steps):
        qapp.processEvents()
        if until():
            return
        time.sleep(0.01)


def test_preview_resize_during_read_frees_the_worker_slot(qapp, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_read(path, transform, target=None, on_draft=None, frame=0, reduced_flag=None):
        started.set()
        release.wait(5)
        return np.zeros((4, 4, 3), np.float16)

    monkeypatch.setattr(ep, "_read_exr_frame", slow_read)
    cache = FrameCache(budget_bytes=64 * 1024 * 1024)
    svc = ExrPrefetchService(
        _Seq(),
        cache,
        [1, 2, 3],
        max_workers=1,
        single_frame=True,
        preview_target=(64, 48, 1),
    )
    try:
        svc.request_immediate(1)
        _pump(qapp, started.is_set)
        assert started.is_set()
        svc.set_preview_target(32, 24, 2)  # pane resized mid-read
        release.set()
        svc.request_immediate(2)
        _pump(qapp, lambda: cache.contains(2))
        assert cache.contains(2), "the stale read kept its worker slot"
    finally:
        release.set()
        svc.shutdown()
        # The done callbacks hold the service: let them finish on the worker
        # while this thread still owns it, so the QObject dies on this thread.
        svc._pool.shutdown(wait=True)
        qapp.processEvents()
