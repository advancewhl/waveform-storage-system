from __future__ import annotations

import unittest
from pathlib import Path

from waveform_storage.gui.controllers import (
    GuiController,
    QueryParameters,
    TaskState,
)
from waveform_storage.gui.workers import BackgroundWorker


class GuiControllerTests(unittest.TestCase):
    def test_success_failure_and_cancel_transitions(self):
        controller = GuiController()
        query = QueryParameters("/r", "20260725", "120000", ("BPM_A",))
        controller.begin(TaskState.ALIGNING, query)
        result = object()
        controller.complete(result, ready=True)
        self.assertEqual(controller.state, TaskState.READY)
        self.assertIs(controller.current_result, result)

        controller.begin(TaskState.EXPORTING, query)
        controller.fail(ValueError("synthetic"))
        self.assertEqual(controller.state, TaskState.FAILED)
        controller.begin(TaskState.RETRIEVING, query)
        controller.cancel()
        self.assertEqual(controller.state, TaskState.CANCELLED)

    def test_second_running_task_is_rejected(self):
        controller = GuiController()
        controller.begin(TaskState.DOWNLOADING)
        with self.assertRaises(RuntimeError):
            controller.begin(TaskState.ALIGNING)

    def test_actions_are_injected_and_do_not_depend_on_widgets(self):
        calls = []
        controller = GuiController(
            retrieve_action=lambda query: calls.append(("retrieve", query)) or [],
            align_action=lambda query: calls.append(("align", query)) or "result",
            export_action=lambda result, query, directory, export_format:
                calls.append(("export", result, query, directory, export_format))
                or "file.h5",
            raw_download_action=lambda query, local:
                calls.append(("raw", query, local)) or [],
        )
        query = QueryParameters("/r", "20260725", "120000", ("BPM_A",))
        self.assertEqual(controller.retrieve_times(query), [])
        self.assertEqual(controller.align(query), "result")
        self.assertEqual(
            controller.export("result", query, "D:/out", "auto"),
            "file.h5",
        )
        self.assertEqual(controller.download_raw(query, "D:/raw"), [])
        self.assertEqual([item[0] for item in calls], [
            "retrieve", "align", "export", "raw",
        ])

    def test_mainwindow_has_no_direct_h5py_or_paramiko_import(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "apps"
            / "operator_gui"
            / "main.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import h5py", source)
        self.assertNotIn("import paramiko", source)
        self.assertIn("BackgroundWorker", source)
        self.assertIn("TaskState.ALIGNING", source)
        self.assertIn("TaskState.EXPORTING", source)


class BackgroundWorkerTests(unittest.TestCase):
    def test_success_failure_and_safe_boundary_cancel(self):
        completed = []
        worker = BackgroundWorker(lambda: 42)
        worker.signals.completed.connect(completed.append)
        worker.run()
        self.assertEqual(completed, [42])

        failures = []
        failing = BackgroundWorker(
            lambda: (_ for _ in ()).throw(ValueError("synthetic"))
        )
        failing.signals.failed.connect(failures.append)
        failing.run()
        self.assertIsInstance(failures[0], ValueError)

        cancelled = []
        pending = BackgroundWorker(lambda: 1)
        pending.signals.cancelled.connect(lambda: cancelled.append(True))
        pending.cancel()
        pending.run()
        self.assertEqual(cancelled, [True])


if __name__ == "__main__":
    unittest.main()
