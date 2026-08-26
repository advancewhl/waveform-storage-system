"""Production component assembly for the acquisition v2 service."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping

import paramiko
from caproto.threading.client import Batch, Context

from waveform_storage.acquisition.protocol import CaptureRequest
from waveform_storage.acquisition.task import CaptureCancelled
from waveform_storage.application.capture_service import (
    CapturePlan,
    CaptureService,
    DeviceCaptureResult,
)
from waveform_storage.config.registry import DeviceRegistry, load_registry
from waveform_storage.io.hdf5_io import write_single_device_hdf5
from waveform_storage.io.txt_parser import parse_waveform_txt
from waveform_storage.transport.device_ssh import (
    DeviceDownload,
    DeviceSshGateway,
)
from waveform_storage.transport.epics_gateway import EpicsGateway


class ServerSettingsError(ValueError):
    """Raised when service settings are incomplete or unsafe."""


@dataclass(frozen=True)
class AcquisitionServerSettings:
    project_root: Path
    endpoint: str
    registry_path: Path
    task_state_dir: Path
    local_data_root: Path
    staging_root: Path
    device_ssh_user: str
    device_ssh_password: str | None = field(default=None, repr=False)
    device_ssh_key_file: Path | None = None
    device_ssh_known_hosts: Path | None = None
    ssh_connect_timeout_seconds: int = 30
    epics_connect_timeout_seconds: int = 10
    epics_pulse_seconds: int = 5
    ssh_max_workers: int = 17
    remote_file_settle_seconds: int = 2
    allow_remote_bind: bool = False
    max_capture_seconds: int = 3600
    max_capture_devices: int = 32

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "AcquisitionServerSettings":
        source = os.environ if environ is None else environ
        root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[3]
        )
        data_root = Path(
            _text(source, "WAVEFORM_LOCAL_DATA_ROOT", "/SHINE_Data/AC/LA")
        )
        key_file = _optional_path(source, "WAVEFORM_DEVICE_SSH_KEY_FILE")
        known_hosts = _optional_path(
            source, "WAVEFORM_DEVICE_SSH_KNOWN_HOSTS"
        )
        settings = cls(
            project_root=root,
            endpoint=_text(
                source,
                "WAVEFORM_SERVER_ENDPOINT",
                "tcp://127.0.0.1:5001",
            ),
            registry_path=Path(
                _text(
                    source,
                    "WAVEFORM_SERVER_REGISTRY",
                    str(root / "config" / "devices.json"),
                )
            ),
            task_state_dir=Path(
                _text(
                    source,
                    "WAVEFORM_TASK_STATE_DIR",
                    str(data_root / ".waveform-tasks"),
                )
            ),
            local_data_root=data_root,
            staging_root=Path(
                _text(
                    source,
                    "WAVEFORM_STAGING_ROOT",
                    str(data_root / ".waveform-staging"),
                )
            ),
            device_ssh_user=_text(
                source, "WAVEFORM_DEVICE_SSH_USER", "root"
            ),
            device_ssh_password=_optional_text(
                source, "WAVEFORM_DEVICE_SSH_PASSWORD"
            ),
            device_ssh_key_file=key_file,
            device_ssh_known_hosts=known_hosts,
            ssh_connect_timeout_seconds=_integer(
                source,
                "WAVEFORM_SSH_CONNECT_TIMEOUT_SECONDS",
                30,
                minimum=1,
                maximum=3600,
            ),
            epics_connect_timeout_seconds=_integer(
                source,
                "WAVEFORM_EPICS_CONNECT_TIMEOUT_SECONDS",
                10,
                minimum=1,
                maximum=3600,
            ),
            epics_pulse_seconds=_integer(
                source,
                "WAVEFORM_EPICS_PULSE_SECONDS",
                5,
                minimum=1,
                maximum=60,
            ),
            ssh_max_workers=_integer(
                source,
                "WAVEFORM_SSH_MAX_WORKERS",
                17,
                minimum=1,
                maximum=128,
            ),
            remote_file_settle_seconds=_integer(
                source,
                "WAVEFORM_REMOTE_FILE_SETTLE_SECONDS",
                2,
                minimum=0,
                maximum=300,
            ),
            allow_remote_bind=_boolean(
                source,
                "WAVEFORM_ALLOW_REMOTE_BIND",
                False,
            ),
            max_capture_seconds=_integer(
                source,
                "WAVEFORM_MAX_CAPTURE_SECONDS",
                3600,
                minimum=1,
                maximum=86_400,
            ),
            max_capture_devices=_integer(
                source,
                "WAVEFORM_MAX_CAPTURE_DEVICES",
                32,
                minimum=1,
                maximum=1000,
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.endpoint.startswith(
            ("tcp://", "ipc://", "inproc://")
        ):
            raise ServerSettingsError(
                "WAVEFORM_SERVER_ENDPOINT must be a ZMQ endpoint"
            )
        if (
            self.endpoint.startswith("tcp://")
            and not self.allow_remote_bind
        ):
            address = self.endpoint.removeprefix("tcp://").rsplit(":", 1)[0]
            if address not in {"127.0.0.1", "localhost", "[::1]", "::1"}:
                raise ServerSettingsError(
                    "non-loopback TCP bind requires "
                    "WAVEFORM_ALLOW_REMOTE_BIND=true"
                )
        if not self.registry_path.is_file():
            raise ServerSettingsError(
                f"registry does not exist: {self.registry_path}"
            )
        if self.device_ssh_key_file is not None:
            if not self.device_ssh_key_file.is_file():
                raise ServerSettingsError(
                    f"SSH key does not exist: {self.device_ssh_key_file}"
                )
        if self.device_ssh_known_hosts is None:
            raise ServerSettingsError(
                "WAVEFORM_DEVICE_SSH_KNOWN_HOSTS is required; "
                "unknown host keys are never accepted automatically"
            )
        if not self.device_ssh_known_hosts.is_file():
            raise ServerSettingsError(
                "WAVEFORM_DEVICE_SSH_KNOWN_HOSTS does not exist: "
                f"{self.device_ssh_known_hosts}"
            )
        host_keys = paramiko.HostKeys()
        try:
            host_keys.load(str(self.device_ssh_known_hosts))
        except (OSError, paramiko.SSHException) as exc:
            raise ServerSettingsError(
                "WAVEFORM_DEVICE_SSH_KNOWN_HOSTS is invalid: "
                f"{exc}"
            ) from exc
        if not host_keys.keys():
            raise ServerSettingsError(
                "WAVEFORM_DEVICE_SSH_KNOWN_HOSTS contains no host keys"
            )

    def public_values(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint,
            "registry_path": str(self.registry_path),
            "task_state_dir": str(self.task_state_dir),
            "local_data_root": str(self.local_data_root),
            "staging_root": str(self.staging_root),
            "device_ssh_user": self.device_ssh_user,
            "device_ssh_key_file": (
                str(self.device_ssh_key_file)
                if self.device_ssh_key_file is not None
                else None
            ),
            "device_ssh_known_hosts": str(
                self.device_ssh_known_hosts
            ),
            "ssh_connect_timeout_seconds": (
                self.ssh_connect_timeout_seconds
            ),
            "epics_connect_timeout_seconds": (
                self.epics_connect_timeout_seconds
            ),
            "epics_pulse_seconds": self.epics_pulse_seconds,
            "ssh_max_workers": self.ssh_max_workers,
            "remote_file_settle_seconds": (
                self.remote_file_settle_seconds
            ),
            "allow_remote_bind": self.allow_remote_bind,
            "max_capture_seconds": self.max_capture_seconds,
            "max_capture_devices": self.max_capture_devices,
        }


class ProductionCaptureRunner:
    """Resolve a validated request into EPICS, SFTP, TXT, and HDF5 work."""

    def __init__(
        self,
        settings: AcquisitionServerSettings,
        registry: DeviceRegistry,
        stop_event: threading.Event,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.stop_event = stop_event
        self.cancel_event = threading.Event()
        self.devices = {item.id: item for item in registry.devices}

    def __call__(self, request: CaptureRequest):
        plan = CapturePlan.from_protocol(request)
        selected = [self.devices[item] for item in plan.device_ids]
        self._configure_epics(selected)

        epics = EpicsGateway(
            Context,
            Batch,
            timeout=self.settings.epics_connect_timeout_seconds,
            pulse_seconds=self.settings.epics_pulse_seconds,
        )
        downloader = DeviceSshGateway(
            self._connect_device,
            max_workers=min(
                self.settings.ssh_max_workers,
                len(selected),
            ),
        )
        expected_points = {
            item.id: item.expected_points for item in selected
        }
        interval = capture_interval_seconds(plan.frequency_hz)
        deadline = time.monotonic() + plan.duration_seconds
        per_device = {item.id: [] for item in selected}
        sequence = 0

        while sequence == 0 or time.monotonic() < deadline:
            cycle_started_monotonic = time.monotonic()
            cycle_started = datetime.now().astimezone()
            cycle_epoch = cycle_started.timestamp()
            destinations = self._download_requests(
                selected,
                request.request_id,
                cycle_epoch,
                sequence,
            )
            device_by_path = {
                item.destination: item.device_id for item in destinations
            }

            def parser(path):
                device_id = device_by_path[Path(path)]
                return parse_waveform_txt(
                    path,
                    expected_points=expected_points[device_id],
                )

            def writer(device_id, waveform, _capture_plan):
                output_dir = (
                    self.settings.local_data_root
                    / cycle_started.strftime("%Y%m%d")
                    / "raw"
                    / "BIData"
                )
                destination = output_dir / (
                    f"RAW-{cycle_started:%H%M%S}-{device_id}-S{sequence:05d}.h5"
                )
                return write_single_device_hdf5(
                    destination,
                    device_id,
                    waveform,
                    metadata={
                        "protocol_version": 2,
                        "request_id": plan.request_id,
                        "frequency_hz": plan.frequency_hz,
                        "duration_seconds": plan.duration_seconds,
                        "capture_sequence": sequence,
                        "captured_at": cycle_started.isoformat(),
                    },
                )

            remaining = max(0.0, deadline - time.monotonic())
            cycle_plan = CapturePlan(
                plan.request_id,
                plan.frequency_hz,
                max(1, int(min(interval, remaining) or 1)),
                plan.device_ids,
                plan.area_ids,
            )
            service = CaptureService(
                epics,
                downloader,
                parser,
                writer,
                waiter=lambda seconds: self._wait_interruptibly(
                    min(float(seconds), max(0.0, deadline - time.monotonic()))
                ),
            )
            cycle_results = service.capture(
                cycle_plan,
                [(item.id, item.capture_pv) for item in selected],
                destinations,
            )
            for result in cycle_results:
                per_device[result.device_id].append(result)
            sequence += 1
            cycle_not_before = cycle_started_monotonic + interval
            if time.monotonic() < min(deadline, cycle_not_before):
                self._wait_interruptibly(
                    min(deadline, cycle_not_before) - time.monotonic()
                )
            if time.monotonic() >= deadline:
                break

        aggregated = []
        for device_id in plan.device_ids:
            cycles = per_device[device_id]
            paths = tuple(
                item.output_path
                for item in cycles
                if item.success and item.output_path is not None
            )
            errors = [
                item.error or "unknown cycle failure"
                for item in cycles
                if not item.success
            ]
            aggregated.append(
                DeviceCaptureResult(
                    device_id,
                    not errors,
                    paths[-1] if paths else None,
                    "; ".join(errors) if errors else None,
                    paths,
                    len(cycles),
                    len(paths),
                )
            )
        return tuple(aggregated)

    def _configure_epics(self, selected) -> None:
        addresses = sorted(
            {f"{item.epics_host}:{item.epics_port}" for item in selected}
        )
        os.environ["EPICS_CA_ADDR_LIST"] = " ".join(addresses)
        os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"

    def _download_requests(
        self,
        selected,
        request_id: str,
        started_epoch: float,
        sequence: int = 0,
    ) -> list[DeviceDownload]:
        staging = self.settings.staging_root / request_id / f"S{sequence:05d}"
        return [
            DeviceDownload(
                item.id,
                item.ssh_host,
                item.remote_data_dir,
                staging / f"{item.id}.txt",
                file_pattern=item.file_pattern,
                not_before_epoch=started_epoch,
                settle_seconds=(
                    self.settings.remote_file_settle_seconds
                ),
            )
            for item in selected
        ]

    def _connect_device(self, request: DeviceDownload):
        device = self.devices[request.device_id]
        client = paramiko.SSHClient()
        client.load_host_keys(
            str(self.settings.device_ssh_known_hosts)
        )
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        discover_keys = (
            self.settings.device_ssh_key_file is None
            and self.settings.device_ssh_password is None
        )
        client.connect(
            hostname=device.ssh_host,
            port=device.ssh_port,
            username=self.settings.device_ssh_user,
            password=self.settings.device_ssh_password,
            key_filename=(
                str(self.settings.device_ssh_key_file)
                if self.settings.device_ssh_key_file is not None
                else None
            ),
            look_for_keys=discover_keys,
            allow_agent=discover_keys,
            timeout=self.settings.ssh_connect_timeout_seconds,
            auth_timeout=self.settings.ssh_connect_timeout_seconds,
            banner_timeout=self.settings.ssh_connect_timeout_seconds,
        )
        return client

    def _wait_interruptibly(self, duration_seconds: float) -> None:
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            if self.stop_event.is_set() or self.cancel_event.is_set():
                raise CaptureCancelled()
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    def cancel(self) -> None:
        """Request cancellation at the next safe runner boundary."""

        self.cancel_event.set()

    def prepare(self) -> None:
        """Reset per-task cancellation before a request is accepted."""

        self.cancel_event.clear()


_CAPTURE_INTERVAL_SECONDS = {
    1000.0: 2,
    100.0: 12,
    50.0: 22,
    2.0: 512,
}


def capture_interval_seconds(frequency_hz: float) -> int:
    """Return the legacy-derived delay between trigger and retrieval."""

    value = float(frequency_hz)
    try:
        return _CAPTURE_INTERVAL_SECONDS[value]
    except KeyError as exc:
        supported = ", ".join(f"{item:g}" for item in _CAPTURE_INTERVAL_SECONDS)
        raise ServerSettingsError(
            f"unsupported capture frequency {value:g} Hz; supported: {supported} Hz"
        ) from exc


def load_server_registry(
    settings: AcquisitionServerSettings,
) -> DeviceRegistry:
    registry = load_registry(settings.registry_path)
    if not any(item.acquisition_enabled for item in registry.devices):
        raise ServerSettingsError(
            "server registry has no acquisition-enabled devices"
        )
    host_keys = paramiko.HostKeys(
        str(settings.device_ssh_known_hosts)
    )
    missing_host_keys = []
    for item in registry.devices:
        if not item.acquisition_enabled:
            continue
        host_key = (
            item.ssh_host
            if item.ssh_port == 22
            else f"[{item.ssh_host}]:{item.ssh_port}"
        )
        if host_keys.lookup(host_key) is None:
            missing_host_keys.append(host_key)
    if missing_host_keys:
        raise ServerSettingsError(
            "known_hosts is missing acquisition device(s): "
            + ", ".join(sorted(set(missing_host_keys)))
        )
    return registry


def _text(
    source: Mapping[str, str],
    name: str,
    default: str,
) -> str:
    value = source.get(name)
    return default if value is None or value == "" else value


def _optional_text(
    source: Mapping[str, str],
    name: str,
) -> str | None:
    value = source.get(name)
    return None if value is None or value == "" else value


def _optional_path(
    source: Mapping[str, str],
    name: str,
) -> Path | None:
    value = _optional_text(source, name)
    return Path(value) if value is not None else None


def _integer(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ServerSettingsError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ServerSettingsError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _boolean(
    source: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ServerSettingsError(f"{name} must be a boolean")
