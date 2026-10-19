from __future__ import annotations

import unittest

from waveform_storage.processing.alignment import DeviceSeries, SkippedDevice
from waveform_storage.processing.bunch_id_diagnostics import diagnose_raw_steps


def series(device_id: str, steps: list[int]) -> DeviceSeries:
    ids = [100]
    for step in steps:
        ids.append(ids[-1] + step)
    return DeviceSeries(device_id, ids, list(range(len(ids))), list(range(len(ids))))


class RawStepDiagnosticsTests(unittest.TestCase):
    def test_reports_observed_mode_and_only_clear_cross_device_majority(self):
        report = diagnose_raw_steps(
            [
                series("A", [2000, 2000, 2000, 2000, 2100]),
                series("B", [2000] * 5),
                series("C", [2000] * 5),
                series("D", [2000] * 5),
                series("E", [28, 28, 28, 28, 76]),
            ],
            requested_devices=("A", "B", "C", "D", "E"),
        )

        self.assertEqual((report.majority_step, report.majority_count,
                          report.majority_denominator), (2000, 4, 5))
        self.assertTrue(report.has_clear_majority)
        self.assertFalse(report.raw_step_compatible)
        self.assertIn("与多数设备不一致", report.summary)
        by_id = {item.device_id: item for item in report.devices}
        self.assertEqual(by_id["A"].unique_steps, (2000, 2100))
        self.assertEqual(by_id["A"].modal_step, 2000)
        self.assertTrue(by_id["E"].differs_from_majority)
        self.assertEqual(by_id["E"].minimum_step, 28)
        self.assertEqual(by_id["E"].maximum_step, 76)

    def test_no_strict_majority_is_reported_as_unknown(self):
        report = diagnose_raw_steps(
            [series("A", [2, 2]), series("B", [3, 3])],
            requested_devices=("A", "B"),
        )
        self.assertFalse(report.has_clear_majority)
        self.assertIsNone(report.majority_step)
        self.assertIn("无法确定", report.summary)
        self.assertFalse(report.raw_step_compatible)

    def test_missing_failed_and_nonmonotonic_devices_are_retained(self):
        report = diagnose_raw_steps(
            [DeviceSeries("A", [1, 3, 2], [1, 2, 3], [4, 5, 6])],
            requested_devices=("A", "B", "C"),
            failed_devices=(SkippedDevice("B", "read_failed", "fixture"),),
        )
        self.assertEqual(report.missing_devices, ("C",))
        self.assertEqual(report.failed_devices[0].device_id, "B")
        self.assertFalse(report.devices[0].strictly_increasing)
        self.assertIn("连续性无法确认", report.summary)

    def test_irregular_raw_gaps_and_zero_ids_do_not_look_compatible(self):
        report = diagnose_raw_steps(
            [
                series("A", [2000, 2000, 2100]),
                DeviceSeries("B", [0, 2000, 4000], [1, 2, 3], [4, 5, 6]),
            ],
            requested_devices=("A", "B"),
        )
        self.assertFalse(report.raw_step_compatible)
        by_id = {item.device_id: item for item in report.devices}
        self.assertIn("整数倍", by_id["A"].issue)
        self.assertEqual(by_id["B"].zero_count, 1)
        self.assertIn("零值", by_id["B"].issue)


if __name__ == "__main__":
    unittest.main()
