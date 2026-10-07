"""The formal history page must align only explicitly selected BPMs."""

from __future__ import annotations

import os
import time
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QDate, Qt
from PyQt5.QtWidgets import QApplication, QDialog

from waveform_storage.config.registry import load_registry
from waveform_storage.gui.operator_contract import OperatorMode
from waveform_storage.gui.operator_window import (
    BpmSelectionDialog,
    OperatorMainWindow,
)
from waveform_storage.processing.alignment import DeviceSeries, align_devices


ROOT = Path(__file__).resolve().parents[1]
FIRST = "AC_LA_INJ1_BI_SBPM01"
SECOND = "AC_LA_INJ1_BI_SBPM02"


class Backend:
    mode = OperatorMode.CANDIDATE_V2

    def __init__(self) -> None:
        self.align_calls = []

    def retrieve_times(self, selected_date):
        return ("230308",)

    def available_devices(self, selected_date, time_sec, device_ids):
        assert selected_date == date(2026, 10, 2)
        assert time_sec == "230308"
        assert FIRST in device_ids and SECOND in device_ids
        return (FIRST, SECOND)

    def align(self, selected_date, time_sec, device_ids):
        self.align_calls.append((selected_date, time_sec, device_ids))
        return align_devices(
            [
                DeviceSeries(
                    device_id,
                    np.array([1, 2, 3], dtype=np.uint64),
                    np.array([10, 11, 12]),
                    np.array([20, 21, 22]),
                )
                for device_id in device_ids
            ]
        )


class FormalHistorySelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_workers(self, window):
        deadline = time.monotonic() + 5
        while window._active_workers and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(window._active_workers)

    def test_frequency_is_displayed_after_startup_query(self):
        class FrequencyBackend(Backend):
            def __init__(self):
                super().__init__()
                self.frequency_calls = 0

            def get_timing_frequency(self):
                self.frequency_calls += 1
                return 5.0

        backend = FrequencyBackend()
        registry = load_registry(ROOT / "config/devices.operator.json")
        capture_registry = load_registry(ROOT / "config/devices.probe47.json")
        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(
                registry,
                backend=backend,
                capture_registry=capture_registry,
                journal_path=Path(temporary) / "journal.jsonl",
            )
            window.show()
            deadline = time.monotonic() + 5
            while window.frequency_value.text() != "5 Hz" and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertEqual(window.frequency_value.text(), "5 Hz")
            self.assertEqual(backend.frequency_calls, 1)
            window.close()

    def test_offline_window_marks_frequency_unavailable(self):
        registry = load_registry(ROOT / "config/devices.operator.json")
        capture_registry = load_registry(ROOT / "config/devices.probe47.json")
        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(
                registry,
                backend=Backend(),
                capture_registry=capture_registry,
                ui_only=True,
                journal_path=Path(temporary) / "journal.jsonl",
            )
            self.assertEqual(window.frequency_value.text(), "未连接")
            self.assertIn("500 Hz", [window.frequency_combo.itemText(i)
                                     for i in range(window.frequency_combo.count())])
            first_area = window.area_model.item(0, 0)
            self.assertEqual(first_area.text(), "ING")
            self.assertEqual(
                first_area.data(window.area_model.AREA_ROLE), "BI"
            )
            first_child = next(first_area.child(i, 0)
                               for i in range(first_area.rowCount())
                               if first_area.child(i, 0).isCheckable())
            first_child.setCheckState(Qt.Checked)
            self.assertEqual(window.area_model.selected_devices(),
                             (first_child.data(window.area_model.DEVICE_ID_ROLE),))
            result = align_devices([DeviceSeries(FIRST,
                np.array([100, 250, 400], dtype=np.uint64),
                np.array([1, 2, 3]), np.array([4, 5, 6]))])
            window._set_data_duration(result)
            self.assertEqual(window.data_duration_label.text(), "数据时长：0.000300 s")
            window.close()

    def test_history_selection_ignores_capture_area_selection(self):
        backend = Backend()
        registry = load_registry(ROOT / "config/devices.operator.json")
        capture_registry = load_registry(ROOT / "config/devices.probe47.json")
        with TemporaryDirectory() as temporary, patch(
            "waveform_storage.gui.operator_window.QMessageBox.information"
        ):
            window = OperatorMainWindow(
                registry,
                backend=backend,
                capture_registry=capture_registry,
                journal_path=Path(temporary) / "journal.jsonl",
            )
            window.area_model.item(0, 0).setCheckState(Qt.Checked)
            window.history_date.setDate(QDate(2026, 10, 2))
            window.retrieve_button.click()
            self.wait_for_workers(window)

            def choose_first(dialog):
                self.assertEqual(dialog.list_widget.count(), 2)
                dialog.list_widget.item(1).setCheckState(Qt.Unchecked)
                return QDialog.Accepted

            with patch.object(BpmSelectionDialog, "exec_", choose_first):
                window.select_bpm_button.click()
                self.wait_for_workers(window)
            self.assertEqual(window.history_selected_devices, (FIRST,))

            window.align_button.click()
            self.wait_for_workers(window)
            self.assertEqual(
                backend.align_calls,
                [(date(2026, 10, 2), "230308", (FIRST,))],
            )
            self.assertEqual(window.alignment_result.device_ids, (FIRST,))
            window.history_date.setDate(QDate(2026, 10, 3))
            self.assertEqual(window.history_selected_devices, ())
            self.assertIsNone(window.alignment_result)
            self.assertEqual(window.plot_device.count(), 0)
            window.close()


if __name__ == "__main__":
    unittest.main()
