from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERLAP_PATH = PROJECT_ROOT / "apps" / "operator_gui" / "overlap.py"
BASE = 38_212_018_001_007


def _load_overlap_module():
    spec = importlib.util.spec_from_file_location(
        "operator_gui_overlap_for_test",
        OVERLAP_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {OVERLAP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LegacyGuiExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overlap = _load_overlap_module()

    def test_create_hdf5_preserves_uint64_and_filtered_metadata(self):
        bunch_ids = np.array([BASE, BASE + 500_000], dtype=np.uint64)
        devices = ["BPM_A", "BPM_SKIPPED", "BPM_C"]
        x = [[1, 2], [10, 20]]
        y = [[3, 4], [30, 40]]

        with tempfile.TemporaryDirectory() as directory:
            output = self.overlap.create_hdf5(
                bunchID=bunch_ids,
                x_new=x,
                y_new=y,
                devicename=devices,
                runID="20251112",
                time_sec="120721",
                skip_ind=[1],
                save_path=directory,
            )
            with h5py.File(output, "r") as h5:
                stored_ids = h5["BunchIDData"][:]
                stored_devices = [
                    value.decode() if isinstance(value, bytes) else str(value)
                    for value in h5.attrs["Device"]
                ]
                labels = [
                    str(h5.attrs[f"Data[0:]_{index}"])
                    for index in range(2)
                ]

        self.assertEqual(stored_ids.dtype, np.dtype(np.uint64))
        np.testing.assert_array_equal(stored_ids[0], bunch_ids)
        self.assertEqual(stored_devices, ["BPM_A", "BPM_C"])
        self.assertEqual(labels, ["BPM_A_X", "BPM_C_X"])


if __name__ == "__main__":
    unittest.main()
