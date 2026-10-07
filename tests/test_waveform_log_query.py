"""Read-only dashboard task detail extraction tests."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "query_waveform_mongodb", ROOT / "tools" / "query_waveform_mongodb.py"
)
QUERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUERY)
DASHBOARD_SPEC = importlib.util.spec_from_file_location(
    "waveform_log_dashboard", ROOT / "tools" / "waveform_log_dashboard.py"
)
DASHBOARD = importlib.util.module_from_spec(DASHBOARD_SPEC)
DASHBOARD_SPEC.loader.exec_module(DASHBOARD)


class DashboardTaskDetailsTests(unittest.TestCase):
    def test_server_mode_queries_local_mongodb_without_ssh(self):
        with patch.object(DASHBOARD.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"tasks": []}'
            self.assertEqual(DASHBOARD.capture_tasks("2026-10-06", "local"), {"tasks": []})
        command = run.call_args.args[0]
        self.assertEqual(command[:2], [DASHBOARD.sys.executable, str(DASHBOARD.QUERY)])
        self.assertEqual(command[2:], ["tasks-date", "2026-10-06"])
        self.assertIsNone(run.call_args.kwargs["input"])

    def test_desktop_mode_reconnects_then_retries_failed_query(self):
        failed = SimpleNamespace(returncode=255, stdout="")
        succeeded = SimpleNamespace(returncode=0, stdout='{"tasks": []}')
        with patch.object(DASHBOARD, "CONTROL_SOCKET", "/tmp/known-waveform-socket"), \
             patch.object(DASHBOARD.Path, "exists", return_value=True), \
             patch.object(DASHBOARD, "reconnect_candidate_ssh") as reconnect, \
             patch.object(DASHBOARD.subprocess, "run", side_effect=[failed, succeeded]) as run:
            self.assertEqual(DASHBOARD.capture_tasks("2026-10-06"), {"tasks": []})
        self.assertEqual(run.call_count, 2)
        reconnect.assert_called_once_with()

    def test_device_stage_provenance_and_sensitive_error_redaction(self):
        request_id = "a54889ef-014c-4bab-8ac7-07d0c6d1fca4"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / f"{request_id}.json").write_text(json.dumps({
                "request_id": request_id,
                "device_results": [
                    {"device_id": "BPM01", "success": False,
                     "attempted_cycles": 2, "successful_cycles": 0,
                     "error": "no completed matching file within 32s"},
                    {"device_id": "BPM02", "success": False,
                     "attempted_cycles": 1, "successful_cycles": 0,
                     "failure_stage": "TXT 解析",
                     "error": "bad column password=hunter2"},
                ],
            }), encoding="utf-8")
            result = {"tasks": [{"_id": request_id,
                                 "error": {"message": "bad password=hunter2"}}]}
            QUERY._attach_device_results(result, state)
        devices = result["tasks"][0]["device_results"]
        self.assertNotIn("hunter2", result["tasks"][0]["error"]["message"])
        self.assertEqual((devices[0]["failure_stage"], devices[0]["stage_source"]),
                         ("设备文件等待/下载", "inferred"))
        self.assertEqual((devices[1]["failure_stage"], devices[1]["stage_source"]),
                         ("TXT 解析", "recorded"))
        self.assertNotIn("hunter2", devices[1]["error"])

    def test_missing_task_state_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            result = {"tasks": [{"_id": "a54889ef-014c-4bab-8ac7-07d0c6d1fca4"}]}
            QUERY._attach_device_results(result, Path(directory))
        self.assertEqual(result["tasks"][0]["device_detail_state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
