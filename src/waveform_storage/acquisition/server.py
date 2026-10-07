"""Persistent asynchronous ZMQ service for acquisition protocol v2."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

import zmq

from waveform_storage.acquisition.protocol import (
    CANCEL,
    GET_STATUS,
    GET_TIMING_FREQUENCY,
    HEALTH,
    FIND_WAVEFORM_FILES,
    LIST_CAPTURE_TIMES,
    PROTOCOL_VERSION,
    CaptureRequest,
    CaptureResponse,
    ControlRequest,
    MetadataQueryRequest,
    MetadataQueryResponse,
    ProtocolValidationError,
    decode_service_request,
    encode_request,
    encode_service_response,
    validate_area_device_snapshot,
    validate_status_transition,
)
from waveform_storage.acquisition.task import CaptureCancelled
from waveform_storage.config.registry import DeviceRegistry
from waveform_storage.config.beamline_areas import build_beamline_areas
from waveform_storage.logging_config import log_context


TASK_RECORD_VERSION = 1
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "rejected"}
FREQUENCY_PV_RESPONSE_TIMEOUT_SECONDS = 20.0


class TaskStoreError(RuntimeError):
    """Raised when durable task state is missing or malformed."""


class JsonTaskStore:
    """One atomically replaced JSON record per request ID."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, object]] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self._validate_record(payload, path)
            except (OSError, ValueError, TypeError) as exc:
                raise TaskStoreError(
                    f"invalid task record {path}: {exc}"
                ) from exc
            request_id = str(payload["request_id"])
            self._records[request_id] = payload

    @staticmethod
    def _validate_record(payload: object, path: Path) -> None:
        if not isinstance(payload, dict):
            raise TaskStoreError(f"{path} root must be an object")
        required = {
            "record_version",
            "request_id",
            "request_json",
            "response",
            "history",
            "device_results",
            "created_utc",
            "updated_utc",
        }
        if set(payload) != required:
            raise TaskStoreError(f"{path} has unexpected record fields")
        if payload["record_version"] != TASK_RECORD_VERSION:
            raise TaskStoreError(f"{path} has unsupported record version")
        response = payload["response"]
        if not isinstance(response, dict):
            raise TaskStoreError(f"{path} response must be an object")
        CaptureResponse(**response)

    def create(
        self,
        request: CaptureRequest,
        response: CaptureResponse,
    ) -> None:
        now = _utc_now()
        record = {
            "record_version": TASK_RECORD_VERSION,
            "request_id": request.request_id,
            "request_json": encode_request(request),
            "response": asdict(response),
            "history": [
                {"status": response.status, "timestamp_utc": now}
            ],
            "device_results": [],
            "created_utc": now,
            "updated_utc": now,
        }
        with self._lock:
            if request.request_id in self._records:
                raise TaskStoreError(
                    f"request already exists: {request.request_id}"
                )
            self._records[request.request_id] = record
            self._write(record)

    def request_matches(self, request: CaptureRequest) -> bool:
        with self._lock:
            record = self._records.get(request.request_id)
            return bool(
                record
                and record["request_json"] == encode_request(request)
            )

    def get_response(self, request_id: str) -> CaptureResponse | None:
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return None
            return CaptureResponse(**dict(record["response"]))

    def set_response(
        self,
        response: CaptureResponse,
        *,
        device_results=(),
    ) -> None:
        with self._lock:
            record = self._records.get(response.request_id)
            if record is None:
                raise TaskStoreError(
                    f"request does not exist: {response.request_id}"
                )
            previous = CaptureResponse(**dict(record["response"]))
            if previous.status != response.status:
                validate_status_transition(previous.status, response.status)
                history = list(record["history"])
                history.append(
                    {
                        "status": response.status,
                        "timestamp_utc": _utc_now(),
                    }
                )
                record["history"] = history
            record["response"] = asdict(response)
            record["device_results"] = [
                {
                    "device_id": item.device_id,
                    "success": bool(item.success),
                    "output_path": (
                        str(item.output_path)
                        if item.output_path is not None
                        else None
                    ),
                    "error": item.error,
                    "failure_stage": getattr(item, "failure_stage", None),
                    "output_paths": [str(path) for path in item.output_paths],
                    "attempted_cycles": item.attempted_cycles,
                    "successful_cycles": item.successful_cycles,
                }
                for item in device_results
            ]
            record["updated_utc"] = _utc_now()
            self._write(record)

    def recover_incomplete(self) -> tuple[str, ...]:
        """Mark tasks interrupted by a service restart as failed."""

        recovered = []
        with self._lock:
            for request_id, record in tuple(self._records.items()):
                response = CaptureResponse(**dict(record["response"]))
                if response.status not in {"accepted", "running"}:
                    continue
                interrupted = CaptureResponse(
                    PROTOCOL_VERSION,
                    request_id,
                    True,
                    "failed",
                    "service restarted before the task reached a terminal state",
                    "INTERRUPTED_BY_RESTART",
                )
                self.set_response(interrupted)
                recovered.append(request_id)
        return tuple(recovered)

    def _write(self, record: dict[str, object]) -> None:
        request_id = str(record["request_id"])
        destination = self.directory / f"{request_id}.json"
        temporary = self.directory / f".{request_id}.{uuid4().hex}.tmp"
        text = json.dumps(
            record,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(text + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()


class RegistryPolicy:
    """Trusted server-side expansion of approved acquisition devices."""

    def __init__(
        self,
        registry: DeviceRegistry,
        *,
        max_duration_seconds: int = 3600,
        max_devices: int = 32,
    ) -> None:
        self.registry = registry
        self.max_duration_seconds = max_duration_seconds
        self.max_devices = max_devices
        self.devices = {item.id: item for item in registry.devices}
        self.area_devices = {
            area.area_id: tuple(
                device_id
                for device_id in area.device_ids
                if self.devices[device_id].acquisition_enabled
            )
            for area in build_beamline_areas(registry)
        }
        self.area_devices = {
            area_id: device_ids
            for area_id, device_ids in self.area_devices.items()
            if device_ids
        }

    def validate(self, request: CaptureRequest) -> None:
        if request.duration_seconds > self.max_duration_seconds:
            raise ProtocolValidationError(
                "duration_seconds exceeds the server limit "
                f"of {self.max_duration_seconds}"
            )
        if len(request.devices) > self.max_devices:
            raise ProtocolValidationError(
                f"device count exceeds the server limit of {self.max_devices}"
            )
        validate_area_device_snapshot(request, self.area_devices)
        blocked = [
            device_id
            for device_id in request.devices
            if device_id not in self.devices
            or not self.devices[device_id].enabled
            or not self.devices[device_id].acquisition_enabled
        ]
        if blocked:
            raise ProtocolValidationError(
                f"devices are not approved for acquisition: {blocked!r}"
            )


class PersistentTaskExecutor:
    """Accept one capture at a time and execute it outside the ZMQ loop."""

    def __init__(
        self,
        store: JsonTaskStore,
        runner: Callable[[CaptureRequest], object],
        *,
        canceller: Callable[[], None] | None = None,
        prepare: Callable[[], None] | None = None,
        metadata=None,
    ) -> None:
        self.store = store
        self._runner = runner
        self._canceller = canceller
        self._prepare = prepare
        self._metadata = metadata
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="waveform-capture",
        )
        self._lock = threading.Lock()
        self._active_request_id: str | None = None
        self.store.recover_incomplete()

    def submit(self, request: CaptureRequest) -> CaptureResponse:
        with self._lock:
            existing = self.store.get_response(request.request_id)
            if existing is not None:
                if self.store.request_matches(request):
                    self._log(
                        existing,
                        "duplicate request returned persisted task state",
                    )
                    return existing
                conflict = _rejected(
                    request.request_id,
                    "request_id was already used for a different payload",
                    "REQUEST_ID_CONFLICT",
                )
                self._log(conflict, "request ID conflict rejected")
                return conflict
            if self._active_request_id is not None:
                response = _rejected(
                    request.request_id,
                    f"capture {self._active_request_id} is still active",
                    "SERVICE_BUSY",
                )
                self.store.create(request, response)
                if self._metadata is not None:
                    self._metadata.record_created(request, response)
                self._log(response, "capture rejected while service is busy")
                return response

            accepted = CaptureResponse(
                PROTOCOL_VERSION,
                request.request_id,
                True,
                "accepted",
                "capture accepted for asynchronous execution",
            )
            if self._prepare is not None:
                self._prepare()
            self.store.create(request, accepted)
            if self._metadata is not None:
                self._metadata.record_created(request, accepted)
            self._active_request_id = request.request_id
            self._executor.submit(self._execute, request)
            self._log(accepted, "capture accepted")
            return accepted

    def _execute(self, request: CaptureRequest) -> None:
        running = CaptureResponse(
            PROTOCOL_VERSION,
            request.request_id,
            True,
            "running",
            "capture is running",
        )
        self.store.set_response(running)
        if self._metadata is not None:
            self._metadata.record_status(request, running)
        self._log(running, "capture started")
        results = ()
        try:
            results = tuple(self._runner(request))
            failed = [item for item in results if not item.success]
            saved = sum(item.successful_cycles for item in results)
            attempted = sum(item.attempted_cycles for item in results)
            without_data = sum(item.successful_cycles == 0 for item in results)
            if failed or saved < attempted:
                partial = saved > 0
                response = CaptureResponse(
                    PROTOCOL_VERSION,
                    request.request_id,
                    True,
                    "failed",
                    (f"partial capture: {saved}/{attempted} cycles saved across "
                     f"{len(results)} device(s); {without_data} device(s) had no data"
                     if partial else f"capture failed: 0/{attempted} cycles saved across {len(results)} device(s)"),
                    "PARTIAL_CAPTURE" if partial else "CAPTURE_FAILED",
                )
            else:
                response = CaptureResponse(
                    PROTOCOL_VERSION,
                    request.request_id,
                    True,
                    "succeeded",
                    f"capture completed for {len(results)} device(s)",
                )
        except CaptureCancelled:
            response = CaptureResponse(
                PROTOCOL_VERSION,
                request.request_id,
                True,
                "cancelled",
                "capture cancelled during service shutdown",
            )
        except Exception as exc:
            response = CaptureResponse(
                PROTOCOL_VERSION,
                request.request_id,
                True,
                "failed",
                str(exc) or type(exc).__name__,
                _error_code(exc),
            )
        try:
            self.store.set_response(response, device_results=results)
            if self._metadata is not None:
                self._metadata.record_status(
                    request,
                    response,
                    device_results=results,
                )
            self._log(response, "capture reached terminal state")
        finally:
            with self._lock:
                self._active_request_id = None

    def status(self, request_id: str) -> CaptureResponse | None:
        return self.store.get_response(request_id)

    def active_request_id(self) -> str | None:
        with self._lock:
            return self._active_request_id

    def cancel(self, request_id: str) -> CaptureResponse:
        with self._lock:
            response = self.store.get_response(request_id)
            if response is None:
                return _rejected(
                    request_id,
                    "task was not found",
                    "TASK_NOT_FOUND",
                )
            if response.status in _TERMINAL_STATUSES:
                return response
            if self._active_request_id != request_id:
                return _rejected(
                    request_id,
                    "task is not active in this service instance",
                    "TASK_NOT_ACTIVE",
                )
            if self._canceller is None:
                return _rejected(
                    request_id,
                    "task cancellation is not configured",
                    "CANCEL_NOT_SUPPORTED",
                )
            self._canceller()
            return CaptureResponse(
                PROTOCOL_VERSION,
                request_id,
                True,
                response.status,
                "cancellation requested; waiting for a safe boundary",
            )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    @staticmethod
    def _log(response: CaptureResponse, message: str) -> None:
        log_context(
            logging.getLogger("waveform_storage.acquisition.server"),
            request_id=response.request_id,
            task_id=response.request_id,
            stage="capture",
            device=None,
            duration=None,
            status=response.status,
            error_type=response.error_code,
        ).info(message)


class CandidateAcquisitionServer:
    """A bounded REP service that returns immediately after task acceptance."""

    def __init__(
        self,
        endpoint: str,
        policy: RegistryPolicy,
        tasks: PersistentTaskExecutor,
        *,
        context=None,
        metadata=None,
        frequency_reader: Callable[[], float] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.policy = policy
        self.tasks = tasks
        self.metadata = metadata
        self.frequency_reader = frequency_reader
        self._frequency_lock = threading.Lock()
        self._frequency_probe: threading.Thread | None = None
        self.context = context or zmq.Context.instance()
        self._socket = None

    def handle_message(
        self,
        message: str | bytes,
    ) -> CaptureResponse | MetadataQueryResponse:
        try:
            request = decode_service_request(message)
        except ProtocolValidationError as exc:
            return _rejected(
                _request_id_or_zero(message),
                str(exc),
                "INVALID_REQUEST",
            )

        if isinstance(request, ControlRequest):
            if request.command == HEALTH:
                return CaptureResponse(
                    PROTOCOL_VERSION,
                    request.request_id,
                    True,
                    "succeeded",
                    "waveform acquisition v2 service is healthy",
                )
            if request.command == GET_STATUS:
                response = self.tasks.status(request.request_id)
                if response is not None:
                    return response
                return _rejected(
                    request.request_id,
                    "task was not found",
                    "TASK_NOT_FOUND",
                )
            if request.command == CANCEL:
                return self.tasks.cancel(request.request_id)
            if request.command == GET_TIMING_FREQUENCY:
                return self._handle_timing_frequency(request)

        if isinstance(request, MetadataQueryRequest):
            return self._handle_metadata_query(request)

        try:
            self.policy.validate(request)
        except ProtocolValidationError as exc:
            return _rejected(
                request.request_id,
                str(exc),
                "REGISTRY_VALIDATION_FAILED",
            )
        # Keep persisted retries idempotent and let the executor report busy
        # requests without waiting for a timing PV that cannot be used yet.
        if (
            self.tasks.status(request.request_id) is not None
            or self.tasks.active_request_id() is not None
        ):
            return self.tasks.submit(request)
        if self.frequency_reader is not None:
            try:
                actual_frequency = self._read_frequency_bounded()
            except Exception as exc:
                return _rejected(
                    request.request_id,
                    f"timing frequency PV read failed: {exc}",
                    "TIMING_FREQUENCY_FAILED",
                )
            if not math.isfinite(actual_frequency) or actual_frequency <= 0:
                return _rejected(
                    request.request_id,
                    "timing frequency PV returned an invalid value",
                    "TIMING_FREQUENCY_FAILED",
                )
            if not math.isclose(
                request.frequency_hz,
                actual_frequency,
                rel_tol=1e-6,
                abs_tol=1e-6,
            ):
                return _rejected(
                    request.request_id,
                    f"requested frequency {request.frequency_hz:g} Hz differs "
                    f"from timing PV {actual_frequency:g} Hz",
                    "FREQUENCY_MISMATCH",
                )
        return self.tasks.submit(request)

    def _handle_timing_frequency(
        self, request: ControlRequest
    ) -> MetadataQueryResponse:
        active = self.tasks.active_request_id()
        if active is not None:
            return MetadataQueryResponse(
                PROTOCOL_VERSION, request.request_id, False,
                f"capture {active} is still active", {}, "SERVICE_BUSY",
            )
        if self.frequency_reader is None:
            return MetadataQueryResponse(
                PROTOCOL_VERSION, request.request_id, False,
                "timing frequency reader is not configured", {},
                "TIMING_FREQUENCY_UNAVAILABLE",
            )
        try:
            frequency = self._read_frequency_bounded()
        except Exception as exc:
            return MetadataQueryResponse(
                PROTOCOL_VERSION, request.request_id, False,
                str(exc) or type(exc).__name__, {}, "TIMING_FREQUENCY_FAILED",
            )
        return MetadataQueryResponse(
            PROTOCOL_VERSION, request.request_id, True,
            "timing frequency read from PV on waveform server",
            {"frequency_hz": frequency, "source": "timing_pv"},
        )

    def _read_frequency_bounded(self) -> float:
        """Keep a stalled EPICS read from blocking the service's REP socket."""

        reader = self.frequency_reader
        if reader is None:
            raise RuntimeError("timing frequency reader is not configured")
        completed = threading.Event()
        outcome: dict[str, object] = {}

        def probe() -> None:
            try:
                outcome["value"] = float(reader())
            except Exception as exc:
                outcome["error"] = exc
            finally:
                completed.set()

        with self._frequency_lock:
            if self._frequency_probe is not None and self._frequency_probe.is_alive():
                raise TimeoutError("previous timing frequency PV read is still pending")
            self._frequency_probe = threading.Thread(
                target=probe, name="waveform-frequency-pv", daemon=True
            )
            self._frequency_probe.start()
        if not completed.wait(FREQUENCY_PV_RESPONSE_TIMEOUT_SECONDS):
            raise TimeoutError("timing frequency PV read exceeded 20 seconds")
        if "error" in outcome:
            raise outcome["error"]
        return float(outcome["value"])

    def _handle_metadata_query(
        self,
        request: MetadataQueryRequest,
    ) -> MetadataQueryResponse:
        if self.metadata is None:
            return MetadataQueryResponse(
                PROTOCOL_VERSION,
                request.request_id,
                False,
                "MongoDB metadata catalog is unavailable",
                {},
                "METADATA_UNAVAILABLE",
            )
        try:
            if request.command == LIST_CAPTURE_TIMES:
                times = self.metadata.list_capture_times(
                    request.storage_date_local
                )
                return MetadataQueryResponse(
                    PROTOCOL_VERSION,
                    request.request_id,
                    True,
                    f"found {len(times)} capture time(s)",
                    {"times": list(times)},
                )
            unknown = [
                device_id
                for device_id in request.devices
                if device_id not in self.policy.devices
            ]
            if unknown:
                return MetadataQueryResponse(
                    PROTOCOL_VERSION,
                    request.request_id,
                    False,
                    f"unknown devices: {unknown!r}",
                    {},
                    "UNKNOWN_DEVICE",
                )
            records = self.metadata.find_waveform_files(
                storage_date_local=request.storage_date_local,
                time_sec=request.time_sec,
                device_ids=request.devices,
            )
            files = [
                {
                    "request_id": str(record["request_id"]),
                    "device_id": str(record["device_id"]),
                    "path": str(record["file"]["path"]),
                    "storage_id": str(record["file"]["storage_id"]),
                }
                for record in records
            ]
            return MetadataQueryResponse(
                PROTOCOL_VERSION,
                request.request_id,
                True,
                f"found {len(files)} waveform file(s)",
                {"files": files},
            )
        except Exception as exc:
            return MetadataQueryResponse(
                PROTOCOL_VERSION,
                request.request_id,
                False,
                str(exc) or type(exc).__name__,
                {},
                "METADATA_QUERY_FAILED",
            )

    def serve_forever(self, stop_event: threading.Event) -> None:
        socket = self.context.socket(zmq.REP)
        socket.setsockopt(zmq.LINGER, 0)
        socket.bind(self.endpoint)
        self._socket = socket
        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)
        try:
            while not stop_event.is_set():
                events = dict(poller.poll(200))
                if socket not in events:
                    continue
                response = self.handle_message(socket.recv())
                socket.send_string(encode_service_response(response))
        finally:
            poller.unregister(socket)
            socket.close()
            self._socket = None

    def close(self) -> None:
        self.tasks.close()


def _request_id_or_zero(message: str | bytes) -> str:
    zero = str(UUID(int=0))
    try:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        payload = json.loads(message)
        value = payload.get("request_id")
        return str(UUID(value)) if value is not None else zero
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return zero


def _rejected(
    request_id: str,
    message: str,
    error_code: str,
) -> CaptureResponse:
    return CaptureResponse(
        PROTOCOL_VERSION,
        request_id,
        False,
        "rejected",
        message,
        error_code,
    )


def _error_code(exc: Exception) -> str:
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).upper()
    return name or "CAPTURE_FAILED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
