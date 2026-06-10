from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class ConvertWorker(QObject):
    progress = Signal(int, int)
    log_message = Signal(str)
    failed = Signal(str)
    finished_ok = Signal()

    def __init__(self, mode: str, kwargs: dict):
        super().__init__()
        self._mode = mode
        self._kwargs = kwargs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _cancel_check(self) -> bool:
        return self._cancelled

    def _emit_progress(self, cur: int, total: int) -> None:
        self.progress.emit(cur, total)

    def _log(self, msg: str) -> None:
        self.log_message.emit(msg)

    @Slot()
    def run(self) -> None:
        try:
            from .convert import run_exr_to_video, run_video_to_exr

            fn = run_video_to_exr if self._mode == "video2exr" else run_exr_to_video
            fn(
                progress=self._emit_progress,
                cancel_check=self._cancel_check,
                log=self._log,
                **self._kwargs,
            )
        except Exception as e:
            self.log_message.emit(f"ERROR: {e}")
            self.failed.emit(str(e))
        else:
            self.log_message.emit("Conversion complete.")
            self.finished_ok.emit()
