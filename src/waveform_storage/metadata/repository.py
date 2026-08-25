"""Driver-independent contract for the waveform metadata catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from waveform_storage.metadata.models import (
    CaptureTaskMetadata,
    RegistrySnapshotMetadata,
    WaveformFileMetadata,
)


class MetadataRepositoryError(RuntimeError):
    """Base error for metadata persistence failures."""


class MetadataRecordNotFound(MetadataRepositoryError):
    """Raised when an update targets an unknown metadata record."""


class MetadataRepository(Protocol):
    def ensure_indexes(self) -> None: ...

    def ping(self) -> None: ...

    def upsert_task(self, task: CaptureTaskMetadata) -> None: ...

    def update_task_status(
        self,
        request_id: str,
        status: str,
        *,
        timestamp: datetime,
        message: str | None = None,
        error_code: str | None = None,
        result_summary: Mapping[str, int] | None = None,
    ) -> None: ...

    def upsert_waveform_file(self, record: WaveformFileMetadata) -> None: ...

    def upsert_registry_snapshot(self, snapshot: RegistrySnapshotMetadata) -> None: ...

    def list_capture_times(self, storage_date_local: str) -> tuple[str, ...]: ...

    def find_waveform_files(
        self,
        *,
        storage_date_local: str,
        time_sec: str | None = None,
        device_ids: Sequence[str] = (),
        captured_from: datetime | None = None,
        captured_to: datetime | None = None,
    ) -> tuple[Mapping[str, Any], ...]: ...

    def close(self) -> None: ...
