from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from waveform_storage.application.retrieval_service import (
    RetrievalService,
    StorageQuery,
)
from waveform_storage.processing.alignment import DeviceSeries, SkippedDevice


class FakeStorage:
    def __init__(self):
        self.find_calls = 0
        self.read_calls = []

    def find_files(self, query):
        self.find_calls += 1
        return [
            ("/remote/b.h5", "BPM_B"),
            ("/remote/unknown.h5", "UNKNOWN"),
            ("/remote/a.h5", "BPM_A"),
        ]

    def read_series(self, pairs, reader):
        self.read_calls.append((list(pairs), reader))
        return (
            [
                DeviceSeries("BPM_A", [1, 2], [10, 11], [20, 21]),
                DeviceSeries("BPM_B", [1, 2], [30, 31], [40, 41]),
            ],
            [SkippedDevice("BPM_C", "read_failed", "synthetic")],
        )


class RetrievalServiceTests(unittest.TestCase):
    def setUp(self):
        self.storage = FakeStorage()
        self.reader = object()
        self.service = RetrievalService(self.storage, reader=self.reader)
        self.query = StorageQuery(
            "/remote/root",
            "20260725",
            "120000",
            ("BPM_A", "BPM_B", "BPM_C"),
        )

    def test_fake_storage_end_to_end_orders_filters_aligns_and_reports(self):
        result = self.service.retrieve(self.query)
        self.assertEqual(
            self.storage.read_calls[0][0],
            [("/remote/a.h5", "BPM_A"), ("/remote/b.h5", "BPM_B")],
        )
        self.assertIs(self.storage.read_calls[0][1], self.reader)
        self.assertEqual(result.device_ids, ("BPM_A", "BPM_B"))
        self.assertEqual(
            [(item.device_id, item.reason) for item in result.skipped_devices],
            [("UNKNOWN", "unknown_device"), ("BPM_C", "read_failed")],
        )
        np.testing.assert_array_equal(result.bunch_ids, [1, 2])

    def test_cache_uses_all_four_query_dimensions_and_can_be_cleared(self):
        first = self.service.retrieve(self.query)
        second = self.service.retrieve(self.query)
        self.assertIs(first, second)
        self.assertEqual(self.storage.find_calls, 1)
        changed = StorageQuery(
            self.query.remote_path,
            self.query.run_id,
            "120001",
            self.query.device_ids,
        )
        self.service.retrieve(changed)
        self.assertEqual(self.storage.find_calls, 2)
        self.service.clear_cache()
        self.service.retrieve(changed)
        self.assertEqual(self.storage.find_calls, 3)

    def test_export_injects_writer_and_preserves_filename_contract(self):
        calls = []

        def writer(path, result):
            calls.append((Path(path), result))
            return path

        service = RetrievalService(
            self.storage,
            reader=self.reader,
            legacy_writer=writer,
        )
        result = service.retrieve(self.query)
        output = service.export(result, self.query, "D:/output")
        self.assertEqual(
            output.name,
            "20260725_120000_BPMdata.h5",
        )
        self.assertIs(calls[0][1], result)

    def test_query_cache_key_contains_complete_device_list(self):
        self.assertEqual(
            self.query.cache_key,
            (
                "/remote/root",
                "20260725",
                "120000",
                ("BPM_A", "BPM_B", "BPM_C"),
                None, None,
            ),
        )

    def test_segment_identity_separates_cache_even_at_same_time(self):
        from dataclasses import replace
        queries = [replace(self.query, request_id="run-a", capture_sequence=0),
                   replace(self.query, request_id="run-b", capture_sequence=0),
                   replace(self.query, request_id="run-b", capture_sequence=3)]
        for query in queries:
            self.service.retrieve(query)
            self.service.retrieve(query)
        self.assertEqual(self.storage.find_calls, 3)
        for fields in ({"request_id": "run-a"}, {"capture_sequence": 0},
                       {"request_id": "run-a", "capture_sequence": True},
                       {"request_id": "run-a", "capture_sequence": -1}):
            with self.assertRaises(ValueError):
                replace(self.query, **fields)

    def test_no_intersection_remains_a_structured_empty_result(self):
        class DisjointStorage(FakeStorage):
            def find_files(self, query):
                return [("/a", "BPM_A"), ("/b", "BPM_B")]

            def read_series(self, pairs, reader):
                return (
                    [
                        DeviceSeries("BPM_A", [1], [10], [20]),
                        DeviceSeries("BPM_B", [2], [30], [40]),
                    ],
                    [],
                )

        result = RetrievalService(DisjointStorage()).retrieve(
            StorageQuery("/remote", "20260725", "120000", ("BPM_A", "BPM_B"))
        )
        self.assertEqual(result.bunch_ids.size, 0)
        self.assertTrue(result.skipped_devices)


if __name__ == "__main__":
    unittest.main()
