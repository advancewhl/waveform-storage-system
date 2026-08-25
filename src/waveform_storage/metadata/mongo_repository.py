"""MongoDB implementation of the independent metadata repository.

Importing this module does not require PyMongo.  The driver is imported only
when ``connect`` is called, so offline tests and deployments with metadata
disabled retain the existing dependency boundary.
"""

from __future__ import annotations

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
from waveform_storage.metadata.settings import MongoSettings


class MongoDriverUnavailable(MetadataRepositoryError):
    pass


class MongoMetadataRepository:
    TASKS = "capture_tasks"
    FILES = "waveform_files"
    REGISTRIES = "device_registry_versions"

    def __init__(self, database, *, client=None) -> None:
        self.database = database
        self.client = client
        self.tasks = database[self.TASKS]
        self.files = database[self.FILES]
        self.registries = database[self.REGISTRIES]

    @classmethod
    def connect(cls, settings: MongoSettings) -> "MongoMetadataRepository":
        if not settings.enabled or not settings.uri:
            raise MetadataRepositoryError("MongoDB metadata is not enabled")
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise MongoDriverUnavailable(
                "PyMongo is required when MongoDB metadata is enabled"
            ) from exc
        client = MongoClient(
            settings.uri,
            serverSelectionTimeoutMS=settings.server_selection_timeout_ms,
            connect=False,
            appname="site-user-waveform-storage",
        )
        return cls(client[settings.database], client=client)

    def ensure_indexes(self) -> None:
        self.tasks.create_index(
            [("status", 1), ("created_at", -1)],
            name="task_status_created",
        )
        self.tasks.create_index(
            [("device_ids", 1), ("created_at", -1)],
            name="task_device_created",
        )
        self.tasks.create_index(
            [("area_ids", 1), ("created_at", -1)],
            name="task_area_created",
        )
        self.files.create_index(
            [("request_id", 1), ("device_id", 1)],
            unique=True,
            name="file_request_device_unique",
        )
        self.files.create_index(
            [("device_id", 1), ("captured_at", -1)],
            name="file_device_captured",
        )
        self.files.create_index(
            [("storage_date_local", 1), ("time_sec", 1), ("captured_at", 1)],
            name="file_local_date_time_captured",
        )
        self.files.create_index(
            [("file.storage_id", 1), ("file.path", 1)],
            unique=True,
            name="file_storage_path_unique",
        )
        self.files.create_index(
            [("status", 1), ("captured_at", -1)],
            name="file_status_captured",
        )
        self.registries.create_index(
            [("created_at", -1)],
            name="registry_created",
        )

    def ping(self) -> None:
        self.database.command("ping")

    def upsert_task(self, task: CaptureTaskMetadata) -> None:
        self.tasks.replace_one(
            {"_id": task.request_id},
            task.to_document(),
            upsert=True,
        )

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
        if status not in TASK_STATUSES:
            raise MetadataRepositoryError(f"unsupported task status: {status!r}")
        if timestamp.tzinfo is None:
            raise MetadataRepositoryError("status timestamp must be timezone-aware")
        set_fields: dict[str, Any] = {"status": status}
        if status == "running":
            set_fields["started_at"] = timestamp
        if status in {"succeeded", "failed", "cancelled", "rejected"}:
            set_fields["completed_at"] = timestamp
        if result_summary is not None:
            set_fields["result_summary"] = dict(result_summary)
        set_fields["error"] = (
            {"code": error_code, "message": message}
            if error_code is not None
            else None
        )
        event = {
            "status": status,
            "timestamp": timestamp,
            "message": message,
            "error_code": error_code,
        }
        result = self.tasks.update_one(
            {"_id": request_id},
            {"$set": set_fields, "$push": {"status_history": event}},
        )
        if result.matched_count == 0:
            raise MetadataRecordNotFound(f"capture task not found: {request_id}")

    def upsert_waveform_file(self, record: WaveformFileMetadata) -> None:
        self.files.replace_one(
            {"_id": record.record_id},
            record.to_document(),
            upsert=True,
        )

    def upsert_registry_snapshot(self, snapshot: RegistrySnapshotMetadata) -> None:
        self.registries.replace_one(
            {"_id": snapshot.registry_id},
            snapshot.to_document(),
            upsert=True,
        )

    def list_capture_times(self, storage_date_local: str) -> tuple[str, ...]:
        values = self.files.distinct(
            "time_sec",
            {
                "storage_date_local": storage_date_local,
                "status": "completed",
            },
        )
        return tuple(sorted(str(value) for value in values))

    def find_waveform_files(
        self,
        *,
        storage_date_local: str,
        time_sec: str | None = None,
        device_ids: Sequence[str] = (),
        captured_from: datetime | None = None,
        captured_to: datetime | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        query: dict[str, Any] = {
            "storage_date_local": storage_date_local,
            "status": "completed",
        }
        if device_ids:
            query["device_id"] = {"$in": list(device_ids)}
        if time_sec is not None:
            query["time_sec"] = time_sec
        captured: dict[str, datetime] = {}
        if captured_from is not None:
            captured["$gte"] = captured_from
        if captured_to is not None:
            captured["$lt"] = captured_to
        if captured:
            query["captured_at"] = captured
        cursor = self.files.find(query).sort(
            [("captured_at", 1), ("device_id", 1)]
        )
        return tuple(cursor)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
