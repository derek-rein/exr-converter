"""Background proxy jobs and the GUI watch-folder poller.

:class:`ProxyQueue` runs proxy transcodes one at a time on a worker
:class:`~PySide6.QtCore.QThread`. Batch conversions and the watch folder both
feed it, so a clip dropped into the watch folder mid-batch just joins the line.

:class:`WatchPoller` scans a :class:`~src.core.watch_folder.WatchFolder` on its
own thread so a slow network share never stalls the window.
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from ..core.errors import ConversionCancelled


class ProxyJob(NamedTuple):
    source: str
    output: str
    codec_key: str
    resolution: str
    audio: bool = True
    watermark_text: str = ""
    watermark_strength: float = 1.0


class _ProxyJobWorker(QObject):
    """Runs one :class:`ProxyJob` on a worker thread."""

    progress = Signal(int, int)
    log_message = Signal(str)
    done = Signal(bool, str)  # ok, error ("" on success or cancel)

    def __init__(self, job: ProxyJob) -> None:
        super().__init__()
        self._job = job
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        from ..core.proxy import run_video_to_proxy

        job = self._job
        try:
            run_video_to_proxy(
                job.source,
                job.output,
                codec_key=job.codec_key,
                resolution=job.resolution,
                audio=job.audio,
                watermark_text=job.watermark_text,
                watermark_strength=job.watermark_strength,
                progress=self.progress.emit,
                cancel_check=self._cancel.is_set,
                log=self.log_message.emit,
            )
        except ConversionCancelled:
            self.done.emit(False, "")
        except Exception as e:
            if self._cancel.is_set():
                self.done.emit(False, "")
            else:
                self.done.emit(False, str(e))
        else:
            self.done.emit(True, "")


class ProxyQueue(QObject):
    """Serial queue of proxy jobs (GUI-thread object; work runs off-thread)."""

    progress = Signal(int, int)
    log_message = Signal(str)
    busy_changed = Signal(bool)
    queue_changed = Signal(int)  # jobs waiting + running
    job_finished = Signal(str, str, bool)  # source, output, ok

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: deque[ProxyJob] = deque()
        self._current: ProxyJob | None = None
        self._worker: _ProxyJobWorker | None = None
        self._thread: QThread | None = None
        self._busy = False
        self.completed = 0
        self.failed = 0

    def is_busy(self) -> bool:
        return self._busy

    def pending(self) -> int:
        return len(self._jobs) + (1 if self._current is not None else 0)

    def is_queued(self, output: str) -> bool:
        """True if a job already targets *output* (running or waiting)."""
        key = str(Path(output))
        if self._current is not None and str(Path(self._current.output)) == key:
            return True
        return any(str(Path(j.output)) == key for j in self._jobs)

    def add(self, job: ProxyJob) -> bool:
        if self.is_queued(job.output):
            return False
        self._jobs.append(job)
        self.queue_changed.emit(self.pending())
        self._start_next()
        return True

    def cancel_all(self) -> None:
        """Drop waiting jobs and stop the running one."""
        dropped = len(self._jobs)
        self._jobs.clear()
        if self._worker is not None:
            self._worker.cancel()
        if dropped:
            self.log_message.emit(f"Proxy queue cleared ({dropped} waiting)")
        self.queue_changed.emit(self.pending())

    def shutdown(self, timeout_ms: int = 8000) -> None:
        self._jobs.clear()
        if self._worker is not None:
            self._worker.cancel()
        thr = self._thread
        if thr is not None and thr.isRunning():
            thr.quit()
            thr.wait(timeout_ms)

    def _start_next(self) -> None:
        if self._current is not None or not self._jobs:
            return
        job = self._jobs.popleft()
        self._current = job
        thread = QThread(self)
        worker = _ProxyJobWorker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.progress)
        worker.log_message.connect(self.log_message)
        worker.done.connect(self._on_done)
        worker.done.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        if not self._busy:
            self._busy = True
            self.busy_changed.emit(True)
        thread.start()

    @Slot(bool, str)
    def _on_done(self, ok: bool, err: str) -> None:
        job = self._current
        self._current = None
        self._worker = None
        self._thread = None
        if job is not None:
            name = Path(job.source).name
            if ok:
                self.completed += 1
            elif err:
                self.failed += 1
                self.log_message.emit(f"ERROR: proxy failed for {name}: {err}")
            else:
                self.log_message.emit(f"Proxy cancelled: {name}")
            self.job_finished.emit(job.source, job.output, ok)
        self.queue_changed.emit(self.pending())
        if self._jobs:
            # Defer so the finished thread unwinds before the next one starts.
            QTimer.singleShot(0, self, self._start_next)
        else:
            self._busy = False
            self.busy_changed.emit(False)


class _PollWorker(QObject):
    files_ready = Signal(list)
    settling = Signal(int)

    def __init__(self, watcher, interval_ms: int) -> None:
        super().__init__()
        self._watcher = watcher
        self._interval_ms = interval_ms
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    @Slot()
    def _poll(self) -> None:
        try:
            ready = self._watcher.poll()
        except Exception:
            ready = []
        if ready:
            self.files_ready.emit([str(p) for p in ready])
        self.settling.emit(self._watcher.pending_count)


class WatchPoller(QObject):
    """Polls a :class:`~src.core.watch_folder.WatchFolder` off the GUI thread."""

    files_ready = Signal(list)  # list[str] of new, fully written clips
    settling = Signal(int)  # clips seen but still being copied

    def __init__(self, watcher, interval_ms: int = 2000, parent: QObject | None = None):
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = _PollWorker(watcher, interval_ms)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._thread.finished.connect(self._worker.deleteLater)
        self._worker.files_ready.connect(self.files_ready)
        self._worker.settling.connect(self.settling)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout_ms: int = 5000) -> None:
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(timeout_ms)
