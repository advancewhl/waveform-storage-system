"""Registry-driven device binding specifications for current and future GUIs."""

from __future__ import annotations

from dataclasses import dataclass

from waveform_storage.config.registry import DeviceRegistry


DEVICE_PREFIX = "AC_LA_"
DEVICE_SEPARATOR = "_BI_"


@dataclass(frozen=True)
class DeviceButtonSpec:
    device_id: str
    widget_name: str
    order: int
    default_selected: bool


def legacy_widget_name(device_id: str) -> str:
    """Map a canonical device ID to the current Designer button object name."""

    if not device_id.startswith(DEVICE_PREFIX):
        raise ValueError(f"unsupported device ID: {device_id!r}")
    body = device_id[len(DEVICE_PREFIX):]
    if DEVICE_SEPARATOR not in body:
        raise ValueError(f"unsupported device ID: {device_id!r}")
    section, name = body.split(DEVICE_SEPARATOR, 1)
    if not section or not name:
        raise ValueError(f"unsupported device ID: {device_id!r}")
    return f"{section}_{name}"


def build_button_specs(registry: DeviceRegistry) -> tuple[DeviceButtonSpec, ...]:
    """Build stable binding specs for enabled registry devices."""

    return tuple(
        DeviceButtonSpec(
            device_id=device.id,
            widget_name=legacy_widget_name(device.id),
            order=device.order,
            default_selected=device.default_selected,
        )
        for device in sorted(registry.devices, key=lambda item: item.order)
        if device.enabled
    )
