"""Candidate V2 backend used by the formal operator GUI."""

from __future__ import annotations

import os
import shutil
import math
from datetime import date
from pathlib import Path
from uuid import uuid4

from waveform_storage.acquisition.protocol import CaptureRequest, PROTOCOL_VERSION
from waveform_storage.application.communication_service import (
    CaptureCommunicationService,
    CaptureDispatchResult,
    CaptureProtocolMode,
)
from waveform_storage.application.retrieval_service import RetrievalService, StorageQuery
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.operator_contract import OperatorMode
from waveform_storage.processing.alignment import SkippedDevice
from waveform_storage.transport.ssh_storage import download_device_series, save_files
from waveform_storage.transport.zmq_transport import ZmqTimeout, ZmqTransport


class _CatalogStorageGateway:
    def __init__(self, backend: "CandidateOperatorBackend") -> None:
        self.backend = backend

    def find_files(self, query: StorageQuery) -> tuple[tuple[str, str], ...]:
        run_id = query.run_id
        selected_date = date(int(run_id[:4]), int(run_id[4:6]), int(run_id[6:8]))
        return self.backend._candidate_file_pairs(
            selected_date, query.time_sec, query.device_ids
        )

    def read_series(self, pairs, reader):
        if self.backend.candidate_local_files:
            return _read_local_series(pairs, reader)
        return download_device_series(
            pairs,
            reader=reader,
            settings=self.backend.settings,
            control_socket=self.backend.storage_control_socket,
        )


