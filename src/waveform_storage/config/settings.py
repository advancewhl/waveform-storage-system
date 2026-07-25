"""Typed application settings with legacy-compatible defaults.

The settings object is deliberately independent of Qt and network libraries.
Passwords remain available to the adapters that need them, but are excluded
from representations and public serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Mapping


class SettingsError(ValueError):
    """Raised when an application setting cannot be parsed safely."""


def _text(source: Mapping[str, str], name: str, default: str) -> str:
    value = source.get(name)
    return default if value is None or value == "" else value


def _optional_text(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name)
    return None if value is None or value == "" else value


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
        raise SettingsError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise SettingsError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


@dataclass(frozen=True)
class AppSettings:
    """All currently configurable application values and legacy defaults."""

    project_root: Path
    config_dir: Path
    zmq_server_host: str = "192.0.2.2"
    zmq_server_port: int = 5000
    zmq_receive_timeout_ms: int = 3000
    storage_host: str = "192.0.2.2"
    storage_port: int = 22
    storage_user: str = "storage-user"
    storage_password: str | None = field(default=None, repr=False)
    storage_data_root: str = "/SHINE_Data/AC/LA"
    device_ssh_user: str = "root"
    device_ssh_password: str | None = field(default=None, repr=False)
    device_ssh_port: int = 22
    device_remote_dir: str = "/home/data"
    local_data_root: Path = Path("/SHINE_Data/AC/LA")
    ssh_connect_timeout_seconds: int = 30
    epics_connect_timeout_seconds: int = 10
    alignment_export_format: str = "auto"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "AppSettings":
        source = os.environ if environ is None else environ
        root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[3]
        )
        config_dir = Path(
            _text(source, "WAVEFORM_CONFIG_DIR", str(root / "config"))
        )
        export_format = _text(
            source,
            "WAVEFORM_ALIGNMENT_EXPORT_FORMAT",
            "auto",
        ).lower()
        if export_format not in {"auto", "legacy", "v2"}:
            raise SettingsError(
                "WAVEFORM_ALIGNMENT_EXPORT_FORMAT must be auto, legacy, or v2"
            )

        return cls(
            project_root=root,
            config_dir=config_dir,
            zmq_server_host=_text(
                source, "WAVEFORM_ZMQ_SERVER_HOST", "192.0.2.2"
            ),
            zmq_server_port=_integer(
                source,
                "WAVEFORM_ZMQ_SERVER_PORT",
                5000,
                minimum=1,
                maximum=65535,
            ),
            zmq_receive_timeout_ms=_integer(
                source,
                "WAVEFORM_ZMQ_RECEIVE_TIMEOUT_MS",
                3000,
                minimum=1,
                maximum=3_600_000,
            ),
            storage_host=_text(
                source, "WAVEFORM_STORAGE_HOST", "192.0.2.2"
            ),
            storage_port=_integer(
                source,
                "WAVEFORM_STORAGE_PORT",
                22,
                minimum=1,
                maximum=65535,
            ),
            storage_user=_text(source, "WAVEFORM_STORAGE_USER", "storage-user"),
            storage_password=_optional_text(
                source, "WAVEFORM_STORAGE_PASSWORD"
            ),
            storage_data_root=_text(
                source, "WAVEFORM_STORAGE_DATA_ROOT", "/SHINE_Data/AC/LA"
            ),
            device_ssh_user=_text(
                source, "WAVEFORM_DEVICE_SSH_USER", "root"
            ),
            device_ssh_password=_optional_text(
                source, "WAVEFORM_DEVICE_SSH_PASSWORD"
            ),
            device_ssh_port=_integer(
                source,
                "WAVEFORM_DEVICE_SSH_PORT",
                22,
                minimum=1,
                maximum=65535,
            ),
            device_remote_dir=_text(
                source, "WAVEFORM_DEVICE_REMOTE_DIR", "/home/data"
            ),
            local_data_root=Path(
                _text(
                    source,
                    "WAVEFORM_LOCAL_DATA_ROOT",
                    "/SHINE_Data/AC/LA",
                )
            ),
            ssh_connect_timeout_seconds=_integer(
                source,
                "WAVEFORM_SSH_CONNECT_TIMEOUT_SECONDS",
                30,
                minimum=1,
                maximum=86_400,
            ),
            epics_connect_timeout_seconds=_integer(
                source,
                "WAVEFORM_EPICS_CONNECT_TIMEOUT_SECONDS",
                10,
                minimum=1,
                maximum=86_400,
            ),
            alignment_export_format=export_format,
        )

    def public_values(self) -> dict[str, object]:
        """Return settings safe for logging or diagnostics."""

        return {
            "project_root": str(self.project_root),
            "config_dir": str(self.config_dir),
            "zmq_server_host": self.zmq_server_host,
            "zmq_server_port": self.zmq_server_port,
            "zmq_receive_timeout_ms": self.zmq_receive_timeout_ms,
            "storage_host": self.storage_host,
            "storage_port": self.storage_port,
            "storage_user": self.storage_user,
            "storage_data_root": self.storage_data_root,
            "device_ssh_user": self.device_ssh_user,
            "device_ssh_port": self.device_ssh_port,
            "device_remote_dir": self.device_remote_dir,
            "local_data_root": str(self.local_data_root),
            "ssh_connect_timeout_seconds": self.ssh_connect_timeout_seconds,
            "epics_connect_timeout_seconds": self.epics_connect_timeout_seconds,
            "alignment_export_format": self.alignment_export_format,
        }
