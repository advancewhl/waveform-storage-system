"""Shared interface between the operator window and runtime backends."""

from __future__ import annotations

from datetime import date
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from waveform_storage.metadata.history import CaptureRunSummary, CaptureSegment

from waveform_storage.acquisition.protocol import CaptureResponse
from waveform_storage.application.communication_service import CaptureDispatchResult
from waveform_storage.processing.alignment import AlignmentResult


class OperatorMode(str, Enum):
    DEMO = "demo"
    LEGACY = "legacy"
    CANDIDATE_V2 = "candidate_v2"


class OperatorBackend(Protocol):
    mode: OperatorMode

    def start_capture(
        self,
        area_ids: tuple[str, ...],
        device_ids: tuple[str, ...],
        duration_seconds: int,
        requested_frequency_hz: float | None = None,
    ) -> CaptureDispatchResult:
        ...

    def get_capture_status(self, request_id: str) -> CaptureResponse:
        ...

    def retrieve_times(self, selected_date: date) -> tuple[str, ...]:
        ...

    def available_devices(
        self,
        selected_date: date,
        time_sec: str,
        device_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        ...

    def align(
        self,
        selected_date: date,
        time_sec: str,
        device_ids: tuple[str, ...],
    ) -> AlignmentResult:
        ...

    def export_alignment(
        self,
        result: AlignmentResult,
        selected_date: date,
        time_sec: str,
        destination: str | Path,
    ) -> Path:
        ...

    def download_raw(
        self,
        selected_date: date,
        time_sec: str,
        device_ids: tuple[str, ...],
        destination: str | Path,
    ) -> tuple[Path, ...]:
        ...


@runtime_checkable
class CandidateHistoryBackend(Protocol):
    """Task/segment capability; legacy backends keep the time-based contract."""

    def retrieve_runs(self, selected_date: date) -> tuple[CaptureRunSummary, ...]: ...

    def retrieve_segments(self, capture_request_id: str) -> tuple[CaptureSegment, ...]: ...

    def align_segment(
        self, segment: CaptureSegment, device_ids: tuple[str, ...],
    ) -> AlignmentResult: ...

    def prepare_segment(self, segment: CaptureSegment, device_ids: tuple[str, ...]): ...

    def align_prepared_segment(self, prepared): ...

    def export_segment_alignment(
        self, result: AlignmentResult, segment: CaptureSegment, destination: str | Path,
    ) -> Path: ...

    def download_segment_raw(
        self, segment: CaptureSegment, device_ids: tuple[str, ...], destination: str | Path,
    ) -> tuple[Path, ...]: ...