class CandidateOperatorBackend:
    """Only the reviewed Candidate V2 capture and retrieval paths."""

    mode = OperatorMode.CANDIDATE_V2

    def __init__(self, settings: AppSettings) -> None:
        endpoint = os.environ.get("WAVEFORM_CANDIDATE_ZMQ_ENDPOINT", "").strip()
        if not endpoint:
            raise ValueError("candidate_v2 requires WAVEFORM_CANDIDATE_ZMQ_ENDPOINT")
        self.settings = settings
        self.candidate_endpoint = endpoint
        self.storage_control_socket = os.environ.get(
            "WAVEFORM_STORAGE_SSH_CONTROL_SOCKET", ""
        ).strip() or None
        self.candidate_local_files = os.environ.get(
            "WAVEFORM_CANDIDATE_LOCAL_FILES", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._retrieval = RetrievalService(_CatalogStorageGateway(self))

    def start_capture(
        self,
        area_ids: tuple[str, ...],
        device_ids: tuple[str, ...],
        duration_seconds: int,
        requested_frequency_hz: float | None = None,
    ):
        frequency = self.get_timing_frequency()
        if requested_frequency_hz is not None and not math.isclose(
            frequency, requested_frequency_hz, rel_tol=0, abs_tol=0.01
        ):
            raise RuntimeError(
                f"所选 {requested_frequency_hz:g} Hz 与服务端频率 PV "
                f"{frequency:g} Hz 不一致，未发送采集请求"
            )
        request = CaptureRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            "start_capture",
            frequency,
            duration_seconds,
            area_ids,
            device_ids,
        )
        try:
            return CaptureCommunicationService(
                candidate_sender=self._candidate_transport(timeout_ms=10000).request
            ).dispatch(request, CaptureProtocolMode.CANDIDATE_V2)
        except ZmqTimeout as exc:
            # A missing reply does not mean the server rejected the request.
            # Resolve its persisted state with the same ID; never send it again.
            try:
                response = self._candidate_transport(timeout_ms=10000).get_status(
                    request.request_id
                )
            except ZmqTimeout as status_exc:
                raise RuntimeError(
                    f"采集回复和状态查询均超时；请求ID：{request.request_id}。"
                    "请先核对服务端任务，勿重复发送。"
                ) from status_exc
            if response.error_code == "TASK_NOT_FOUND":
                raise RuntimeError(
                    f"采集回复超时，暂未查到服务端任务；请求ID：{request.request_id}。"
                    "请先核对服务端任务，勿重复发送。"
                ) from exc
            if response.request_id != request.request_id:
                raise RuntimeError("采集状态查询返回了不匹配的请求ID")
            return CaptureDispatchResult(
                response.request_id,
                CaptureProtocolMode.CANDIDATE_V2,
                response.accepted,
                response.status,
                response.message,
                response.error_code,
            )

    def get_timing_frequency(self) -> float:
        frequency_reply = self._candidate_transport(
            timeout_ms=25000
        ).get_timing_frequency(str(uuid4()))
        if not frequency_reply.accepted:
            raise RuntimeError(
                f"服务器读取频率PV失败：{frequency_reply.error_code}: "
                f"{frequency_reply.message}"
            )
        frequency = frequency_reply.payload.get("frequency_hz")
        if (
            isinstance(frequency, bool)
            or not isinstance(frequency, (int, float))
            or not math.isfinite(float(frequency))
            or frequency <= 0
        ):
            raise RuntimeError("服务器返回的频率PV读数无效，未发送采集请求")
        return float(frequency)

    def get_capture_status(self, request_id: str):
        return self._candidate_transport().get_status(request_id)

    def retrieve_times(self, selected_date: date) -> tuple[str, ...]:
        reply = self._candidate_transport().list_capture_times(
            str(uuid4()), selected_date.isoformat()
        )
        self._require_metadata_reply(reply)
        times = reply.payload.get("times")
        if not isinstance(times, list) or any(
            not isinstance(item, str) for item in times
        ):
            raise RuntimeError("candidate service returned invalid capture times")
        return tuple(times)

    def available_devices(
        self, selected_date: date, time_sec: str, device_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        available = {
            device_id
            for _path, device_id in self._candidate_file_pairs(
                selected_date, time_sec, device_ids
            )
        }
        return tuple(device_id for device_id in device_ids if device_id in available)

    def align(self, selected_date: date, time_sec: str, device_ids: tuple[str, ...]):
        query = StorageQuery(
            "mongodb-catalog",
            selected_date.strftime("%Y%m%d"),
            time_sec,
            tuple(device_ids),
        )
        return self._retrieval.retrieve(query)

    def export_alignment(
        self, result, selected_date: date, time_sec: str, destination
    ) -> Path:
        query = StorageQuery(
            "candidate-export",
            selected_date.strftime("%Y%m%d"),
            time_sec,
            tuple(result.device_ids),
        )
        return self._retrieval.export(
            result, query, destination, self.settings.alignment_export_format
        )

    def download_raw(
        self,
        selected_date: date,
        time_sec: str,
        device_ids: tuple[str, ...],
        destination,
    ) -> tuple[Path, ...]:
        local_path = Path(destination) / selected_date.strftime("%Y%m%d") / time_sec
        pairs = self._candidate_file_pairs(selected_date, time_sec, device_ids)
        if self.candidate_local_files:
            local_path.mkdir(parents=True, exist_ok=True)
            return tuple(
                Path(shutil.copy2(source, local_path / Path(source).name))
                for source, _device_id in pairs
            )
        saved = save_files(
            self.settings.storage_host,
            self.settings.storage_user,
            self.settings.storage_password,
            pairs,
            local_path,
            self.settings.storage_port,
            control_socket=self.storage_control_socket,
        )
        return tuple(Path(item) for item in saved)

    def _candidate_file_pairs(
        self,
        selected_date: date,
        time_sec: str,
        device_ids: tuple[str, ...],
    ) -> tuple[tuple[str, str], ...]:
        reply = self._candidate_transport().find_waveform_files(
            str(uuid4()), selected_date.isoformat(), time_sec, tuple(device_ids)
        )
        self._require_metadata_reply(reply)
        files = reply.payload.get("files")
        if not isinstance(files, list):
            raise RuntimeError("candidate service returned invalid waveform files")
        pairs = []
        for item in files:
            if not isinstance(item, dict):
                raise RuntimeError("candidate service returned invalid file record")
            path = item.get("path")
            device_id = item.get("device_id")
            if not isinstance(path, str) or not isinstance(device_id, str):
                raise RuntimeError("candidate service returned invalid file path")
            pairs.append((path, device_id))
        return tuple(pairs)

    def _candidate_transport(self, *, timeout_ms: int | None = None) -> ZmqTransport:
        return ZmqTransport(
            self.candidate_endpoint,
            timeout_ms=timeout_ms or max(10000, self.settings.zmq_receive_timeout_ms),
        )

    @staticmethod
    def _require_metadata_reply(reply) -> None:
        if not reply.accepted:
            raise RuntimeError(
                f"{reply.error_code or 'METADATA_QUERY_FAILED'}: {reply.message}"
            )


def _read_local_series(pairs, reader):
    records = []
    skipped = []
    for path, expected_device in pairs:
        try:
            record = reader(path)
            if record.device_id != expected_device:
                raise ValueError(
                    f"HDF5 device {record.device_id!r} does not match "
                    f"catalog device {expected_device!r}"
                )
            records.append(record)
        except Exception as exc:
            skipped.append(
                SkippedDevice(
                    expected_device,
                    "read_failed",
                    f"{type(exc).__name__}: {exc}",
                )
            )
    return tuple(records), tuple(skipped)
