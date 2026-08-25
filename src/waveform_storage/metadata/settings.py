"""Environment configuration for the optional MongoDB metadata module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


class MongoSettingsError(ValueError):
    pass


def _boolean(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MongoSettingsError(f"{name} must be a boolean")


def _integer(
    source: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = source.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise MongoSettingsError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise MongoSettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class MongoSettings:
    enabled: bool = False
    uri: str | None = field(default=None, repr=False)
    database: str = "waveform_storage"
    server_selection_timeout_ms: int = 3000
    required: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "MongoSettings":
        enabled = _boolean(environ, "WAVEFORM_MONGODB_ENABLED", False)
        uri = environ.get("WAVEFORM_MONGODB_URI") or None
        database = environ.get("WAVEFORM_MONGODB_DATABASE") or "waveform_storage"
        required = _boolean(environ, "WAVEFORM_MONGODB_REQUIRED", False)
        timeout = _integer(
            environ,
            "WAVEFORM_MONGODB_SERVER_SELECTION_TIMEOUT_MS",
            3000,
            100,
            300_000,
        )
        if enabled and not uri:
            raise MongoSettingsError(
                "WAVEFORM_MONGODB_URI is required when MongoDB is enabled"
            )
        if required and not enabled:
            raise MongoSettingsError(
                "WAVEFORM_MONGODB_REQUIRED requires WAVEFORM_MONGODB_ENABLED"
            )
        if not database.strip():
            raise MongoSettingsError("WAVEFORM_MONGODB_DATABASE must not be empty")
        return cls(enabled, uri, database.strip(), timeout, required)

    def public_values(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "database": self.database,
            "server_selection_timeout_ms": self.server_selection_timeout_ms,
            "required": self.required,
            "uri_configured": bool(self.uri),
        }
