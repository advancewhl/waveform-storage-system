"""Validated device registry."""

from .registry import (
    Device,
    DeviceRegistry,
    RegistryValidationError,
    import_legacy_registry,
    load_registry,
    render_legacy_json_lines,
    save_registry,
    validate_registry,
)
from .settings import AppSettings, SettingsError

__all__ = [
    "Device",
    "DeviceRegistry",
    "RegistryValidationError",
    "import_legacy_registry",
    "load_registry",
    "render_legacy_json_lines",
    "save_registry",
    "validate_registry",
    "AppSettings",
    "SettingsError",
]
