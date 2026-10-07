"""Pure registry-area grouping shared by server policy and operator UI."""

from __future__ import annotations

from dataclasses import dataclass

from waveform_storage.config.registry import DeviceRegistry


@dataclass(frozen=True)
class BeamlineArea:
    """One atomic selection unit containing multiple BPM devices."""

    area_id: str
    display_name: str
    device_ids: tuple[str, ...]
    capture_pvs: tuple[str, ...]

    @property
    def device_count(self) -> int:
        return len(self.device_ids)

    @property
    def pv_count(self) -> int:
        return len(self.capture_pvs)


def build_beamline_areas(registry: DeviceRegistry) -> tuple[BeamlineArea, ...]:
    """Group enabled devices by section while preserving registry order."""

    grouped: dict[str, list[object]] = {}
    for device in sorted(registry.devices, key=lambda item: item.order):
        if device.enabled:
            grouped.setdefault(device.section, []).append(device)

    return tuple(
        BeamlineArea(
            area_id=section,
            display_name=(
                devices[0].display_name.rsplit("/", 1)[0]
                if "/" in devices[0].display_name
                else section
            ),
            device_ids=tuple(device.id for device in devices),
            capture_pvs=tuple(device.capture_pv for device in devices),
        )
        for section, devices in grouped.items()
    )


def selected_device_ids(
    areas: tuple[BeamlineArea, ...],
    selected_area_ids: set[str] | frozenset[str],
) -> tuple[str, ...]:
    """Expand selected areas into an ordered, duplicate-free device list."""

    result: list[str] = []
    seen: set[str] = set()
    for area in areas:
        if area.area_id not in selected_area_ids:
            continue
        for device_id in area.device_ids:
            if device_id not in seen:
                result.append(device_id)
                seen.add(device_id)
    return tuple(result)
