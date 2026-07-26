from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    from waveform_storage.config.registry import load_registry
    from waveform_storage.gui.experimental_window import ExperimentalMainWindow
    from waveform_storage.gui.operator_backend import DemoOperatorBackend
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
            "waveform_storage.gui.experimental_window.QMessageBox.information"
        ), patch(
            "waveform_storage.gui.experimental_window.QFileDialog."
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
                "waveform_storage.gui.experimental_window.show_operation_error"
            ) as error_dialog:
                window._show_error_demo()
                error_dialog.assert_called_once()
                previous_rows = window.task_table.rowCount()
                window._show_validation_error(
                    "测试错误",
                    "验证错误日志",
                    "TEST_VALIDATION_ERROR",
                    "无需处理",
                )
                self.assertEqual(window.task_table.rowCount(), previous_rows + 1)
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


if __name__ == "__main__":
    unittest.main()
