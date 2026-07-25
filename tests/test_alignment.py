from __future__ import annotations

import unittest

import numpy as np

from waveform_storage.processing.alignment import (
    AlignmentParameters,
    DeviceSeries,
    align_devices,
)


def record(device_id, bunch_ids, x=None, y=None):
    count = len(bunch_ids)
    return DeviceSeries(
        device_id=device_id,
        bunch_ids=bunch_ids,
        x=np.arange(count, dtype=np.int64) if x is None else x,
        y=np.arange(count, dtype=np.int64) + 100 if y is None else y,
    )


class StrictAlignmentTests(unittest.TestCase):
    def test_empty_input_is_safe(self):
        result = align_devices([])

        self.assertEqual(result.device_ids, ())
        self.assertEqual(result.bunch_ids.dtype, np.dtype(np.uint64))
        self.assertEqual(result.x.shape, (0, 0))

    def test_preserves_large_uint64_ids_and_extracts_values(self):
        base = 38_212_018_001_007
        first = record("B", [base, base + 500_000, base + 1_000_000], [1, 2, 3], [4, 5, 6])
        second = record("A", [base + 500_000, base + 1_000_000], [20, 30], [50, 60])

        result = align_devices([first, second])

        self.assertEqual(result.device_ids, ("A", "B"))
        np.testing.assert_array_equal(
            result.bunch_ids,
            np.array([base + 500_000, base + 1_000_000], dtype=np.uint64),
        )
        np.testing.assert_array_equal(result.x, [[20, 30], [2, 3]])
        self.assertTrue(result.valid_mask.all())

    def test_no_intersection_returns_reasons_instead_of_crashing(self):
        result = align_devices([record("A", [1, 2]), record("B", [10, 11])])

        self.assertEqual(result.device_ids, ())
        self.assertEqual(result.bunch_ids.size, 0)
        self.assertEqual(
            {item.device_id for item in result.skipped_devices},
            {"A", "B"},
        )
        self.assertEqual(
            {item.reason for item in result.skipped_devices},
            {"no_common_bunch_ids"},
        )

    def test_zero_bunch_id_is_kept_by_default_and_xy_zero_is_data(self):
        result = align_devices(
            [
                record("A", [0, 1], [0, 7], [3, 0]),
                record("B", [0, 1], [4, 5], [0, 6]),
            ]
        )

        np.testing.assert_array_equal(result.bunch_ids, [0, 1])
        self.assertEqual(result.x[0, 0], 0)
        self.assertEqual(result.y[0, 1], 0)
        self.assertTrue(result.valid_mask.all())

    def test_unsorted_input_is_sorted_with_xy(self):
        result = align_devices(
            [record("A", [3, 1, 2], [30, 10, 20], [300, 100, 200])]
        )

        np.testing.assert_array_equal(result.bunch_ids, [1, 2, 3])
        np.testing.assert_array_equal(result.x, [[10, 20, 30]])
        quality = result.quality_report[0]
        self.assertFalse(quality.was_strictly_increasing)


class InputQualityTests(unittest.TestCase):
    def test_empty_device_id_is_rejected(self):
        result = align_devices([record("  ", [1, 2])])

        self.assertEqual(result.device_ids, ())
        self.assertEqual(result.skipped_devices[0].reason, "empty_device_id")

    def test_duplicate_bunch_id_rejects_device_with_reason(self):
        result = align_devices([record("A", [1, 1, 2])])

        self.assertEqual(result.device_ids, ())
        self.assertEqual(result.skipped_devices[0].reason, "duplicate_bunch_id")
        self.assertEqual(result.quality_report[0].duplicate_count, 1)

    def test_duplicate_first_policy_is_deterministic(self):
        params = AlignmentParameters(duplicate_policy="first")
        result = align_devices(
            [record("A", [2, 1, 1], [20, 10, 11], [200, 100, 101])],
            params,
        )

        np.testing.assert_array_equal(result.bunch_ids, [1, 2])
        np.testing.assert_array_equal(result.x, [[10, 20]])

    def test_length_mismatch_skips_only_bad_device(self):
        bad = record("bad", [1, 2], x=[1], y=[2, 3])
        good = record("good", [1, 2])

        result = align_devices([bad, good])

        self.assertEqual(result.device_ids, ("good",))
        self.assertEqual(result.skipped_devices[0].reason, "length_mismatch")

    def test_zero_drop_policy_removes_only_zero_id_samples(self):
        params = AlignmentParameters(zero_policy="drop")
        result = align_devices(
            [record("A", [0, 2, 3], [10, 20, 30], [11, 21, 31])],
            params,
        )

        np.testing.assert_array_equal(result.bunch_ids, [2, 3])
        np.testing.assert_array_equal(result.x, [[20, 30]])
        self.assertEqual(result.quality_report[0].zero_count, 1)


