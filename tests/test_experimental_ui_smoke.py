from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QLabel, QMessageBox, QTableWidget

    from waveform_storage.config.registry import load_registry
    from waveform_storage.acquisition.protocol import CaptureResponse, PROTOCOL_VERSION
    from waveform_storage.gui.experimental_window import ExperimentalMainWindow
    from waveform_storage.gui.operator_backend import (
        DemoOperatorBackend,
        OperatorMode,
    )
    from waveform_storage.transport.zmq_transport import ZmqTimeout
except ImportError as exc:  # pragma: no cover - optional desktop dependency
    raise unittest.SkipTest(f"PyQt5 desktop dependency unavailable: {exc}")


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ExperimentalUiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_workers(self, window, timeout=5):
        deadline = time.monotonic() + timeout
        while window._active_workers and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(window._active_workers, "background UI task timed out")

    def test_demo_buttons_complete_without_network(self):
        registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        backend = DemoOperatorBackend(registry)
        with tempfile.TemporaryDirectory() as directory, patch(
            "waveform_storage.gui.operator_window.QMessageBox.information"
        ), patch(
            "waveform_storage.gui.operator_window.QFileDialog."
            "getExistingDirectory",
            return_value=directory,
        ):
            journal = Path(directory) / "operator-ui.jsonl"
            window = ExperimentalMainWindow(
                registry,
                backend=backend,
                journal_path=journal,
            )
            window.area_model.item(0, 0).setCheckState(Qt.Checked)
            window.start_button.click()
            self.wait_for_workers(window)
            self.assertTrue(backend.last_request_json)

            window.retrieve_button.click()
            self.wait_for_workers(window)
            self.assertEqual(window.history_time.count(), 3)

            window.align_button.click()
            self.wait_for_workers(window)
            self.assertIsNotNone(window.alignment_result)
            self.assertIsNotNone(window.plot_canvas)

            window.show_id_button.click()
            window.plot_button.click()
            self.assertIn("bunchID", window.bunch_id_label.text())
            window.plot_mode.setCurrentIndex(1)
            window.plot_button.click()
            self.wait_for_workers(window)
            self.assertEqual(window.plot_mode.currentData(), "device")
            self.assertEqual(
                window.plot_canvas.figure.axes[0].get_xlabel().split("·")[0].strip(),
                "BunchID",
            )

            window.export_button.click()
            self.wait_for_workers(window)
            window.raw_button.click()
            self.wait_for_workers(window)
            self.assertTrue(tuple(Path(directory).rglob("*.h5")))

            with patch(
                "waveform_storage.gui.operator_window.show_operation_error"
            ) as error_dialog:
                window._show_error_demo()
                error_dialog.assert_called_once()
                window._show_validation_error(
                    "测试错误",
                    "验证错误日志",
                    "TEST_VALIDATION_ERROR",
                    "无需处理",
                )
                self.assertIsNone(window.findChild(QTableWidget, "taskTable"))
                self.assertIn(
                    "TEST_VALIDATION_ERROR",
                    journal.read_text(encoding="utf-8"),
                )

            class DeferredPool:
                worker = None

                def start(self, worker):
                    self.worker = worker

            real_pool = window.thread_pool
            deferred = DeferredPool()
            window.thread_pool = deferred
            window._run_task("竞态测试", lambda: "ok", lambda _result: None)
            self.assertTrue(window._busy)
            self.assertFalse(window.align_button.isEnabled())
            self.assertIsNotNone(deferred.worker)
            window._finish_worker(deferred.worker)
            window.thread_pool = real_pool
            self.assertFalse(window._busy)

            window.close()

    def test_candidate_registry_renders_383_devices_without_network(self):
        registry = load_registry(
            PROJECT_ROOT / "config" / "devices.candidate.json"
        )
        backend = DemoOperatorBackend(registry)
        with patch(
            "waveform_storage.gui.operator_window.QMessageBox.information"
        ):
            window = ExperimentalMainWindow(registry, backend=backend)

            self.assertEqual(
                sum(area.device_count for area in window.area_model.areas),
                383,
            )
            self.assertEqual(window.area_model.rowCount(), 24)
            self.assertEqual(window.windowTitle(), "SHINE Waveform Console")
            self.assertEqual(
                [window.duration_combo.itemData(i) for i in range(window.duration_combo.count())],
                [1, 10, 30, 60, 70],
            )
            self.assertEqual(window.duration_combo.currentData(), 70)
            self.assertEqual(window.area_model.item(0, 0).text(), "ING")
            self.assertTrue(window.area_model.item(0, 0).isCheckable())
            self.assertIsNone(window.findChild(QLabel, "environment"))
            self.assertIsNone(window.findChild(QLabel, "environmentPill"))
            bi_row = next(
                index for index, area in enumerate(window.area_model.areas)
                if area.area_id == "BI"
            )
            first_status = window.area_model.item(bi_row, 0).child(0, 3).text()
            self.assertIn("待验证", first_status)

            window.area_model.item(bi_row, 0).setCheckState(Qt.Checked)
            window.start_button.click()
            self.wait_for_workers(window)
            self.assertTrue(backend.last_request_json)
            window.close()

    def test_combined_display_area_sends_original_sections(self):
        registry = load_registry(
            PROJECT_ROOT / "config" / "devices.candidate.json"
        )
        backend = DemoOperatorBackend(registry)
        with patch(
            "waveform_storage.gui.operator_window.QMessageBox.information"
        ):
            window = ExperimentalMainWindow(registry, backend=backend)
            row = next(
                index for index, area in enumerate(window.area_model.areas)
                if area.area_id == "EXT1&2"
            )
            window.area_model.item(row, 0).setCheckState(Qt.Checked)
            window.start_button.click()
            self.wait_for_workers(window)
            request = json.loads(backend.last_request_json)
            self.assertEqual(
                request["area_ids"], ["AC_LA_EXT1", "AC_LA_EXT2"]
            )
            self.assertEqual(len(request["devices"]), 8)
            window.close()

    def test_candidate_registry_cannot_send_connected_capture(self):
        registry = load_registry(
            PROJECT_ROOT / "config" / "devices.candidate.json"
        )
        backend = DemoOperatorBackend(registry)
        backend.mode = OperatorMode.LEGACY
        window = ExperimentalMainWindow(registry, backend=backend)
        window.area_model.item(0, 0).setCheckState(Qt.Checked)

        with patch.object(window, "_present_error") as present_error:
            window.start_button.click()

        present_error.assert_called_once()
        error = present_error.call_args.args[0]
        self.assertEqual(error.code, "CAPTURE_DEVICES_NOT_ENABLED")
        self.assertFalse(backend.last_request_json)
        self.assertFalse(window._active_workers)
        window.close()

    def test_operator_display_keeps_new_devices_out_of_capture(self):
        display_registry = load_registry(
            PROJECT_ROOT / "config" / "devices.operator.json"
        )
        capture_registry = load_registry(
            PROJECT_ROOT / "dist" / "deepin20.9" / "devices.probe47.json"
        )
        backend = DemoOperatorBackend(display_registry)
        backend.mode = OperatorMode.CANDIDATE_V2
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        window = ExperimentalMainWindow(
            display_registry,
            backend=backend,
            capture_registry=capture_registry,
            journal_path=Path(temporary.name) / "operator-ui.jsonl",
        )
        self.assertEqual(window.area_model.rowCount(), 24)
        self.assertEqual(sum(a.device_count for a in window.area_model.areas), 383)
        inj1_row = next(
            index for index, area in enumerate(window.area_model.areas)
            if area.area_id == "BI"
        )
        with patch(
            "waveform_storage.gui.operator_window.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ), patch(
            "waveform_storage.gui.operator_window.QMessageBox.information"
        ):
            window.area_model.item(inj1_row, 0).setCheckState(Qt.Checked)
            window.start_button.click()
            self.wait_for_workers(window)
        request = json.loads(backend.last_request_json)
        self.assertEqual(request["area_ids"], ["INJ1"])
        self.assertEqual(len(request["devices"]), 20)
        self.assertEqual(
            set(request["devices"]),
            {
                device.id for device in capture_registry.devices
                if device.section == "INJ1"
            },
        )
        window.area_model.item(inj1_row, 0).setCheckState(Qt.Unchecked)
        l2_row = next(
            index for index, area in enumerate(window.area_model.areas)
            if area.area_id == "L2"
        )
        window.area_model.item(l2_row, 0).setCheckState(Qt.Checked)
        with patch.object(window, "_present_error") as present_error:
            window.start_button.click()
        self.assertEqual(
            present_error.call_args.args[0].code,
            "CAPTURE_NO_AREA",
        )
        self.assertEqual(json.loads(backend.last_request_json), request)
        window.close()

    def test_capture_monitor_reports_terminal_success(self):
        registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        backend = DemoOperatorBackend(registry)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        window = ExperimentalMainWindow(
            registry,
            backend=backend,
            journal_path=Path(temporary.name) / "operator-ui.jsonl",
        )
        request_id = "12345678-1234-4234-8234-123456789abc"

        with patch(
            "waveform_storage.gui.operator_window.QMessageBox.information"
        ) as information:
            window._begin_capture_monitor(request_id, 60)
            deadline = time.monotonic() + 5
            while (
                window._capture_status_worker is not None
                and time.monotonic() < deadline
            ):
                self.app.processEvents()
                time.sleep(0.01)
            self.app.processEvents()

        self.assertIsNone(window._capture_request_id)
        self.assertFalse(window._busy)
        self.assertEqual(window.task_state_text.text(), "采集完成")
        self.assertEqual(window.task_progress.value(), 100)
        information.assert_called_once()
        window.close()

    def test_capture_monitor_keeps_task_after_timeouts_and_blocks_resubmission(self):
        registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        backend = DemoOperatorBackend(registry)
        backend.mode = OperatorMode.CANDIDATE_V2
        request_id = "12345678-1234-4234-8234-123456789abc"
        running = CaptureResponse(
            PROTOCOL_VERSION, request_id, True, "running", "capture is running"
        )

        def wait_for_status(window):
            deadline = time.monotonic() + 5
            while window._capture_status_worker is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.app.processEvents()
            self.assertIsNone(window._capture_status_worker)

        with tempfile.TemporaryDirectory() as directory, patch(
            "waveform_storage.gui.operator_window.QMessageBox.information"
        ) as information:
            journal = Path(directory) / "operator-ui.jsonl"
            with patch.object(
                backend,
                "get_capture_status",
                side_effect=[ZmqTimeout("timeout")] * 3 + [running],
            ):
                window = ExperimentalMainWindow(
                    registry, backend=backend, journal_path=journal
                )
                window._begin_capture_monitor(request_id, 4200)
                for attempt in range(3):
                    if attempt:
                        window._poll_capture_status()
                    wait_for_status(window)
                self.assertEqual(window._capture_request_id, request_id)
                self.assertIn("状态待确认", window.task_state_text.text())
                self.assertTrue(window.start_button.isEnabled())
                self.assertEqual(window.start_button.text(), "状态待确认")
                self.assertTrue(window.capture_state_path.exists())
                window.start_button.click()
                self.assertEqual(information.call_args.args[1], "采集状态待确认")
                self.assertFalse(backend.last_request_json)
                window._poll_capture_status()
                wait_for_status(window)
                self.assertIn("采集中", window.task_state_text.text())
                self.assertEqual(window.start_button.text(), "采集中")
                window._capture_timer.stop()
                window.close()

            with patch.object(backend, "get_capture_status", return_value=running):
                reopened = ExperimentalMainWindow(
                    registry, backend=backend, journal_path=journal
                )
                deadline = time.monotonic() + 5
                while reopened._capture_request_id is None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                wait_for_status(reopened)
                self.assertEqual(reopened._capture_request_id, request_id)
                self.assertIn("采集中", reopened.task_state_text.text())
                reopened._capture_timer.stop()
                reopened.close()


if __name__ == "__main__":
    unittest.main()
