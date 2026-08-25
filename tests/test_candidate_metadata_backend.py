from __future__ import annotations

import os
import sys
import unittest
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import h5py
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPERATOR_DIR = PROJECT_ROOT / "apps" / "operator_gui"
if str(OPERATOR_DIR) not in sys.path:
    sys.path.insert(0, str(OPERATOR_DIR))

from experimental_backend import ConnectedOperatorBackend
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.operator_backend import OperatorMode


class CandidateMetadataBackendTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"WAVEFORM_CANDIDATE_ZMQ_ENDPOINT": "tcp://127.0.0.1:5001"},
        )
        self.environment.start()
        self.backend = ConnectedOperatorBackend(
            AppSettings.from_env(project_root=PROJECT_ROOT),
            mode=OperatorMode.CANDIDATE_V2,
        )

    def tearDown(self):
        self.environment.stop()

    @patch("experimental_backend.ZmqTransport")
    def test_gui_time_query_uses_candidate_service_metadata(self, transport_type):
        transport_type.return_value.list_capture_times.return_value = SimpleNamespace(
            accepted=True,
            error_code=None,
            message="ok",
            payload={"times": ["083000", "120721"]},
        )
        result = self.backend.retrieve_times(date(2026, 8, 25))
        self.assertEqual(result, ("083000", "120721"))
        call = transport_type.return_value.list_capture_times.call_args
        self.assertEqual(call.args[1], "2026-08-25")

    @patch("experimental_backend.ZmqTransport")
    def test_gui_file_query_uses_catalog_paths(self, transport_type):
        transport_type.return_value.find_waveform_files.return_value = SimpleNamespace(
            accepted=True,
            error_code=None,
            message="ok",
            payload={
                "files": [
                    {
                        "device_id": "BPM_A",
                        "path": "/SHINE_Data/raw/RAW-120721-BPM_A-S00000.h5",
                    }
                ]
            },
        )
        pairs = self.backend._candidate_file_pairs(
            date(2026, 8, 25),
            "120721",
            ("BPM_A",),
        )
        self.assertEqual(
            pairs,
            (("/SHINE_Data/raw/RAW-120721-BPM_A-S00000.h5", "BPM_A"),),
        )

    @patch("experimental_backend.ZmqTransport")
    def test_gui_surfaces_metadata_query_failure(self, transport_type):
        transport_type.return_value.list_capture_times.return_value = SimpleNamespace(
            accepted=False,
            error_code="METADATA_UNAVAILABLE",
            message="catalog unavailable",
            payload={},
        )
        with self.assertRaisesRegex(RuntimeError, "METADATA_UNAVAILABLE"):
            self.backend.retrieve_times(date(2026, 8, 25))

    @patch("experimental_backend.ZmqTransport")
    def test_explicit_local_mode_reads_catalog_hdf5_without_ssh(
        self, transport_type
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "RAW-110023-BPM_A-S00000.h5"
            with h5py.File(path, "w") as h5:
                h5.attrs["Device"] = "BPM_A"
                h5.create_dataset(
                    "BunchIDData", data=np.asarray([[1, 2]], dtype=np.uint64)
                )
                h5.create_dataset(
                    "Data", data=np.asarray([[3, 4], [5, 6]], dtype=np.int64)
                )
            transport_type.return_value.find_waveform_files.return_value = (
                SimpleNamespace(
                    accepted=True,
                    error_code=None,
                    message="ok",
                    payload={
                        "files": [{"device_id": "BPM_A", "path": str(path)}]
                    },
                )
            )
            with patch.dict(
                os.environ,
                {"WAVEFORM_CANDIDATE_LOCAL_FILES": "true"},
            ):
                backend = ConnectedOperatorBackend(
                    AppSettings.from_env(project_root=PROJECT_ROOT),
                    mode=OperatorMode.CANDIDATE_V2,
                )
                result = backend.align(
                    date(2025, 11, 12), "110023", ("BPM_A",)
                )

        self.assertEqual(result.device_ids, ("BPM_A",))
        self.assertEqual(result.bunch_ids.tolist(), [1, 2])


if __name__ == "__main__":
    unittest.main()