class CoverageAlignmentTests(unittest.TestCase):
    def test_coverage_handles_200_devices_without_order_dependent_selection(self):
        base = 38_212_018_001_007
        main_ids = np.arange(1024, dtype=np.uint64) * 500_000 + base
        shifted_ids = main_ids + 2_000_000_000
        records = [
            *(record(f"main-{index:03d}", main_ids) for index in range(190)),
            *(record(f"shifted-{index:03d}", shifted_ids) for index in range(10)),
        ]
        params = AlignmentParameters(
            mode="coverage",
            min_id_device_coverage=0.90,
            min_device_id_coverage=0.95,
        )

        result = align_devices(list(reversed(records)), params)

        self.assertEqual(len(result.device_ids), 190)
        self.assertEqual(result.bunch_ids.size, 1024)
        self.assertEqual(result.x.shape, (190, 1024))
        self.assertTrue(result.valid_mask.all())
        self.assertEqual(len(result.skipped_devices), 10)
        self.assertTrue(
            all(item.reason == "low_consensus_coverage" for item in result.skipped_devices)
        )

    def test_dominant_consensus_is_order_independent(self):
        base = 38_212_018_001_007
        main_ids = np.arange(8, dtype=np.uint64) * 500_000 + base
        shifted_ids = main_ids + 20_000_000
        dense_ids = np.arange(8, dtype=np.uint64) * 75 + base + 17
        records = [
            *(record(f"main-{index}", main_ids) for index in range(6)),
            record("shifted", shifted_ids),
            record("dense", dense_ids),
        ]
        params = AlignmentParameters(
            mode="coverage",
            min_id_device_coverage=0.70,
            min_device_id_coverage=0.90,
        )

        forward = align_devices(records, params)
        reverse = align_devices(list(reversed(records)), params)

        self.assertEqual(forward.device_ids, tuple(f"main-{index}" for index in range(6)))
        self.assertEqual(forward.device_ids, reverse.device_ids)
        np.testing.assert_array_equal(forward.bunch_ids, main_ids)
        np.testing.assert_array_equal(forward.bunch_ids, reverse.bunch_ids)
        self.assertEqual(
            {item.device_id for item in forward.skipped_devices},
            {"shifted", "dense"},
        )

    def test_coverage_output_uses_mask_for_missing_values(self):
        params = AlignmentParameters(
            mode="coverage",
            min_id_device_coverage=0.5,
            min_device_id_coverage=0.5,
        )
        result = align_devices(
            [
                record("A", [1, 2, 3], [10, 20, 30], [11, 21, 31]),
                record("B", [2, 3, 4], [200, 300, 400], [201, 301, 401]),
            ],
            params,
        )

        np.testing.assert_array_equal(result.bunch_ids, [1, 2, 3, 4])
        np.testing.assert_array_equal(
            result.valid_mask,
            [[True, True, True, False], [False, True, True, True]],
        )
        self.assertEqual(result.x[0, 3], 0)
        self.assertEqual(result.x[1, 0], 0)

    def test_equal_disjoint_groups_do_not_choose_by_input_order(self):
        params = AlignmentParameters(
            mode="coverage",
            min_id_device_coverage=0.75,
            min_device_id_coverage=1.0,
        )
        records = [
            record("A1", [1, 2]),
            record("A2", [1, 2]),
            record("B1", [10, 11]),
            record("B2", [10, 11]),
        ]

        result = align_devices(records, params)

        self.assertEqual(result.device_ids, ())
        self.assertEqual(result.bunch_ids.size, 0)
        self.assertEqual(
            {item.reason for item in result.skipped_devices},
            {"no_consensus_bunch_ids"},
        )


if __name__ == "__main__":
    unittest.main()
