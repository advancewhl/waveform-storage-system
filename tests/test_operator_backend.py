from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import h5py

from waveform_storage.config.registry import load_registry
from waveform_storage.gui.operator_backend import DemoOperatorBackend


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DemoOperatorBackendTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        self.backend = DemoOperatorBackend(self.registry)
        self.devices = tuple(device.id for device in self.registry.devices[:3])

    def test_capture_is_offline_v2_round_trip_with_atomic_area_ids(self):
        result = self.backend.start_capture(("INJ1",), self.devices, 600)
        payload = json.loads(self.backend.last_request_json)
        self.assertTrue(result.accepted)
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["area_ids"], ["INJ1"])
        self.assertEqual(payload["devices"], list(self.devices))

    def test_demo_retrieval_alignment_export_and_raw_download(self):
        selected_date = date(2026, 7, 26)
        self.assertEqual(
            self.backend.retrieve_times(selected_date),
            ("083000", "120000", "154500"),
        )
        result = self.backend.align(selected_date, "083000", self.devices)
        self.assertEqual(result.device_ids, tuple(sorted(self.devices)))
        self.assertEqual(result.bunch_ids.size, 64)
        with tempfile.TemporaryDirectory() as directory:
            exported = self.backend.export_alignment(
                result, selected_date, "083000", directory
            )
            raw = self.backend.download_raw(
                selected_date, "083000", self.devices, directory
            )
            self.assertTrue(exported.is_file())
            self.assertEqual(len(raw), len(self.devices))
            with h5py.File(raw[0], "r") as h5:
                self.assertIn("BunchIDData", h5)
                self.assertIn("Data", h5)


if __name__ == "__main__":
    unittest.main()
