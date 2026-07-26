from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from waveform_storage.config.registry import DeviceRegistry, load_registry
from waveform_storage.gui.beamline_selection import (
    build_beamline_areas,
    selected_device_ids,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BeamlineAreaSelectionTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        self.areas = build_beamline_areas(self.registry)

    def test_current_registry_groups_by_beamline_section(self):
        self.assertEqual(
            [area.area_id for area in self.areas],
            ["INJ1", "COL0", "L1", "BC1", "COL1"],
        )
        self.assertEqual(sum(area.device_count for area in self.areas), 47)
        self.assertEqual(sum(area.pv_count for area in self.areas), 47)

    def test_selection_expands_whole_area_only(self):
        selected = selected_device_ids(self.areas, {"L1"})
        l1 = next(area for area in self.areas if area.area_id == "L1")
        self.assertEqual(selected, l1.device_ids)
        self.assertEqual(len(selected), 5)

    def test_multiple_areas_preserve_registry_order(self):
        selected = selected_device_ids(self.areas, {"BC1", "INJ1"})
        expected = tuple(
            device.id
            for device in self.registry.devices
            if device.enabled and device.section in {"BC1", "INJ1"}
        )
        self.assertEqual(selected, expected)

    def test_selection_model_scales_to_400_devices(self):
        template = self.registry.devices[0]
        devices = tuple(
            replace(
                template,
                id=f"AREA_{index // 50:02d}_BPM_{index:03d}",
                section=f"AREA_{index // 50:02d}",
                display_name=f"AREA_{index // 50:02d}/BPM_{index:03d}",
                capture_pv=f"TEST:AREA:{index // 50:02d}:BPM:{index:03d}",
                order=index,
            )
            for index in range(400)
        )
        areas = build_beamline_areas(DeviceRegistry(1, devices))
        self.assertEqual(len(areas), 8)
        self.assertTrue(all(area.device_count == 50 for area in areas))
        self.assertEqual(
            len(selected_device_ids(areas, {area.area_id for area in areas})),
            400,
        )


if __name__ == "__main__":
    unittest.main()
