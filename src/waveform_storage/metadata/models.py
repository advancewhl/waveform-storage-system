"""Validated, driver-independent metadata records.

Waveform arrays remain in HDF5.  These records contain only task provenance,
file locations, compact shape/type descriptions, and registry snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence


TASK_STATUSES = frozenset(
    {"accepted", "running", "succeeded", "failed", "cancelled", "rejected"}
)
FILE_STATUSES = frozenset({"completed", "failed"})
SENSITIVE_REGISTRY_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "private_key",
    "credential",
    "mongodb_uri",
)


class MetadataValidationError(ValueError):
    """Raised when a metadata record is incomplete or ambiguous."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MetadataValidationError(f"{name} must be timezone-aware")
    return value


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MetadataValidationError(f"{name} must be a non-empty string")
    return value.strip()


def _unique_strings(values: Sequence[str], name: str) -> tuple[str, ...]:
    normalized = tuple(_identifier(value, name) for value in values)
    if not normalized:
        raise MetadataValidationError(f"{name} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise MetadataValidationError(f"{name} contains duplicates")
    return normalized


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in SENSITIVE_REGISTRY_KEY_PARTS):
                return True
            if _contains_sensitive_key(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


@dataclass(frozen=True)
class StatusEvent:
    status: str
    timestamp: datetime = field(default_factory=utc_now)
    message: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in TASK_STATUSES:
            raise MetadataValidationError(f"unsupported task status: {self.status!r}")
        _aware(self.timestamp, "timestamp")

    def to_document(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "timestamp": self.timestamp,
            "message": self.message,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class CaptureTaskMetadata:
    request_id: str
    frequency_hz: float
    duration_seconds: int
    area_ids: tuple[str, ...]
    device_ids: tuple[str, ...]
    registry_id: str
    registry_schema_version: int
    status: str = "accepted"
    status_history: tuple[StatusEvent, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    succeeded_devices: int = 0
    failed_devices: int = 0
    protocol_version: int = 2
    software_version: str | None = None
    git_commit: str | None = None
    error: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        object.__setattr__(self, "registry_id", _identifier(self.registry_id, "registry_id"))
        object.__setattr__(self, "area_ids", _unique_strings(self.area_ids, "area_ids"))
        object.__setattr__(self, "device_ids", _unique_strings(self.device_ids, "device_ids"))
        if not isfinite(float(self.frequency_hz)) or self.frequency_hz <= 0:
            raise MetadataValidationError("frequency_hz must be finite and positive")
        if self.duration_seconds <= 0:
            raise MetadataValidationError("duration_seconds must be positive")
        if self.registry_schema_version <= 0 or self.protocol_version <= 0:
            raise MetadataValidationError("schema and protocol versions must be positive")
        if self.status not in TASK_STATUSES:
            raise MetadataValidationError(f"unsupported task status: {self.status!r}")
        _aware(self.created_at, "created_at")
        if self.started_at is not None:
            _aware(self.started_at, "started_at")
        if self.completed_at is not None:
            _aware(self.completed_at, "completed_at")
        if self.succeeded_devices < 0 or self.failed_devices < 0:
            raise MetadataValidationError("device result counts must not be negative")
        if self.succeeded_devices + self.failed_devices > len(self.device_ids):
            raise MetadataValidationError("device result counts exceed requested devices")
        if self.status_history and self.status_history[-1].status != self.status:
            raise MetadataValidationError("last status event must match current status")

    def to_document(self) -> dict[str, Any]:
        history = self.status_history or (
            StatusEvent(self.status, self.created_at),
        )
        return {
            "_id": self.request_id,
            "schema_version": 1,
            "command": "start_capture",
            "frequency_hz": float(self.frequency_hz),
            "duration_seconds": int(self.duration_seconds),
            "area_ids": list(self.area_ids),
            "device_ids": list(self.device_ids),
            "registry": {
                "registry_id": self.registry_id,
                "schema_version": self.registry_schema_version,
            },
            "status": self.status,
            "status_history": [item.to_document() for item in history],
            "result_summary": {
                "requested": len(self.device_ids),
                "succeeded": self.succeeded_devices,
                "failed": self.failed_devices,
            },
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "software": {
                "version": self.software_version,
                "git_commit": self.git_commit,
                "protocol_version": self.protocol_version,
            },
            "error": dict(self.error) if self.error is not None else None,
        }


@dataclass(frozen=True)
class WaveformFileMetadata:
    request_id: str
    device_id: str
    capture_pv: str
    captured_at: datetime
    storage_date_local: str
    time_sec: str
    timezone_name: str
    storage_id: str
    path: str
    sample_count: int
    bunch_id_shape: tuple[int, ...]
    data_shape: tuple[int, ...]
    size_bytes: int
    status: str = "completed"
    file_schema_name: str = "site-user-waveform-raw"
    file_schema_version: int = 1
    bunch_id_dtype: str = "uint64"
    data_dtype: str = "int64"
    sha256: str | None = None
    x_unit: str | None = None
    y_unit: str | None = None
    scale_factor: float | None = None
    created_at: datetime = field(default_factory=utc_now)
    error: Mapping[str, Any] | None = None
    capture_sequence: int = 0

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "device_id",
            "capture_pv",
            "storage_date_local",
            "time_sec",
            "timezone_name",
            "storage_id",
            "path",
            "file_schema_name",
            "bunch_id_dtype",
            "data_dtype",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        _aware(self.captured_at, "captured_at")
        _aware(self.created_at, "created_at")
        if len(self.time_sec) != 6 or not self.time_sec.isdigit():
            raise MetadataValidationError("time_sec must contain exactly six digits")
        if self.status not in FILE_STATUSES:
            raise MetadataValidationError(f"unsupported file status: {self.status!r}")
        if self.file_schema_version <= 0:
            raise MetadataValidationError("file_schema_version must be positive")
        if self.sample_count < 0 or self.size_bytes < 0:
            raise MetadataValidationError("sample_count and size_bytes must not be negative")
        if any(value < 0 for value in self.bunch_id_shape + self.data_shape):
            raise MetadataValidationError("dataset shapes must not contain negative values")
        if self.scale_factor is not None and self.scale_factor <= 0:
            raise MetadataValidationError("scale_factor must be positive or null")
        if self.capture_sequence < 0:
            raise MetadataValidationError("capture_sequence must not be negative")

    @property
    def record_id(self) -> str:
        if self.capture_sequence == 0:
            return f"{self.request_id}:{self.device_id}"
        return f"{self.request_id}:{self.device_id}:S{self.capture_sequence:05d}"

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.record_id,
            "schema_version": 1,
            "request_id": self.request_id,
            "device_id": self.device_id,
            "capture_pv": self.capture_pv,
            "capture_sequence": self.capture_sequence,
            "status": self.status,
            "captured_at": self.captured_at,
            "storage_date_local": self.storage_date_local,
            "time_sec": self.time_sec,
            "timezone": self.timezone_name,
            "file": {
                "storage_id": self.storage_id,
                "path": self.path,
                "format": "HDF5",
                "schema_name": self.file_schema_name,
                "schema_version": self.file_schema_version,
                "size_bytes": self.size_bytes,
                "sha256": self.sha256,
            },
            "datasets": {
                "BunchIDData": {
                    "dtype": self.bunch_id_dtype,
                    "shape": list(self.bunch_id_shape),
                },
                "Data": {
                    "dtype": self.data_dtype,
                    "shape": list(self.data_shape),
                },
            },
            "sample_count": self.sample_count,
            "x_unit": self.x_unit,
            "y_unit": self.y_unit,
            "scale_factor": self.scale_factor,
            "created_at": self.created_at,
            "error": dict(self.error) if self.error is not None else None,
        }


@dataclass(frozen=True)
class RegistrySnapshotMetadata:
    registry_id: str
    registry_schema_version: int
    description: str
    devices: tuple[Mapping[str, Any], ...]
    acquisition_enabled_count: int
    source_filename: str
    created_at: datetime = field(default_factory=utc_now)
    git_commit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "registry_id", _identifier(self.registry_id, "registry_id"))
        object.__setattr__(
            self,
            "source_filename",
            _identifier(self.source_filename, "source_filename"),
        )
        _aware(self.created_at, "created_at")
        if self.registry_schema_version <= 0:
            raise MetadataValidationError("registry_schema_version must be positive")
        if not self.devices:
            raise MetadataValidationError("devices must not be empty")
        if _contains_sensitive_key(self.devices):
            raise MetadataValidationError(
                "registry snapshot contains a credential-like field"
            )
        if not 0 <= self.acquisition_enabled_count <= len(self.devices):
            raise MetadataValidationError("invalid acquisition_enabled_count")

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.registry_id,
            "schema_version": 1,
            "registry_schema_version": self.registry_schema_version,
            "description": self.description,
            "device_count": len(self.devices),
            "acquisition_enabled_count": self.acquisition_enabled_count,
            "created_at": self.created_at,
            "source": {
                "filename": self.source_filename,
                "git_commit": self.git_commit,
            },
            "devices": [dict(item) for item in self.devices],
        }
