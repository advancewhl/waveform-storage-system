"""Bounded device SSH downloads with explicit file selection."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import time


@dataclass(frozen=True)
class DeviceDownload:
    device_id: str
    host: str
    remote_dir: str
    destination: Path
    file_pattern: str = "*.txt"
    not_before_epoch: float = 0
    settle_seconds: float = 2
    wait_timeout_seconds: float = 0
    poll_seconds: float = 2


@dataclass(frozen=True)
class DownloadResult:
    device_id: str
    success: bool
    path: Path | None = None
    error: str | None = None


class DeviceSshGateway:
    def __init__(
        self,
        connector,
        file_selector=None,
        *,
        max_workers=17,
        waiter=time.sleep,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._connector = connector
        self._file_selector = file_selector
        self._max_workers = max_workers
        self._waiter = waiter

    def download_one(self, request):
        client = None
        sftp = None
        try:
            client = self._connector(request)
            sftp = client.open_sftp()
            deadline = time.monotonic() + request.wait_timeout_seconds
            while True:
                if self._file_selector is None:
                    filename = self._select_completed_file(sftp, request)
                else:
                    filename = self._file_selector(
                        request.device_id,
                        list(sftp.listdir(request.remote_dir)),
                    )
                if filename:
                    remote_path = f"{request.remote_dir.rstrip('/')}/{filename}"
                    try:
                        if self._file_selector is None:
                            self._require_stable_file(sftp, remote_path, request)
                        break
                    except (OSError, RuntimeError):
                        # A file can appear before its writer closes it.
                        pass
                if time.monotonic() >= deadline:
                    raise FileNotFoundError(
                        f"no completed matching file within {request.wait_timeout_seconds:g}s"
                    )
                self._waiter(min(request.poll_seconds, max(0, deadline - time.monotonic())))
            request.destination.parent.mkdir(parents=True, exist_ok=True)
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

    @staticmethod
    def _select_completed_file(sftp, request):
        candidates = []
        for item in sftp.listdir_attr(request.remote_dir):
            filename = item.filename
            if not fnmatch(filename, request.file_pattern):
                continue
            if filename.endswith((".part", ".partial", ".tmp", ".swp")):
                continue
            if float(item.st_mtime) < request.not_before_epoch:
                continue
            if int(item.st_size) <= 0:
                continue
            candidates.append((float(item.st_mtime), filename))
        if not candidates:
            return None
        return max(candidates)[1]

    def _require_stable_file(self, sftp, remote_path, request):
        first = sftp.stat(remote_path)
        if request.settle_seconds > 0:
            self._waiter(request.settle_seconds)
        second = sftp.stat(remote_path)
        if int(first.st_size) <= 0:
            raise RuntimeError("remote file is empty")
        if (
            int(first.st_size) != int(second.st_size)
            or float(first.st_mtime) != float(second.st_mtime)
        ):
            raise RuntimeError("remote file is still changing")

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
