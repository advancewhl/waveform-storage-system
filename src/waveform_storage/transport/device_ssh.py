"""Bounded device SSH downloads with explicit file selection."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeviceDownload:
    device_id: str
    host: str
    remote_dir: str
    destination: Path


@dataclass(frozen=True)
class DownloadResult:
    device_id: str
    success: bool
    path: Path | None = None
    error: str | None = None


class DeviceSshGateway:
    def __init__(self, connector, file_selector, *, max_workers=17):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._connector = connector
        self._file_selector = file_selector
        self._max_workers = max_workers

    def download_one(self, request):
        client = None
        sftp = None
        try:
            client = self._connector(request.host)
            sftp = client.open_sftp()
            filename = self._file_selector(
                request.device_id,
                list(sftp.listdir(request.remote_dir)),
            )
            if not filename:
                raise FileNotFoundError("no completed matching file")
            request.destination.parent.mkdir(parents=True, exist_ok=True)
            remote_path = f"{request.remote_dir.rstrip('/')}/{filename}"
            sftp.get(remote_path, str(request.destination))
            return DownloadResult(
                request.device_id,
                True,
                request.destination,
            )
        except Exception as exc:
            return DownloadResult(request.device_id, False, error=str(exc))
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()

    def download_many(self, requests):
        results = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self.download_one, request): request.device_id
                for request in requests
            }
            for future in as_completed(futures):
                results.append(future.result())
        return sorted(results, key=lambda item: item.device_id)
