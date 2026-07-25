"""Reusable background worker with safe-boundary cancellation."""

from __future__ import annotations

import traceback

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot


class WorkerSignals(QObject):
    started = pyqtSignal()
    progress = pyqtSignal(int)
    completed = pyqtSignal(object)
    failed = pyqtSignal(object)
    cancelled = pyqtSignal()


class BackgroundWorker(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    @pyqtSlot()
    def run(self):
        if self._cancel_requested:
            self.signals.cancelled.emit()
            return
        self.signals.started.emit()
        self.signals.progress.emit(0)
        try:
            result = self.operation()
        except Exception as exc:
            exc.worker_traceback = traceback.format_exc()
            self.signals.failed.emit(exc)
            return
        if self._cancel_requested:
            self.signals.cancelled.emit()
            return
        self.signals.progress.emit(100)
        self.signals.completed.emit(result)
