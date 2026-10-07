"""Reference beamline display grouping for the operator UI."""

from __future__ import annotations

from waveform_storage.config.beamline_areas import (
    BeamlineArea,
    build_beamline_areas,
    selected_device_ids,
)
from waveform_storage.config.registry import DeviceRegistry


# The reference control screen follows the accelerator path. These are UI
# groupings only; the acquisition service still uses registry sections.
REFERENCE_AREAS = (
    ("BI", ("AC_LA_INJ1",)),
    ("COL0", ("AC_LA_COL0",)),
    ("L1", ("AC_LA_L1",)),
    ("BC1", ("AC_LA_BC1",)),
    ("COL1", ("AC_LA_COL1",)),
    ("L2", ("AC_LA_L2",)),
    ("BC2", ("AC_LA_BC2",)),
    ("L3", ("AC_LA_L3",)),
    ("BRK", ("AC_LA_BRK",)),
    ("EXT1&2", ("AC_LA_EXT1", "AC_LA_EXT2")),
    ("EXT3&4", ("AC_LA_EXT3", "AC_LA_EXT4")),
    ("BC3", ("AC_LA_BC3",)),
)


def build_display_areas(registry: DeviceRegistry) -> tuple[BeamlineArea, ...]:
    """Show reference segments first while retaining every registered device."""

    areas = build_beamline_areas(registry)
    if "AC_LA_INJ1" not in {area.area_id for area in areas}:
        return areas

    by_section = {area.area_id: area for area in areas}
    used: set[str] = set()
    display: list[BeamlineArea] = []
    for name, sections in REFERENCE_AREAS:
        members = [by_section[section] for section in sections if section in by_section]
        if not members:
            continue
        used.update(area.area_id for area in members)
        display.append(
            BeamlineArea(
                area_id=name,
                display_name="ING" if name == "BI" else name,
                device_ids=tuple(device_id for area in members for device_id in area.device_ids),
                capture_pvs=tuple(pv for area in members for pv in area.capture_pvs),
            )
        )
    display.extend(area for area in areas if area.area_id not in used)
    return tuple(display)
