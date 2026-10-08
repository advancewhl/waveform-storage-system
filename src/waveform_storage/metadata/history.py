"""Qt-free task and segment summaries for completed catalog files."""

from dataclasses import asdict, dataclass
import math
import re
from datetime import date
from datetime import datetime, timezone
from typing import Mapping, Sequence


def aware_datetime(value: datetime) -> datetime:
    """Mongo's default naive dates represent UTC, never the client's local zone."""
    if not isinstance(value, datetime):
        raise ValueError("capture timestamps must be datetime values")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class CaptureRunSummary:
    request_id: str
    started_at: datetime
    first_capture_at: datetime
    last_capture_at: datetime
    frequency_hz: float | None
    duration_seconds: int | None
    status: str
    segment_count: int
    requested_device_count: int | None
    available_device_count: int
    has_task_metadata: bool

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("request_id must be a nonempty string")
        if not isinstance(self.status, str) or not self.status:
            raise ValueError("status must be a nonempty string")
        if not isinstance(self.has_task_metadata, bool):
            raise ValueError("has_task_metadata must be boolean")
        for value in (self.segment_count, self.available_device_count):
            if type(value) is not int or value < 0:
                raise ValueError("capture counts must be nonnegative integers")
        for value in (self.duration_seconds, self.requested_device_count):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("optional capture counts must be nonnegative integers")
        if self.frequency_hz is not None and (
            isinstance(self.frequency_hz, bool) or not isinstance(self.frequency_hz, (int, float))
            or not math.isfinite(self.frequency_hz) or self.frequency_hz <= 0
        ):
            raise ValueError("frequency_hz must be a positive finite number")
        for key in ("started_at", "first_capture_at", "last_capture_at"):
            object.__setattr__(self, key, aware_datetime(getattr(self, key)))

    def to_payload(self) -> dict:
        return {key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in asdict(self).items()}

    @classmethod
    def from_payload(cls, payload: Mapping):
        values = dict(payload)
        for key in ("started_at", "first_capture_at", "last_capture_at"):
            values[key] = datetime.fromisoformat(values[key])
        return cls(**values)


@dataclass(frozen=True)
class CaptureSegment:
    request_id: str
    capture_sequence: int
    time_sec: str
    captured_at: datetime
    device_ids: tuple[str, ...]
    storage_date_local: str | None = None

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("request_id must be a nonempty string")
        if type(self.capture_sequence) is not int or self.capture_sequence < 0:
            raise ValueError("capture_sequence must be a nonnegative integer")
        if not isinstance(self.time_sec, str) or not re.fullmatch(r"[0-9]{6}", self.time_sec):
            raise ValueError("time_sec must contain six digits")
        if not isinstance(self.device_ids, (tuple, list)) or not self.device_ids or any(
            not isinstance(item, str) or not item for item in self.device_ids
        ) or len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("device_ids must contain unique device strings")
        object.__setattr__(self, "device_ids", tuple(self.device_ids))
        if self.storage_date_local is not None:
            date.fromisoformat(self.storage_date_local)
        object.__setattr__(self, "captured_at", aware_datetime(self.captured_at))

    def to_payload(self) -> dict:
        values = asdict(self)
        values["captured_at"] = self.captured_at.isoformat()
        values["device_ids"] = list(self.device_ids)
        return values

    @classmethod
    def from_payload(cls, payload: Mapping):
        values = dict(payload)
        values["captured_at"] = datetime.fromisoformat(values["captured_at"])
        if not isinstance(values["device_ids"], list):
            raise ValueError("device_ids must be a JSON list")
        values["device_ids"] = tuple(values["device_ids"])
        return cls(**values)


def summarize_runs(records: Sequence[Mapping], tasks: Mapping) -> tuple[CaptureRunSummary, ...]:
    groups = {}
    for record in records:
        groups.setdefault(record["request_id"], []).append(record)
    runs = []
    for request_id, files in groups.items():
        task = tasks.get(request_id)
        first = min(item["captured_at"] for item in files)
        last = max(item["captured_at"] for item in files)
        runs.append(CaptureRunSummary(
            request_id, (task.get("started_at") or first) if task else first,
            first, last, task.get("frequency_hz") if task else None,
            task.get("duration_seconds") if task else None,
            task["status"] if task else "historical",
            len({item.get("capture_sequence", 0) for item in files}),
            len(task["device_ids"]) if task else None,
            len({item["device_id"] for item in files}), task is not None,
        ))
    return tuple(sorted(runs, key=lambda item: (item.first_capture_at, item.request_id),
                        reverse=True))


def summarize_segments(records: Sequence[Mapping]) -> tuple[CaptureSegment, ...]:
    groups = {}
    for record in records:
        groups.setdefault(record.get("capture_sequence", 0), []).append(record)
    segments = []
    for sequence, files in sorted(groups.items()):
        first = min(files, key=lambda item: (item["captured_at"], item["device_id"]))
        segments.append(CaptureSegment(
            first["request_id"], sequence, first["time_sec"], first["captured_at"],
            tuple(sorted({item["device_id"] for item in files})), first["storage_date_local"],
        ))
    return tuple(segments)
