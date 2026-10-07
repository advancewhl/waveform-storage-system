"""Version-2 acquisition protocol shared by the candidate client and server."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from numbers import Integral, Real
from typing import Literal, Mapping, Sequence
from uuid import UUID


PROTOCOL_VERSION = 2
START_CAPTURE = "start_capture"
GET_STATUS = "get_status"
HEALTH = "health"
CANCEL = "cancel"
GET_TIMING_FREQUENCY = "get_timing_frequency"
LIST_CAPTURE_TIMES = "list_capture_times"
FIND_WAVEFORM_FILES = "find_waveform_files"
DEVICE_ID_PATTERN = re.compile(r"^[A-Z0-9_]+$")
LOCAL_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_FIELD_PATTERN = re.compile(r"^\d{6}$")

TaskStatus = Literal[
    "accepted",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "rejected",
]

_REQUEST_FIELDS = {
    "version",
    "request_id",
    "command",
    "frequency_hz",
    "duration_seconds",
    "area_ids",
    "devices",
}
_CONTROL_REQUEST_FIELDS = {
    "version",
    "request_id",
    "command",
}
_LIST_CAPTURE_TIMES_FIELDS = {
    "version",
    "request_id",
    "command",
    "storage_date_local",
}
_FIND_WAVEFORM_FILES_FIELDS = {
    "version",
    "request_id",
    "command",
    "storage_date_local",
    "time_sec",
    "devices",
}
_RESPONSE_FIELDS = {
    "version",
    "request_id",
    "accepted",
    "status",
    "message",
    "error_code",
}
_METADATA_RESPONSE_FIELDS = {
    "version",
    "request_id",
    "accepted",
    "message",
    "error_code",
    "payload",
}
_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    "accepted": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "rejected": frozenset(),
}


class ProtocolValidationError(ValueError):
    """Raised when a candidate protocol message is unsafe or ambiguous."""


@dataclass(frozen=True)
class CaptureRequest:
    version: int
    request_id: str
    command: Literal["start_capture"]
    frequency_hz: float
    duration_seconds: int
    area_ids: tuple[str, ...]
    devices: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_version(self.version)
        _validate_request_id(self.request_id)
        if self.command != START_CAPTURE:
            raise ProtocolValidationError(
                f"unsupported command: {self.command!r}"
            )
        if (
            isinstance(self.frequency_hz, bool)
            or not isinstance(self.frequency_hz, Real)
            or not math.isfinite(float(self.frequency_hz))
            or float(self.frequency_hz) <= 0
        ):
            raise ProtocolValidationError(
                "frequency_hz must be a finite positive number"
            )
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, Integral)
            or int(self.duration_seconds) <= 0
        ):
            raise ProtocolValidationError(
                "duration_seconds must be a positive integer"
            )
        if not self.devices:
            raise ProtocolValidationError("devices must not be empty")
        if not self.area_ids:
            raise ProtocolValidationError("area_ids must not be empty")
        if len(set(self.area_ids)) != len(self.area_ids):
            raise ProtocolValidationError("area_ids contains duplicates")
        if len(set(self.devices)) != len(self.devices):
            raise ProtocolValidationError("devices contains duplicates")
        invalid_areas = [
            item
            for item in self.area_ids
            if not isinstance(item, str)
            or not DEVICE_ID_PATTERN.fullmatch(item)
        ]
        if invalid_areas:
            raise ProtocolValidationError(
                f"area_ids contains invalid IDs: {invalid_areas!r}"
            )
        invalid = [
            item
            for item in self.devices
            if not isinstance(item, str)
            or not DEVICE_ID_PATTERN.fullmatch(item)
        ]
        if invalid:
            raise ProtocolValidationError(
                f"devices contains invalid IDs: {invalid!r}"
            )


@dataclass(frozen=True)
class ControlRequest:
    """A health probe or a status query for ``request_id``."""

    version: int
    request_id: str
    command: Literal["get_status", "health", "cancel", "get_timing_frequency"]

    def __post_init__(self) -> None:
        _validate_version(self.version)
        _validate_request_id(self.request_id)
        if self.command not in {GET_STATUS, HEALTH, CANCEL, GET_TIMING_FREQUENCY}:
            raise ProtocolValidationError(
                f"unsupported command: {self.command!r}"
            )


@dataclass(frozen=True)
class MetadataQueryRequest:
    version: int
    request_id: str
    command: Literal["list_capture_times", "find_waveform_files"]
    storage_date_local: str
    time_sec: str | None = None
    devices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_version(self.version)
        _validate_request_id(self.request_id)
        if self.command not in {LIST_CAPTURE_TIMES, FIND_WAVEFORM_FILES}:
            raise ProtocolValidationError(
                f"unsupported metadata query command: {self.command!r}"
            )
        if not isinstance(self.storage_date_local, str) or not LOCAL_DATE_PATTERN.fullmatch(
            self.storage_date_local
        ):
            raise ProtocolValidationError(
                "storage_date_local must use YYYY-MM-DD"
            )
        try:
            date.fromisoformat(self.storage_date_local)
        except ValueError as exc:
            raise ProtocolValidationError(
                "storage_date_local is not a valid calendar date"
            ) from exc
        if self.command == LIST_CAPTURE_TIMES:
            if self.time_sec is not None or self.devices:
                raise ProtocolValidationError(
                    "list_capture_times does not accept time_sec or devices"
                )
            return
        if not isinstance(self.time_sec, str) or not TIME_FIELD_PATTERN.fullmatch(
            self.time_sec
        ):
            raise ProtocolValidationError("time_sec must contain six digits")
        if not self.devices:
            raise ProtocolValidationError("devices must not be empty")
        if len(set(self.devices)) != len(self.devices):
            raise ProtocolValidationError("devices contains duplicates")
        invalid = [
            item
            for item in self.devices
            if not isinstance(item, str)
            or not DEVICE_ID_PATTERN.fullmatch(item)
        ]
        if invalid:
            raise ProtocolValidationError(
                f"devices contains invalid IDs: {invalid!r}"
            )


@dataclass(frozen=True)
class CaptureResponse:
    version: int
    request_id: str
    accepted: bool
    status: TaskStatus
    message: str
    error_code: str | None = None

    def __post_init__(self) -> None:
        _validate_version(self.version)
        _validate_request_id(self.request_id)
        if self.status not in _ALLOWED_TRANSITIONS:
            raise ProtocolValidationError(
                f"unsupported task status: {self.status!r}"
            )
        if not isinstance(self.accepted, bool):
            raise ProtocolValidationError("accepted must be boolean")
        if self.accepted == (self.status == "rejected"):
            raise ProtocolValidationError(
                "rejected responses must have accepted=false and all other "
                "statuses must have accepted=true"
            )
        if not isinstance(self.message, str):
            raise ProtocolValidationError("message must be a string")
        if self.error_code is not None and (
            not isinstance(self.error_code, str) or not self.error_code.strip()
        ):
            raise ProtocolValidationError(
                "error_code must be a non-empty string or null"
            )
        if self.status in {"failed", "rejected"} and self.error_code is None:
            raise ProtocolValidationError(
                f"{self.status} response requires error_code"
            )


@dataclass(frozen=True)
class MetadataQueryResponse:
    version: int
    request_id: str
    accepted: bool
    message: str
    payload: Mapping[str, object]
    error_code: str | None = None

    def __post_init__(self) -> None:
        _validate_version(self.version)
        _validate_request_id(self.request_id)
        if not isinstance(self.accepted, bool):
            raise ProtocolValidationError("accepted must be boolean")
        if not isinstance(self.message, str):
            raise ProtocolValidationError("message must be a string")
        if not isinstance(self.payload, Mapping):
            raise ProtocolValidationError("payload must be an object")
        if self.accepted and self.error_code is not None:
            raise ProtocolValidationError(
                "accepted metadata responses must not contain error_code"
            )
        if not self.accepted and not self.error_code:
            raise ProtocolValidationError(
                "rejected metadata responses require error_code"
            )


def encode_request(request: CaptureRequest) -> str:
    payload = asdict(request)
    payload["area_ids"] = list(request.area_ids)
    payload["devices"] = list(request.devices)
    return _encode(payload)


def decode_request(message: str | bytes) -> CaptureRequest:
    payload = _decode_object(message)
    _require_exact_fields(payload, _REQUEST_FIELDS)
    devices = payload["devices"]
    area_ids = payload["area_ids"]
    if not isinstance(devices, list):
        raise ProtocolValidationError("devices must be a JSON array")
    if not isinstance(area_ids, list):
        raise ProtocolValidationError("area_ids must be a JSON array")
    return CaptureRequest(
        version=payload["version"],
        request_id=payload["request_id"],
        command=payload["command"],
        frequency_hz=payload["frequency_hz"],
        duration_seconds=payload["duration_seconds"],
        area_ids=tuple(area_ids),
        devices=tuple(devices),
    )


def encode_control_request(request: ControlRequest) -> str:
    return _encode(asdict(request))


def encode_metadata_query_request(request: MetadataQueryRequest) -> str:
    payload = asdict(request)
    if request.command == LIST_CAPTURE_TIMES:
        payload.pop("time_sec")
        payload.pop("devices")
    else:
        payload["devices"] = list(request.devices)
    return _encode(payload)


def decode_service_request(
    message: str | bytes,
) -> CaptureRequest | ControlRequest | MetadataQueryRequest:
    """Decode any request understood by the deployable v2 service."""

    payload = _decode_object(message)
    command = payload.get("command")
    if command == START_CAPTURE:
        _require_exact_fields(payload, _REQUEST_FIELDS)
        devices = payload["devices"]
        area_ids = payload["area_ids"]
        if not isinstance(devices, list):
            raise ProtocolValidationError("devices must be a JSON array")
        if not isinstance(area_ids, list):
            raise ProtocolValidationError("area_ids must be a JSON array")
        return CaptureRequest(
            version=payload["version"],
            request_id=payload["request_id"],
            command=payload["command"],
            frequency_hz=payload["frequency_hz"],
            duration_seconds=payload["duration_seconds"],
            area_ids=tuple(area_ids),
            devices=tuple(devices),
        )
    if command in {GET_STATUS, HEALTH, CANCEL, GET_TIMING_FREQUENCY}:
        _require_exact_fields(payload, _CONTROL_REQUEST_FIELDS)
        return ControlRequest(
            version=payload["version"],
            request_id=payload["request_id"],
            command=payload["command"],
        )
    if command == LIST_CAPTURE_TIMES:
        _require_exact_fields(payload, _LIST_CAPTURE_TIMES_FIELDS)
        return MetadataQueryRequest(
            version=payload["version"],
            request_id=payload["request_id"],
            command=payload["command"],
            storage_date_local=payload["storage_date_local"],
        )
    if command == FIND_WAVEFORM_FILES:
        _require_exact_fields(payload, _FIND_WAVEFORM_FILES_FIELDS)
        devices = payload["devices"]
        if not isinstance(devices, list):
            raise ProtocolValidationError("devices must be a JSON array")
        return MetadataQueryRequest(
            version=payload["version"],
            request_id=payload["request_id"],
            command=payload["command"],
            storage_date_local=payload["storage_date_local"],
            time_sec=payload["time_sec"],
            devices=tuple(devices),
        )
    raise ProtocolValidationError(f"unsupported command: {command!r}")


def encode_response(response: CaptureResponse) -> str:
    return _encode(asdict(response))


def decode_response(message: str | bytes) -> CaptureResponse:
    payload = _decode_object(message)
    _require_exact_fields(payload, _RESPONSE_FIELDS)
    return CaptureResponse(**payload)


def encode_metadata_query_response(response: MetadataQueryResponse) -> str:
    payload = asdict(response)
    payload["payload"] = dict(response.payload)
    return _encode(payload)


def decode_metadata_query_response(
    message: str | bytes,
) -> MetadataQueryResponse:
    payload = _decode_object(message)
    _require_exact_fields(payload, _METADATA_RESPONSE_FIELDS)
    return MetadataQueryResponse(**payload)


def encode_service_response(
    response: CaptureResponse | MetadataQueryResponse,
) -> str:
    if isinstance(response, MetadataQueryResponse):
        return encode_metadata_query_response(response)
    return encode_response(response)


def validate_status_transition(
    previous: TaskStatus,
    current: TaskStatus,
) -> None:
    if previous not in _ALLOWED_TRANSITIONS:
        raise ProtocolValidationError(f"unsupported task status: {previous!r}")
    if current not in _ALLOWED_TRANSITIONS[previous]:
        raise ProtocolValidationError(
            f"invalid task transition: {previous} -> {current}"
        )


def validate_area_device_snapshot(
    request: CaptureRequest,
    area_devices: Mapping[str, Sequence[str]],
) -> None:
    """Validate a nonempty, approved subset of the selected server areas.

    ``area_devices`` must come from the receiving service's trusted,
    version-controlled registry rather than from the request.
    """

    unknown = [area_id for area_id in request.area_ids if area_id not in area_devices]
    if unknown:
        raise ProtocolValidationError(
            f"area_ids are absent from the server registry: {unknown!r}"
        )
    allowed = tuple(
        device_id
        for area_id in request.area_ids
        for device_id in area_devices[area_id]
    )
    if len(set(allowed)) != len(allowed) or not set(request.devices).issubset(allowed):
        raise ProtocolValidationError(
            "devices must belong to the selected server area_ids"
        )
    actual_areas = {
        area_id for area_id in request.area_ids
        if any(device_id in request.devices for device_id in area_devices[area_id])
    }
    if len(actual_areas) != len(request.area_ids):
        raise ProtocolValidationError("every area_id must contain a selected device")


def _validate_version(version: object) -> None:
    if (
        isinstance(version, bool)
        or not isinstance(version, Integral)
        or int(version) != PROTOCOL_VERSION
    ):
        raise ProtocolValidationError(
            f"version must be {PROTOCOL_VERSION}"
        )


def _validate_request_id(request_id: object) -> None:
    if not isinstance(request_id, str):
        raise ProtocolValidationError("request_id must be a UUID string")
    try:
        parsed = UUID(request_id)
    except (ValueError, AttributeError) as exc:
        raise ProtocolValidationError(
            "request_id must be a UUID string"
        ) from exc
    if str(parsed) != request_id.lower():
        raise ProtocolValidationError(
            "request_id must use canonical UUID form"
        )


def _decode_object(message: str | bytes) -> dict[str, object]:
    if isinstance(message, bytes):
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolValidationError("message is not valid UTF-8") from exc
    if not isinstance(message, str):
        raise ProtocolValidationError("message must be str or bytes")
    try:
        payload = json.loads(message)
    except json.JSONDecodeError as exc:
        raise ProtocolValidationError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ProtocolValidationError("message root must be an object")
    return payload


def _require_exact_fields(
    payload: dict[str, object],
    expected: set[str],
) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    errors = []
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if unknown:
        errors.append("unknown fields: " + ", ".join(unknown))
    if errors:
        raise ProtocolValidationError("; ".join(errors))


def _encode(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
