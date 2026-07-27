from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from waveform_storage.io.hdf5_io import (
    Hdf5SchemaError,
    read_alignment_v2,
    read_legacy_merged_hdf5,
    read_single_device_hdf5,
    write_single_device_hdf5,
    write_alignment_v2,
    write_legacy_merged_hdf5,
)
from waveform_storage.io.txt_parser import ParsedWaveform
from waveform_storage.processing.alignment import (
    AlignmentParameters,
    DeviceSeries,
    align_devices,
)


BASE = 38_212_018_001_007


class SingleDeviceHdf5Tests(unittest.TestCase):
    def test_atomic_writer_preserves_verified_raw_schema(self):
        waveform = ParsedWaveform(
            x=np.asarray([10, -20], dtype=np.int64),
            y=np.asarray([30, 0], dtype=np.int64),
            bunch_ids=np.asarray([2**40, 2**40 + 1], dtype=np.uint64),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "raw.h5"
            written = write_single_device_hdf5(
                path,
                "BPM_A",
                waveform,
                metadata={"request_id": "request"},
            )
            loaded = read_single_device_hdf5(written)
            with h5py.File(path, "r") as h5:
                self.assertEqual(h5["BunchIDData"].dtype, np.uint64)
                self.assertEqual(h5["Data"].dtype, np.int64)
                self.assertEqual(h5.attrs["Data_0"], "bpm_x")
            leftovers = tuple(path.parent.glob("*.part"))

        self.assertEqual(loaded.device_id, "BPM_A")
        self.assertEqual(loaded.bunch_ids.tolist(), [2**40, 2**40 + 1])
        self.assertEqual(leftovers, ())

    def test_reads_verified_production_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.h5"
            bunch_ids = np.arange(1024, dtype=np.uint64) * 500_000 + BASE
            data = np.vstack(
                (
                    np.arange(1024, dtype=np.int64),
                    -np.arange(1024, dtype=np.int64),
                )
            )
            with h5py.File(path, "w") as h5:
                h5.attrs["Device"] = "AC_LA_INJ1_BI_SBPM08"
                h5.attrs["Data_0"] = "bpm_x"
                h5.attrs["Data_1"] = "bpm_y"
                h5.create_dataset(
                    "BunchIDData",
                    data=bunch_ids.reshape(1, -1),
                )
                h5.create_dataset("Data", data=data)

            result = read_single_device_hdf5(path)

        self.assertEqual(result.device_id, "AC_LA_INJ1_BI_SBPM08")
        self.assertEqual(np.asarray(result.bunch_ids).dtype, np.dtype(np.uint64))
        np.testing.assert_array_equal(result.bunch_ids, bunch_ids)
        np.testing.assert_array_equal(result.x, data[0])

    def test_rejects_length_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.h5"
            with h5py.File(path, "w") as h5:
                h5.attrs["Device"] = "A"
                h5.create_dataset(
                    "BunchIDData",
                    data=np.array([[1, 2]], dtype=np.uint64),
                )
                h5.create_dataset(
                    "Data",
                    data=np.zeros((2, 3), dtype=np.int64),
                )

            with self.assertRaises(Hdf5SchemaError):
                read_single_device_hdf5(path)


class AlignmentV2RoundTripTests(unittest.TestCase):
    def _coverage_result(self):
        parameters = AlignmentParameters(
            mode="coverage",
            min_id_device_coverage=0.5,
            min_device_id_coverage=0.5,
        )
        return align_devices(
            [
                DeviceSeries(
                    "A",
                    [BASE, BASE + 500_000],
                    [0, 20],
                    [11, 21],
                ),
                DeviceSeries(
                    "B",
                    [BASE + 500_000, BASE + 1_000_000],
                    [200, 300],
                    [201, 301],
                ),
            ],
            parameters,
        )

    def test_v2_round_trip_preserves_mask_parameters_and_uint64(self):
        result = self._coverage_result()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aligned-v2.h5"
            write_alignment_v2(
                path,
                result,
                metadata={"run_id": "20251112", "time_sec": "120721"},
            )
            loaded = read_alignment_v2(path)
            leftovers = list(Path(directory).glob("*.part"))

        self.assertEqual(loaded.metadata["run_id"], "20251112")
        self.assertEqual(loaded.result.parameters.mode, "coverage")
        self.assertEqual(loaded.result.bunch_ids.dtype, np.dtype(np.uint64))
        np.testing.assert_array_equal(
            loaded.result.bunch_ids,
            result.bunch_ids,
        )
        np.testing.assert_array_equal(
            loaded.result.valid_mask,
            result.valid_mask,
        )
        np.testing.assert_array_equal(loaded.result.x, result.x)
        self.assertEqual(leftovers, [])

    def test_rejects_wrong_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.h5"
            with h5py.File(path, "w") as h5:
                h5.attrs["schema_name"] = "wrong"
                h5.attrs["schema_version"] = 99
            with self.assertRaises(Hdf5SchemaError):
                read_alignment_v2(path)


class LegacyMergedRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_large_ids_and_filtered_device_metadata(self):
        result = align_devices(
            [
                DeviceSeries(
                    "AC_LA_INJ1_BI_SBPM08",
                    [BASE, BASE + 500_000],
                    [1, 2],
                    [3, 4],
                ),
                DeviceSeries(
                    "AC_LA_INJ1_BI_SBPM10",
                    [BASE, BASE + 500_000],
                    [10, 20],
                    [30, 40],
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.h5"
            write_legacy_merged_hdf5(path, result)
            loaded = read_legacy_merged_hdf5(path)
            with h5py.File(path, "r") as h5:
                stored_dtype = h5["BunchIDData"].dtype
                first_id = int(h5["BunchIDData"][0, 0])
                device_count = len(h5.attrs["Device"])

        self.assertEqual(stored_dtype, np.dtype(np.uint64))
        self.assertEqual(first_id, BASE)
        self.assertEqual(device_count, 2)
        self.assertEqual(loaded.result.device_ids, result.device_ids)
        np.testing.assert_array_equal(loaded.result.x, result.x)

    def test_legacy_writer_refuses_missing_values(self):
        parameters = AlignmentParameters(
            mode="coverage",
            min_id_device_coverage=0.5,
            min_device_id_coverage=0.5,
        )
        result = align_devices(
            [
                DeviceSeries("A", [1, 2], [1, 2], [3, 4]),
                DeviceSeries("B", [2, 3], [5, 6], [7, 8]),
            ],
            parameters,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.h5"
            with self.assertRaises(Hdf5SchemaError):
                write_legacy_merged_hdf5(path, result)
            self.assertFalse(path.exists())

    def test_reader_detects_metadata_row_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-legacy.h5"
            with h5py.File(path, "w") as h5:
                h5.attrs["Device"] = ["A"]
                h5.attrs["Data[0:]_0"] = "WRONG_X"
                h5.attrs["Data[1:]_0"] = "A_Y"
                h5.create_dataset(
                    "BunchIDData",
                    data=np.array([[1, 2]], dtype=np.uint64),
                )
                h5.create_dataset(
                    "Data",
                    data=np.zeros((2, 2), dtype=np.int64),
                )
            with self.assertRaises(Hdf5SchemaError):
                read_legacy_merged_hdf5(path)


if __name__ == "__main__":
    unittest.main()
