from __future__ import annotations

import os
import sys
import tempfile
import unittest
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

from candidate_backend import CandidateOperatorBackend
from waveform_storage.acquisition.protocol import CaptureResponse, PROTOCOL_VERSION
from waveform_storage.config.settings import AppSettings
from waveform_storage.transport.zmq_transport import ZmqTimeout


class FormalCandidateBackendTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"WAVEFORM_CANDIDATE_ZMQ_ENDPOINT": "tcp://127.0.0.1:5001"},
        )
        self.environment.start()
        self.backend = CandidateOperatorBackend(
            AppSettings.from_env(project_root=PROJECT_ROOT)
        )

    def tearDown(self):
        self.environment.stop()

    def test_candidate_history_contract_is_formal_and_legacy_is_separate(self):
        from waveform_storage.gui.operator_contract import CandidateHistoryBackend, OperatorBackend
        self.assertIsInstance(self.backend, CandidateHistoryBackend)
        for name in ("retrieve_runs", "retrieve_segments", "align_segment",
                     "export_segment_alignment", "download_segment_raw"):
            self.assertIn(name, CandidateHistoryBackend.__dict__)
            self.assertNotIn(name, OperatorBackend.__dict__)

    @patch("candidate_backend.ZmqTransport")
    def test_malformed_metadata_responses_have_stable_runtime_errors(self, transport):
        from datetime import datetime, timezone
        from uuid import uuid4
        from waveform_storage.metadata.history import CaptureRunSummary, CaptureSegment
        capture_id = str(uuid4())
        now = datetime.now(timezone.utc)
        run = CaptureRunSummary(capture_id, now, now, now, 1000, 600, "failed", 1, 2, 1, True).to_payload()
        segment = CaptureSegment(capture_id, 3, "000001", now, ("BPM_A",), "2026-10-08").to_payload()
        def reply(payload):
            return SimpleNamespace(accepted=True, payload=payload, error_code=None, message="ok")
        for payload in (None, [], {}, {"runs": {}}, {"runs": [None]}, {"runs": [{}]},
                        {"runs": [dict(run, segment_count=True)]},
                        {"runs": [dict(run, frequency_hz="1000")]},
                        {"runs": [dict(run, frequency_hz=10**1000)]},
                        {"runs": [dict(run, first_capture_at="broken")]}, {"runs": [run, run]}):
            with self.subTest(payload=payload):
                transport.return_value.list_capture_runs.return_value = reply(payload)
                with self.assertRaisesRegex(RuntimeError, "invalid capture runs"):
                    self.backend.retrieve_runs(date(2026, 10, 8))
        for payload in (None, {}, {"segments": "bad"}, {"segments": [None]}, {"segments": [{}]},
                        {"segments": [dict(segment, request_id=str(uuid4()))]},
                        {"segments": [segment, segment]},
                        {"segments": [dict(segment, capture_sequence=True)]},
                        {"segments": [dict(segment, capture_sequence=-1)]},
                        {"segments": [dict(segment, device_ids=["BPM_A", "BPM_A"])]},
                        {"segments": [dict(segment, device_ids="BPM_A")]},
                        {"segments": [dict(segment, device_ids=[])]}):
            transport.return_value.list_capture_segments.return_value = reply(payload)
            with self.assertRaisesRegex(RuntimeError, "invalid capture segments"):
                self.backend.retrieve_segments(capture_id)
        file = {"request_id": capture_id, "capture_sequence": 3, "device_id": "BPM_A", "path": "/data.h5"}
        for payload in (None, {}, {"files": {}}, {"files": [None]},
                        {"files": [dict(file, request_id=str(uuid4()))]},
                        {"files": [dict(file, capture_sequence=4)]},
                        {"files": [dict(file, capture_sequence=3.0)]},
                        {"files": [dict(file, path="")]}, {"files": [file, file]}):
            transport.return_value.find_waveform_files_by_segment.return_value = reply(payload)
            with self.assertRaises(RuntimeError):
                self.backend._candidate_segment_file_pairs(capture_id, 3, ("BPM_A",))
        transport.return_value.list_capture_runs.return_value = reply({"runs": []})
        self.assertEqual(self.backend.retrieve_runs(date(2026, 10, 8)), ())

    @patch("candidate_backend.ZmqTransport")
    def test_precise_segment_queries_cache_download_and_export(self, transport):
        import json
        from dataclasses import replace
        from datetime import datetime, timezone
        from uuid import uuid4
        from waveform_storage.metadata.history import CaptureRunSummary, CaptureSegment
        from waveform_storage.processing.alignment import DeviceSeries, align_devices
        now = datetime(2026, 10, 8, 16, 0, 1, tzinfo=timezone.utc)
        first_id, second_id = str(uuid4()), str(uuid4())
        segment = CaptureSegment(first_id, 3, "000001", now, ("BPM_A",), "2026-10-09")
        run = CaptureRunSummary(first_id, now, now, now, 1000, 600, "failed", 1, 2, 1, True)
        def reply(payload):
            return SimpleNamespace(accepted=True, payload=payload, error_code=None, message="ok")
        transport.return_value.list_capture_runs.return_value = reply({"runs": [run.to_payload()]})
        transport.return_value.list_capture_segments.return_value = reply({"segments": [segment.to_payload()]})
        self.assertEqual(self.backend.retrieve_runs(date(2026, 10, 9)), (run,))
        self.assertEqual(self.backend.retrieve_segments(first_id), (segment,))
        with tempfile.TemporaryDirectory() as temporary:
            sources = {}
            for request_id in (first_id, second_id):
                path = Path(temporary) / request_id / "RAW.h5"
                path.parent.mkdir()
                path.write_bytes(b"raw")
                sources[request_id] = path
            def lookup(correlation, capture_id, sequence, devices):
                return reply({"files": [{"request_id": capture_id, "capture_sequence": sequence,
                    "device_id": "BPM_A", "path": str(sources[capture_id]), "storage_id": "primary"}]})
            transport.return_value.find_waveform_files_by_segment.side_effect = lookup
            result = align_devices([DeviceSeries("BPM_A", [1, 2], [10, 11], [20, 21])])
            with patch.object(self.backend._retrieval._storage, "read_series", return_value=(
                (DeviceSeries("BPM_A", [1, 2], [10, 11], [20, 21]),), ())):
                for selected in (segment, replace(segment, request_id=second_id),
                                 replace(segment, request_id=second_id, capture_sequence=9)):
                    self.backend.align_segment(selected, ("BPM_A",))
                    self.backend.align_segment(selected, ("BPM_A",))
            self.assertEqual(transport.return_value.find_waveform_files_by_segment.call_count, 3)
            self.backend.candidate_local_files = True
            paths = self.backend.download_segment_raw(segment, ("BPM_A",), Path(temporary) / "out")
            self.assertEqual(paths[0].parent, Path(temporary) / "out" / "20261009" / first_id / "S00003")
            self.assertEqual(paths[0].read_bytes(), b"raw")
            exported = self.backend.export_segment_alignment(result, segment, temporary)
            self.assertIn(f"20261009_000001_S00003_{first_id}", exported.name)
            with h5py.File(exported) as h5:
                metadata = json.loads(h5.attrs["metadata_json"])
                self.assertEqual(metadata["request_id"], first_id)
                self.assertEqual(metadata["capture_sequence"], 3)
                self.assertEqual(metadata["captured_at"], now.isoformat())
            transport.return_value.find_waveform_files.assert_not_called()
            # A server returning another capture must fail rather than silently read it.
            transport.return_value.find_waveform_files_by_segment.side_effect = None
            transport.return_value.find_waveform_files_by_segment.return_value = reply({"files": [{
                "request_id": second_id, "capture_sequence": 3, "device_id": "BPM_A", "path": "/wrong"}]})
            with self.assertRaises(RuntimeError):
                self.backend._candidate_segment_file_pairs(first_id, 3, ("BPM_A",))

    @patch("candidate_backend.ZmqTransport")
    def test_capture_uses_frequency_read_by_waveform_server(self, transport):
        transport.return_value.get_timing_frequency.return_value = SimpleNamespace(
            accepted=True, payload={"frequency_hz": 100.0}, error_code=None,
            message="ok",
        )
        def respond(request):
            self.assertEqual(request.frequency_hz, 100.0)
            return CaptureResponse(
                PROTOCOL_VERSION, request.request_id, True, "accepted", "ok"
            )

        transport.return_value.request.side_effect = respond
        result = self.backend.start_capture(
            ("INJ1",), ("AC_LA_INJ1_BI_SBPM08",), 600
        )
        self.assertTrue(result.accepted)
        transport.return_value.get_timing_frequency.assert_called_once()

    @patch("candidate_backend.ZmqTransport")
    def test_pv_failure_does_not_submit_capture(self, transport):
        transport.return_value.get_timing_frequency.return_value = SimpleNamespace(
            accepted=False, payload={}, error_code="TIMING_FREQUENCY_FAILED",
            message="PV timeout",
        )
        with self.assertRaisesRegex(RuntimeError, "PV timeout"):
            self.backend.start_capture(
                ("INJ1",), ("AC_LA_INJ1_BI_SBPM08",), 600
            )
        transport.return_value.request.assert_not_called()

    @patch("candidate_backend.ZmqTransport")
    def test_selected_frequency_must_match_live_pv(self, transport):
        transport.return_value.get_timing_frequency.return_value = SimpleNamespace(
            accepted=True, payload={"frequency_hz": 1000.0}, error_code=None,
            message="ok",
        )
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            self.backend.start_capture(
                ("INJ1",), ("AC_LA_INJ1_BI_SBPM08",), 60, 500.0
            )
        transport.return_value.request.assert_not_called()

    @patch("candidate_backend.ZmqTransport")
    def test_capture_timeout_recovers_persisted_running_task_without_resending(self, transport):
        transport.return_value.get_timing_frequency.return_value = SimpleNamespace(
            accepted=True, payload={"frequency_hz": 5.0}, error_code=None,
            message="ok",
        )
        transport.return_value.request.side_effect = ZmqTimeout("reply timed out")

        def status(request_id):
            return CaptureResponse(
                PROTOCOL_VERSION, request_id, True, "running", "capture is running"
            )

        transport.return_value.get_status.side_effect = status
        result = self.backend.start_capture(
            ("INJ1",), ("AC_LA_INJ1_BI_SBPM01",), 4200
        )
        self.assertEqual(result.status, "running")
        self.assertEqual(transport.return_value.request.call_count, 1)
        self.assertEqual(
            transport.return_value.get_status.call_args.args,
            (result.request_id,),
        )
        self.assertEqual(
            [call.kwargs["timeout_ms"] for call in transport.call_args_list],
            [25000, 10000, 10000],
        )

    @patch("candidate_backend.ZmqTransport")
    def test_capture_double_timeout_preserves_request_id(self, transport):
        transport.return_value.get_timing_frequency.return_value = SimpleNamespace(
            accepted=True, payload={"frequency_hz": 5.0}, error_code=None,
            message="ok",
        )
        transport.return_value.request.side_effect = ZmqTimeout("reply timed out")
        transport.return_value.get_status.side_effect = ZmqTimeout("status timed out")
        with self.assertRaisesRegex(RuntimeError, r"请求ID：[0-9a-f-]{36}"):
            self.backend.start_capture(
                ("INJ1",), ("AC_LA_INJ1_BI_SBPM01",), 4200
            )
        self.assertEqual(transport.return_value.request.call_count, 1)

    @patch("candidate_backend.ZmqTransport")
    def test_time_query_uses_candidate_metadata(self, transport):
        transport.return_value.list_capture_times.return_value = SimpleNamespace(
            accepted=True,
            error_code=None,
            message="ok",
            payload={"times": ["083000", "120721"]},
        )
        self.assertEqual(
            self.backend.retrieve_times(date(2026, 8, 25)),
            ("083000", "120721"),
        )
        self.assertEqual(
            transport.return_value.list_capture_times.call_args.args[1],
            "2026-08-25",
        )

    @patch("candidate_backend.ZmqTransport")
    def test_available_devices_excludes_missing_waveform_files(self, transport):
        transport.return_value.find_waveform_files.return_value = SimpleNamespace(
            accepted=True,
            error_code=None,
            message="ok",
            payload={
                "files": [
                    {"device_id": "BPM_A", "path": "/data/a.h5"},
                    {"device_id": "BPM_C", "path": "/data/c.h5"},
                ]
            },
        )
        selected = self.backend.available_devices(
            date(2026, 10, 2), "230308", ("BPM_A", "BPM_B", "BPM_C")
        )
        self.assertEqual(selected, ("BPM_A", "BPM_C"))

    @patch("candidate_backend.ZmqTransport")
    def test_local_catalog_alignment_and_export(self, transport):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "RAW-110023-BPM_A-S00000.h5"
            with h5py.File(path, "w") as h5:
                h5.attrs["Device"] = "BPM_A"
                h5.create_dataset("BunchIDData", data=np.asarray([[1, 2]], dtype=np.uint64))
                h5.create_dataset("Data", data=np.asarray([[3, 4], [5, 6]], dtype=np.int64))
            transport.return_value.find_waveform_files.return_value = SimpleNamespace(
                accepted=True,
                error_code=None,
                message="ok",
                payload={"files": [{"device_id": "BPM_A", "path": str(path)}]},
            )
            with patch.dict(os.environ, {"WAVEFORM_CANDIDATE_LOCAL_FILES": "true"}):
                backend = CandidateOperatorBackend(
                    AppSettings.from_env(project_root=PROJECT_ROOT)
                )
                result = backend.align(date(2025, 11, 12), "110023", ("BPM_A",))
                output = backend.export_alignment(
                    result, date(2025, 11, 12), "110023", directory
                )
            self.assertEqual(result.device_ids, ("BPM_A",))
            self.assertEqual(result.bunch_ids.tolist(), [1, 2])
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
