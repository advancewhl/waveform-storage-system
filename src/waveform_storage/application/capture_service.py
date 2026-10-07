"""Local candidate capture orchestration; not connected to production."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import time

from waveform_storage.acquisition.protocol import (
    CaptureRequest as ProtocolCaptureRequest,
)
from waveform_storage.logging_config import log_context


@dataclass(frozen=True)
class CapturePlan:
    """Application-level capture plan derived from a validated wire request."""

    request_id: str
    frequency_hz: float
    duration_seconds: int
    device_ids: tuple[str, ...]
    area_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.request_id:
            raise ValueError("request_id must not be empty")
        if self.frequency_hz <= 0:
            raise ValueError("frequency_hz must be positive")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if not self.device_ids or len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("device_ids must be non-empty and unique")

    @classmethod
    def from_protocol(cls, request: ProtocolCaptureRequest) -> "CapturePlan":
        if request.command != "start_capture":
            raise ValueError(f"unsupported capture command: {request.command}")
        return cls(
            request_id=request.request_id,
            frequency_hz=float(request.frequency_hz),
            duration_seconds=request.duration_seconds,
            device_ids=tuple(request.devices),
            area_ids=tuple(request.area_ids),
        )


# Compatibility alias for callers created before the protocol/application split
# was made explicit. New code should use CapturePlan.
CaptureRequest = CapturePlan


@dataclass(frozen=True)
class DeviceCaptureResult:
    device_id: str
    success: bool
    output_path: Path | None = None
    error: str | None = None
    output_paths: tuple[Path, ...] = ()
    attempted_cycles: int = 1
    successful_cycles: int = 0
    failure_stage: str | None = None

    def __post_init__(self) -> None:
        paths = tuple(Path(item) for item in self.output_paths)
        if not paths and self.output_path is not None:
            paths = (Path(self.output_path),)
        object.__setattr__(self, "output_paths", paths)
        if self.success and self.successful_cycles == 0 and paths:
            object.__setattr__(self, "successful_cycles", len(paths))


class CaptureService:
    def __init__(self, epics, downloader, parser, writer, waiter=lambda _: None):
        self._epics = epics
        self._downloader = downloader
        self._parser = parser
        self._writer = writer
        self._waiter = waiter

    def capture(self, request, device_pvs, download_requests):
        started = time.monotonic()
        logger = log_context(
            logging.getLogger("waveform_storage.capture"),
            request_id=request.request_id,
            task_id=request.request_id,
            stage="capture",
            device=None,
            duration=None,
            status="running",
            error_type=None,
        )
        logger.info("capture candidate started")
        trigger_by_device = {
            item.device_id: item
            for item in self._epics.trigger(device_pvs)
        }
        triggered_ids = {
            device_id
            for device_id, result in trigger_by_device.items()
            if result.success
        }
        if not triggered_ids:
            completed = tuple(
                DeviceCaptureResult(
                    device_id,
                    False,
                    error=(
                        trigger_by_device[device_id].error
                        if device_id in trigger_by_device
                        else "not triggered"
                    ),
                    failure_stage="EPICS 触发",
                )
                for device_id in request.device_ids
            )
            log_context(
                logging.getLogger("waveform_storage.capture"),
                request_id=request.request_id,
                task_id=request.request_id,
                stage="capture",
                device=None,
                duration=round(time.monotonic() - started, 6),
                status="failed",
                error_type="NoDeviceTriggered",
            ).info("capture candidate completed without a connected device")
            return completed
        self._waiter(request.duration_seconds)
        eligible_downloads = [
            item
            for item in download_requests
            if getattr(item, "device_id", item) in triggered_ids
        ]
        downloads = {
            item.device_id: item
            for item in self._downloader.download_many(eligible_downloads)
        }
        results = []
        for device_id in request.device_ids:
            trigger = trigger_by_device.get(device_id)
            if trigger is None or not trigger.success:
                error = trigger.error if trigger is not None else "not triggered"
                results.append(DeviceCaptureResult(
                    device_id, False, error=error, failure_stage="EPICS 触发"
                ))
                continue
            download = downloads.get(device_id)
            if download is None or not download.success:
                error = download.error if download is not None else "not downloaded"
                results.append(DeviceCaptureResult(
                    device_id, False, error=error,
                    failure_stage="设备文件等待/下载",
                ))
                continue
            try:
                parsed = self._parser(download.path)
            except Exception as exc:
                results.append(
                    DeviceCaptureResult(
                        device_id, False, error=str(exc),
                        failure_stage="TXT 解析",
                    )
                )
                continue
            try:
                output = Path(self._writer(device_id, parsed, request))
                results.append(DeviceCaptureResult(device_id, True, output))
            except Exception as exc:
                results.append(
                    DeviceCaptureResult(
                        device_id, False, error=str(exc),
                        failure_stage="HDF5 写入",
                    )
                )
        completed = tuple(results)
        failures = sum(not item.success for item in completed)
        log_context(
            logging.getLogger("waveform_storage.capture"),
            request_id=request.request_id,
            task_id=request.request_id,
            stage="capture",
            device=None,
            duration=round(time.monotonic() - started, 6),
            status="failed" if failures else "succeeded",
            error_type="PartialDeviceFailure" if failures else None,
        ).info("capture candidate completed")
        return completed

    def capture_protocol_request(
        self,
        request: ProtocolCaptureRequest,
        resolver,
    ):
        """Run a validated v2 request through the application service.

        ``resolver`` converts the device IDs in the plan into the EPICS PV and
        download request collections required by the deployment adapter.
        """

        plan = CapturePlan.from_protocol(request)
        device_pvs, download_requests = resolver(plan)
        return self.capture(plan, device_pvs, download_requests)
