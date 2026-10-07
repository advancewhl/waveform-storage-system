"""Application-facing metadata recording and query service."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import h5py

from waveform_storage.acquisition.protocol import CaptureRequest, CaptureResponse
from waveform_storage.config.registry import DeviceRegistry
from waveform_storage.metadata.models import (
    CaptureTaskMetadata,
    RegistrySnapshotMetadata,
    StatusEvent,
    WaveformFileMetadata,
)


_TIME_FIELD = re.compile(r"^RAW-(?P<time>\d{6})-.+-S(?P<sequence>\d{5})\.h5$")


class CaptureMetadataService:
    """Mirror durable capture state into a query-oriented metadata catalog.

    Database failures are logged and isolated from HDF5 capture.  Availability
    requirements are enforced during service startup, before this object is
    constructed.
    """

    def __init__(
        self,
        repository,
        registry: DeviceRegistry,
        registry_path: str | Path,
        *,
        storage_id: str = "shine_data_primary",
        timezone_name: str = "Asia/Shanghai",
        software_version: str | None = None,
        git_commit: str | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.registry_path = Path(registry_path)
        self.storage_id = storage_id
        self.timezone_name = timezone_name
        self.local_timezone = ZoneInfo(timezone_name)
        self.software_version = software_version
        self.git_commit = git_commit
        self.devices = {item.id: item for item in registry.devices}
        self.registry_id = self._registry_id()
        self._accepted_at: dict[str, datetime] = {}
        self.logger = logging.getLogger("waveform_storage.metadata")

    def initialize(self) -> None:
        self.repository.ensure_indexes()
        devices = tuple(
            {
                "id": item.id,
                "section": item.section,
                "display_name": item.display_name,
                "device_type": item.device_type,
                "capture_pv": item.capture_pv,
                "epics_host": item.epics_host,
                "epics_port": item.epics_port,
                "ssh_host": item.ssh_host,
                "ssh_port": item.ssh_port,
                "remote_data_dir": item.remote_data_dir,
                "file_pattern": item.file_pattern,
                "expected_points": item.expected_points,
                "x_unit": item.x_unit,
                "y_unit": item.y_unit,
                "scale_factor": item.scale_factor,
                "order": item.order,
                "enabled": item.enabled,
                "acquisition_enabled": item.acquisition_enabled,
                "tags": list(item.tags),
            }
            for item in self.registry.devices
        )
        snapshot = RegistrySnapshotMetadata(
            self.registry_id,
            self.registry.schema_version,
            self.registry.description,
            devices,
            sum(item.acquisition_enabled for item in self.registry.devices),
            self.registry_path.name,
            git_commit=self.git_commit,
        )
        self.repository.upsert_registry_snapshot(snapshot)

    def record_created(
        self,
        request: CaptureRequest,
        response: CaptureResponse,
    ) -> None:
        now = datetime.now(timezone.utc)
        if response.status in {"accepted", "running"}:
            self._accepted_at[request.request_id] = now
        task = CaptureTaskMetadata(
            request.request_id,
            request.frequency_hz,
            request.duration_seconds,
            tuple(request.area_ids),
            tuple(request.devices),
            self.registry_id,
            self.registry.schema_version,
            status=response.status,
            status_history=(
                StatusEvent(
                    response.status,
                    now,
                    response.message,
                    response.error_code,
                ),
            ),
            created_at=now,
            protocol_version=request.version,
            software_version=self.software_version,
            git_commit=self.git_commit,
            error=self._response_error(response),
        )
        self._best_effort("create capture task", self.repository.upsert_task, task)

    def record_status(
        self,
        request: CaptureRequest,
        response: CaptureResponse,
        *,
        device_results: Sequence[Any] = (),
    ) -> None:
        now = datetime.now(timezone.utc)
        summary = None
        if device_results:
            with_data = sum(item.successful_cycles > 0 for item in device_results)
            summary = {
                "requested": len(request.devices),
                "succeeded": with_data,
                "failed": len(device_results) - with_data,
                "attempted_cycles": sum(item.attempted_cycles for item in device_results),
                "saved_cycles": sum(item.successful_cycles for item in device_results),
                "partial_devices": sum(
                    0 < item.successful_cycles < item.attempted_cycles
                    for item in device_results
                ),
            }
        self._best_effort(
            "update capture task",
            self.repository.update_task_status,
            request.request_id,
            response.status,
            timestamp=now,
            message=response.message,
            error_code=response.error_code,
            result_summary=summary,
        )
        if response.status in {"succeeded", "failed"}:
            self._record_files(request, device_results)
        if response.status in {"succeeded", "failed", "cancelled", "rejected"}:
            self._accepted_at.pop(request.request_id, None)

    def list_capture_times(self, storage_date_local: str) -> tuple[str, ...]:
        return self.repository.list_capture_times(storage_date_local)

    def find_waveform_files(
        self,
        *,
        storage_date_local: str,
        time_sec: str,
        device_ids: Sequence[str],
    ) -> tuple[Mapping[str, Any], ...]:
        return self.repository.find_waveform_files(
            storage_date_local=storage_date_local,
            time_sec=time_sec,
            device_ids=device_ids,
        )

    def _record_files(
        self,
        request: CaptureRequest,
        device_results: Sequence[Any],
    ) -> None:
        captured_at = self._accepted_at.get(
            request.request_id,
            datetime.now(timezone.utc),
        )
        for result in device_results:
            device = self.devices.get(result.device_id)
            if device is None:
                self.logger.error("metadata skipped unknown device %s", result.device_id)
                continue
            paths = tuple(getattr(result, "output_paths", ()))
            if not paths and result.output_path is not None:
                paths = (result.output_path,)
            for raw_path in paths:
                path = Path(raw_path)
                try:
                    with h5py.File(path, "r") as h5:
                        bunch = h5["BunchIDData"]
                        data = h5["Data"]
                        bunch_shape = tuple(int(value) for value in bunch.shape)
                        data_shape = tuple(int(value) for value in data.shape)
                        bunch_dtype = str(bunch.dtype)
                        data_dtype = str(data.dtype)
                        sample_count = int(data.shape[-1])
                        file_captured_at = self._file_captured_at(
                            h5.attrs.get("capture_metadata_json"),
                            captured_at,
                        )
                    match = _TIME_FIELD.match(path.name)
                    time_sec = (
                        match.group("time")
                        if match is not None
                        else file_captured_at.astimezone(
                            self.local_timezone
                        ).strftime("%H%M%S")
                    )
                    local_date = file_captured_at.astimezone(
                        self.local_timezone
                    ).date().isoformat()
                    sequence = (
                        int(match.group("sequence")) if match is not None else 0
                    )
                    record = WaveformFileMetadata(
                        request.request_id,
                        result.device_id,
                        device.capture_pv,
                        file_captured_at,
                        local_date,
                        time_sec,
                        self.timezone_name,
                        self.storage_id,
                        str(path),
                        sample_count,
                        bunch_shape,
                        data_shape,
                        path.stat().st_size,
                        bunch_id_dtype=bunch_dtype,
                        data_dtype=data_dtype,
                        x_unit=device.x_unit,
                        y_unit=device.y_unit,
                        scale_factor=device.scale_factor,
                        capture_sequence=sequence,
                    )
                except Exception as exc:
                    self.logger.error(
                        "failed to inspect HDF5 metadata for %s: %s",
                        result.device_id,
                        exc,
                    )
                    continue
                self._best_effort(
                    "upsert waveform file",
                    self.repository.upsert_waveform_file,
                    record,
                )

    @staticmethod
    def _file_captured_at(raw_metadata: object, fallback: datetime) -> datetime:
        try:
            if isinstance(raw_metadata, bytes):
                raw_metadata = raw_metadata.decode("utf-8")
            if not isinstance(raw_metadata, str) or not raw_metadata:
                return fallback
            metadata = json.loads(raw_metadata)
            value = datetime.fromisoformat(str(metadata["captured_at"]))
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return fallback
        if value.tzinfo is None:
            return fallback
        return value

    def _registry_id(self) -> str:
        digest = hashlib.sha256(self.registry_path.read_bytes()).hexdigest()
        return f"sha256:{digest}"

    def _best_effort(self, operation: str, function, *args, **kwargs) -> None:
        try:
            function(*args, **kwargs)
        except Exception as exc:
            self.logger.error("MongoDB metadata %s failed: %s", operation, exc)

    @staticmethod
    def _response_error(response: CaptureResponse) -> dict[str, str] | None:
        if response.error_code is None:
            return None
        return {"code": response.error_code, "message": response.message}
