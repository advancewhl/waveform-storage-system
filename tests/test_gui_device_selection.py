from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from waveform_storage.config.registry import load_registry
from waveform_storage.gui.device_selection import (
    build_button_specs,
    legacy_widget_name,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeviceButtonSpecTests(unittest.TestCase):
    def test_current_registry_drives_order_names_and_defaults(self):
        registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        specs = build_button_specs(registry)

        self.assertEqual(len(specs), 47)
        self.assertEqual([item.order for item in specs], list(range(47)))
        self.assertEqual(
            sum(item.default_selected for item in specs),
            12,
        )
        self.assertEqual(specs[0].widget_name, "INJ1_SBPM01")
        self.assertEqual(
            specs[0].device_id,
            "AC_LA_INJ1_BI_SBPM01",
        )

    def test_legacy_widget_name_rejects_ambiguous_ids(self):
        self.assertEqual(
            legacy_widget_name("AC_LA_BC1_BI_SBPM02"),
            "BC1_SBPM02",
        )
        for invalid in ("BC1_SBPM02", "AC_LA_BC1_SBPM02", "AC_LA__BI_X"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                legacy_widget_name(invalid)

    def test_every_default_device_has_a_current_ui_button(self):
        registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        default_widgets = {
            item.widget_name
            for item in build_button_specs(registry)
            if item.default_selected
        }
        root = ET.parse(
            PROJECT_ROOT / "apps" / "operator_gui" / "mainWindow.ui"
        ).getroot()
        current_widgets = {
            element.attrib["name"]
            for element in root.iter("widget")
            if "name" in element.attrib
        }
        self.assertEqual(default_widgets - current_widgets, set())


if __name__ == "__main__":
    unittest.main()
