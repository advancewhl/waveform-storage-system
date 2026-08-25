"""Deterministic in-memory metadata repository for tests and demo wiring."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from waveform_storage.metadata.models import (
    TASK_STATUSES,
    CaptureTaskMetadata,
    RegistrySnapshotMetadata,
    WaveformFileMetadata,
)
from waveform_storage.metadata.repository import (
    MetadataRecordNotFound,
    MetadataRepositoryError,
)


class InMemoryMetadataRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.files: dict[str, dict[str, Any]] = {}
        self.registries: dict[str, dict[str, Any]] = {}

    def ensure_indexes(self) -> None:
        return None

    def ping(self) -> None:
        return None

    def upsert_task(self, task: CaptureTaskMetadata) -> None:
        self.tasks[task.request_id] = deepcopy(task.to_document())

    def update_task_status(
        self,
        request_id: str,
        status: str,
        *,
        timestamp: datetime,
        message: str | None = None,
        error_code: str | None = None,
        result_summary: Mapping[str, int] | None = None,
    ) -> None:
        if request_id not in self.tasks:
            raise MetadataRecordNotFound(f"capture task not found: {request_id}")
        if status not in TASK_STATUSES:
            raise MetadataRepositoryError(f"unsupported task status: {status!r}")
        if timestamp.tzinfo is None:
            raise MetadataRepositoryError("status timestamp must be timezone-aware")
        record = self.tasks[request_id]
        record["status"] = status
        record["status_history"].append(
            {
                "status": status,
                "timestamp": timestamp,
                "message": message,
                "error_code": error_code,
            }
        )
        if status == "running":
            record["started_at"] = timestamp
        if status in {"succeeded", "failed", "cancelled", "rejected"}:
            record["completed_at"] = timestamp
        if result_summary is not None:
            record["result_summary"] = dict(result_summary)
        record["error"] = (
            {"code": error_code, "message": message}
            if error_code is not None
            else None
        )

    def upsert_waveform_file(self, record: WaveformFileMetadata) -> None:
        self.files[record.record_id] = deepcopy(record.to_document())

    def upsert_registry_snapshot(self, snapshot: RegistrySnapshotMetadata) -> None:
        self.registries[snapshot.registry_id] = deepcopy(snapshot.to_document())

    def list_capture_times(self, storage_date_local: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    str(record["time_sec"])
                    for record in self.files.values()
                    if record["storage_date_local"] == storage_date_local
                    and record["status"] == "completed"
                }
            )
        )

    def find_waveform_files(
        self,
        *,
        storage_date_local: str,
        time_sec: str | None = None,
        device_ids: Sequence[str] = (),
        captured_from: datetime | None = None,
        captured_to: datetime | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        allowed = set(device_ids)
        records = []
        for record in self.files.values():
            captured_at = record["captured_at"]
            if record["storage_date_local"] != storage_date_local:
                continue
            if time_sec is not None and record["time_sec"] != time_sec:
                continue
            if record["status"] != "completed":
                continue
            if allowed and record["device_id"] not in allowed:
                continue
            if captured_from is not None and captured_at < captured_from:
                continue
            if captured_to is not None and captured_at >= captured_to:
                continue
            records.append(deepcopy(record))
        records.sort(key=lambda item: (item["captured_at"], item["device_id"]))
        return tuple(records)

    def close(self) -> None:
        return None
