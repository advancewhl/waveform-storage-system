"""Local candidate capture orchestration; not connected to production."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CaptureRequest:
    request_id: str
    frequency_hz: float
    duration_seconds: int
    device_ids: tuple[str, ...]

    def __post_init__(self):
        if not self.request_id:
            raise ValueError("request_id must not be empty")
        if self.frequency_hz <= 0:
            raise ValueError("frequency_hz must be positive")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if not self.device_ids or len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("device_ids must be non-empty and unique")


@dataclass(frozen=True)
class DeviceCaptureResult:
    device_id: str
    success: bool
    output_path: Path | None = None
    error: str | None = None


class CaptureService:
    def __init__(self, epics, downloader, parser, writer, waiter=lambda _: None):
        self._epics = epics
        self._downloader = downloader
        self._parser = parser
        self._writer = writer
        self._waiter = waiter

    def capture(self, request, device_pvs, download_requests):
        trigger_by_device = {
            item.device_id: item
            for item in self._epics.trigger(device_pvs)
        }
        self._waiter(request.duration_seconds)
        downloads = {
            item.device_id: item
            for item in self._downloader.download_many(download_requests)
        }
        results = []
        for device_id in request.device_ids:
            trigger = trigger_by_device.get(device_id)
            if trigger is None or not trigger.success:
                error = trigger.error if trigger is not None else "not triggered"
                results.append(DeviceCaptureResult(device_id, False, error=error))
                continue
            download = downloads.get(device_id)
            if download is None or not download.success:
                error = download.error if download is not None else "not downloaded"
                results.append(DeviceCaptureResult(device_id, False, error=error))
                continue
            try:
                parsed = self._parser(download.path)
                output = Path(self._writer(device_id, parsed, request))
                results.append(DeviceCaptureResult(device_id, True, output))
            except Exception as exc:
                results.append(
                    DeviceCaptureResult(device_id, False, error=str(exc))
                )
        return tuple(results)
