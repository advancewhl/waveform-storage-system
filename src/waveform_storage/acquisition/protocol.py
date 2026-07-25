"""Candidate version-1 acquisition protocol for local development.

This module is deliberately not wired to the production ``saveApp`` service.
Its purpose is to make validation, encoding, task identity, and state
transitions testable before the production socket and response semantics are
confirmed.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from numbers import Integral, Real
from typing import Literal
from uuid import UUID


PROTOCOL_VERSION = 1
START_CAPTURE = "start_capture"
DEVICE_ID_PATTERN = re.compile(r"^[A-Z0-9_]+$")

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


def encode_request(request: CaptureRequest) -> str:
    payload = asdict(request)
    payload["devices"] = list(request.devices)
    return _encode(payload)


def decode_request(message: str | bytes) -> CaptureRequest:
    payload = _decode_object(message)
    _require_exact_fields(payload, _REQUEST_FIELDS)
    devices = payload["devices"]
    if not isinstance(devices, list):
        raise ProtocolValidationError("devices must be a JSON array")
    return CaptureRequest(
        version=payload["version"],
        request_id=payload["request_id"],
        command=payload["command"],
        frequency_hz=payload["frequency_hz"],
        duration_seconds=payload["duration_seconds"],
        devices=tuple(devices),
    )


def encode_response(response: CaptureResponse) -> str:
    return _encode(asdict(response))


def decode_response(message: str | bytes) -> CaptureResponse:
    payload = _decode_object(message)
    _require_exact_fields(payload, _RESPONSE_FIELDS)
    return CaptureResponse(**payload)


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
