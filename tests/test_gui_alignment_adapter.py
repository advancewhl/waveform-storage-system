from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import matplotlib.pyplot as plt
import numpy as np

from waveform_storage.gui.plotting import plot_alignment_result, plot_device_waveform
from waveform_storage.io.hdf5_io import (
    read_alignment_v2,
    read_legacy_merged_hdf5,
)
from waveform_storage.processing.alignment import (
    AlignmentParameters,
    DeviceSeries,
    align_devices,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERLAP_PATH = PROJECT_ROOT / "apps" / "operator_gui" / "overlap.py"


def _load_overlap_module():
    spec = importlib.util.spec_from_file_location(
        "operator_gui_alignment_adapter_for_test",
        OVERLAP_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {OVERLAP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AlignmentResultCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overlap = _load_overlap_module()

    def test_same_query_reuses_one_result_and_clear_invalidates_it(self):
        cache = self.overlap.AlignmentResultCache()
        calls = []

        def loader():
            calls.append(object())
            return calls[-1]

        first = cache.get_or_load(("20260725", "120000"), loader)
        second = cache.get_or_load(("20260725", "120000"), loader)
        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

        cache.clear()
        third = cache.get_or_load(("20260725", "120000"), loader)
        self.assertIsNot(first, third)
        self.assertEqual(len(calls), 2)


class AlignmentResultAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overlap = _load_overlap_module()

    def test_remote_adapter_orders_known_devices_and_reports_unknown(self):
        records = [
            DeviceSeries("BPM_A", [1, 2], [10, 11], [20, 21]),
            DeviceSeries("BPM_B", [1, 2], [30, 31], [40, 41]),
        ]
        with patch.object(
            self.overlap,
            "remote_find_h5_files",
            return_value=(
                ["/tmp/b.h5", "/tmp/unknown.h5", "/tmp/a.h5"],
                ["BPM_B", "BPM_UNKNOWN", "BPM_A"],
            ),
        ), patch.object(
            self.overlap,
            "read_h5_device_series",
            return_value=(records, []),
        ) as reader:
            result = self.overlap.main_alignment_process(
                "/remote",
                "20260725",
                "120000",
                ["BPM_A", "BPM_B"],
            )

        reader.assert_called_once_with(
            [("/tmp/a.h5", "BPM_A"), ("/tmp/b.h5", "BPM_B")]
        )
        self.assertEqual(result.device_ids, ("BPM_A", "BPM_B"))
        self.assertEqual(result.skipped_devices[0].device_id, "BPM_UNKNOWN")
        self.assertEqual(result.skipped_devices[0].reason, "unknown_device")

    def test_plot_uses_valid_mask_but_keeps_real_zero(self):
        result = align_devices(
            [
                DeviceSeries("BPM_A", [1, 2], [0, 11], [20, 21]),
                DeviceSeries("BPM_B", [2, 3], [30, 31], [40, 41]),
            ],
            AlignmentParameters(
                mode="coverage",
                min_id_device_coverage=0.5,
                min_device_id_coverage=0.5,
            ),
        )
        fig = self.overlap.plot_alignment_result(result, 0)
        try:
            x_line = fig.axes[0].lines[0]
            self.assertEqual(list(x_line.get_xdata()), ["BPM_A"])
            self.assertEqual(list(x_line.get_ydata()), [0])
        finally:
            plt.close(fig)

    def test_distribution_keeps_all_devices_with_sparse_wrapped_labels(self):
        device_ids = tuple(
            f"AC_LA_L{index // 5 + 1}_BI_COLDBPM{index + 1:02d}"
            for index in range(25)
        )
        result = align_devices([
            DeviceSeries(device_id, [1000, 3000], [index, index + 1], [index + 2, index + 3])
            for index, device_id in enumerate(device_ids)
        ])

        figure = plot_alignment_result(result, 0)
        try:
            axis = figure.axes[0]
            self.assertEqual(len(axis.lines[0].get_xdata()), 25)
            self.assertEqual(len(axis.get_xticklabels()), 6)
            labels = [label.get_text() for label in axis.get_xticklabels()]
            self.assertEqual(labels[0].replace("\n", ""), device_ids[0])
            self.assertEqual(labels[-1].replace("\n", ""), device_ids[-1])
        finally:
            plt.close(figure)

    def test_single_device_plot_uses_bunch_id_as_x_axis(self):
        result = align_devices(
            [
                DeviceSeries(
                    "BPM_A",
                    [38_212_018_001_007, 38_212_018_501_007],
                    [10, 11],
                    [20, 21],
                )
            ]
        )
        fig = plot_device_waveform(result, "BPM_A")
        try:
            axis = fig.axes[0]
            self.assertEqual(
                [int(value) for value in axis.lines[0].get_xdata()],
                result.bunch_ids.tolist(),
            )
            self.assertEqual(axis.get_xlabel().split("·")[0].strip(), "BunchID")
            self.assertEqual(
                [text.get_text() for text in axis.get_legend().get_texts()],
                ["X position", "Y position"],
            )
            self.assertEqual(axis.get_legend().get_title().get_text(), "")
            self.assertTrue(all(spine.get_visible() for spine in axis.spines.values()))
            self.assertGreaterEqual(axis.spines["left"].get_linewidth(), 1.0)
        finally:
            plt.close(fig)

    def test_auto_export_uses_legacy_when_complete_and_v2_when_masked(self):
        complete = align_devices(
            [
                DeviceSeries("BPM_A", [1, 2], [10, 11], [20, 21]),
                DeviceSeries("BPM_B", [1, 2], [30, 31], [40, 41]),
            ]
        )
        masked = align_devices(
            [
                DeviceSeries("BPM_A", [1, 2], [10, 11], [20, 21]),
                DeviceSeries("BPM_B", [2, 3], [30, 31], [40, 41]),
            ],
            AlignmentParameters(
                mode="coverage",
                min_id_device_coverage=0.5,
                min_device_id_coverage=0.5,
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            legacy_path = self.overlap.save_alignment_result(
                complete,
                "20260725",
                "120000",
                directory,
            )
            v2_path = self.overlap.save_alignment_result(
                masked,
                "20260725",
                "120001",
                directory,
            )
            legacy = read_legacy_merged_hdf5(legacy_path)
            v2 = read_alignment_v2(v2_path)

        self.assertTrue(legacy_path.endswith("_BPMdata.h5"))
        self.assertTrue(v2_path.endswith("_BPMdata_v2.h5"))
        self.assertEqual(legacy.result.device_ids, complete.device_ids)
        np.testing.assert_array_equal(v2.result.valid_mask, masked.valid_mask)


if __name__ == "__main__":
    unittest.main()
