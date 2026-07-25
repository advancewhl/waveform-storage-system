"""Qt-widget-independent GUI task state and query coordination."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskState(str, Enum):
    IDLE = "IDLE"
    RETRIEVING = "RETRIEVING"
    DOWNLOADING = "DOWNLOADING"
    ALIGNING = "ALIGNING"
    READY = "READY"
    EXPORTING = "EXPORTING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class QueryParameters:
    remote_path: str
    run_id: str
    time_sec: str
    device_ids: tuple[str, ...]


class GuiController:
    """Own task state and results without knowing concrete Qt controls."""

    def __init__(
        self,
        *,
        retrieve_action=None,
        align_action=None,
        export_action=None,
        raw_download_action=None,
    ) -> None:
        self.state = TaskState.IDLE
        self.query = None
        self.current_result = None
        self.user_status = ""
        self.error = None
        self._retrieve_action = retrieve_action
        self._align_action = align_action
        self._export_action = export_action
        self._raw_download_action = raw_download_action

    def retrieve_times(self, query):
        if self._retrieve_action is None:
            raise RuntimeError("retrieve action is not configured")
        return self._retrieve_action(query)

    def align(self, query):
        if self._align_action is None:
            raise RuntimeError("align action is not configured")
        return self._align_action(query)

    def export(self, result, query, directory, export_format):
        if self._export_action is None:
            raise RuntimeError("export action is not configured")
        return self._export_action(
            result,
            query,
            directory,
            export_format,
        )

    def download_raw(self, query, local_path):
        if self._raw_download_action is None:
            raise RuntimeError("raw download action is not configured")
        return self._raw_download_action(query, local_path)

    def begin(self, state: TaskState, query: QueryParameters | None = None):
        if state not in {
            TaskState.RETRIEVING,
            TaskState.DOWNLOADING,
            TaskState.ALIGNING,
            TaskState.EXPORTING,
        }:
            raise ValueError(f"{state} is not a running state")
        if self.state in {
            TaskState.RETRIEVING,
            TaskState.DOWNLOADING,
            TaskState.ALIGNING,
            TaskState.EXPORTING,
        }:
            raise RuntimeError(f"task already running in {self.state}")
        self.state = state
        self.query = query
        self.user_status = state.value
        self.error = None

    def complete(self, result=None, *, ready=False):
        if result is not None:
            self.current_result = result
        self.state = TaskState.READY if ready else TaskState.IDLE
        self.user_status = self.state.value
        self.error = None

    def fail(self, error):
        self.state = TaskState.FAILED
        self.error = error
        self.user_status = str(error)

    def cancel(self):
        self.state = TaskState.CANCELLED
        self.user_status = TaskState.CANCELLED.value
