"""Dual-protocol capture communication boundary.

The legacy path preserves the deployed GUI string exactly. Candidate v1 uses
the validated JSON request/response types. Selecting a transport is explicit;
there is no automatic fallback that could duplicate a capture after timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from waveform_storage.acquisition.protocol import (
    CaptureRequest,
    CaptureResponse,
)
from waveform_storage.transport.zmq_transport import (
    encode_legacy_capture_message,
)


class CaptureProtocolMode(str, Enum):
    LEGACY = "legacy"
    CANDIDATE_V1 = "candidate_v1"


class CommunicationConfigurationError(ValueError):
    """Raised before a request is sent when protocol settings are unsafe."""


@dataclass(frozen=True)
class CaptureDispatchResult:
    request_id: str
    mode: CaptureProtocolMode
    accepted: bool
    status: str
    message: str
    error_code: str | None = None
    raw_response: str | None = None


class CaptureCommunicationService:
    """Dispatch one canonical v1 request through an explicit protocol mode."""

    def __init__(
        self,
        *,
        legacy_sender: Callable[[str], object] | None = None,
        candidate_sender: Callable[[CaptureRequest], CaptureResponse] | None = None,
    ):
        self._legacy_sender = legacy_sender
        self._candidate_sender = candidate_sender

    def dispatch(
        self,
        request: CaptureRequest,
        mode: CaptureProtocolMode | str,
    ) -> CaptureDispatchResult:
        selected = CaptureProtocolMode(mode)
        if selected is CaptureProtocolMode.LEGACY:
            return self._dispatch_legacy(request)
        return self._dispatch_candidate(request)

    def _dispatch_legacy(self, request: CaptureRequest) -> CaptureDispatchResult:
        if self._legacy_sender is None:
            raise CommunicationConfigurationError(
                "legacy sender is not configured"
            )
        minutes, remainder = divmod(request.duration_seconds, 60)
        if remainder:
            raise CommunicationConfigurationError(
                "legacy duration must be an exact number of minutes"
            )
        raw = self._legacy_sender(
            encode_legacy_capture_message(
                request.frequency_hz,
                str(minutes),
                list(request.devices),
            )
        )
        if raw is False or raw is None:
            return CaptureDispatchResult(
                request.request_id,
                CaptureProtocolMode.LEGACY,
                False,
                "transport_failed",
                "legacy transport returned no response",
                "LEGACY_TRANSPORT_FAILED",
            )
        return CaptureDispatchResult(
            request.request_id,
            CaptureProtocolMode.LEGACY,
            True,
            "legacy_response",
            str(raw),
            raw_response=str(raw),
        )

    def _dispatch_candidate(
        self,
        request: CaptureRequest,
    ) -> CaptureDispatchResult:
        if self._candidate_sender is None:
            raise CommunicationConfigurationError(
                "candidate v1 sender is not configured"
            )
        response = self._candidate_sender(request)
        if response.request_id != request.request_id:
            raise CommunicationConfigurationError(
                "candidate response request_id does not match request"
            )
        return CaptureDispatchResult(
            response.request_id,
            CaptureProtocolMode.CANDIDATE_V1,
            response.accepted,
            response.status,
            response.message,
            response.error_code,
        )
