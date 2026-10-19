"""The formal history page must align only explicitly selected BPMs."""

from __future__ import annotations

import os
import time
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from waveform_storage.metadata.history import CaptureRunSummary, CaptureSegment
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QDate, Qt
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog

from waveform_storage.config.registry import load_registry
from waveform_storage.gui.operator_contract import OperatorMode
from waveform_storage.gui.operator_window import (
    BpmSelectionDialog,
    HistoryDiagnosticDialog,
    OperatorMainWindow,
)
from waveform_storage.processing.alignment import DeviceSeries, align_devices
from waveform_storage.processing.bunch_id_diagnostics import diagnose_raw_steps


ROOT = Path(__file__).resolve().parents[1]
FIRST = "AC_LA_INJ1_BI_SBPM01"
SECOND = "AC_LA_INJ1_BI_SBPM02"


class Backend:
    mode = OperatorMode.CANDIDATE_V2

    def __init__(self) -> None:
        self.align_calls = []

    def retrieve_runs(self, selected_date):
        now = datetime(2026, 10, 2, 15, 3, 8, tzinfo=timezone.utc)
        return (CaptureRunSummary("run-10min", now, now, now, 1000, 600,
                                  "failed", 30, 2, 2, True),)

    def retrieve_segments(self, capture_request_id):
        now = datetime(2026, 10, 2, 15, 3, 8, tzinfo=timezone.utc)
        return tuple(CaptureSegment(capture_request_id, i + (2 if i >= 6 else 0),
            "230308", now, (FIRST, SECOND) if i == 0 else (FIRST,), "2026-10-02")
            for i in range(30))

    def align_segment(self, segment, device_ids):
        return self.align(date(2026, 10, 2), (segment.request_id, segment.capture_sequence), device_ids)

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

    @staticmethod
    def continue_to_spectrum(window):
        warning = window._spectrum_warning_dialog
        if warning is not None:
            next(button for button in warning.buttons()
                 if button.text() == "继续到频谱").click()

    def test_history_layout_and_normal_diagnostic_stays_quiet(self):
        from dataclasses import replace
        from types import SimpleNamespace

        backend = Backend()
        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(
                load_registry(ROOT / "config/devices.operator.json"), backend=backend,
                capture_registry=load_registry(ROOT / "config/devices.probe47.json"),
                journal_path=Path(temporary) / "journal.jsonl",
            )
            try:
                window.resize(1600, 1000)
                window._set_compact_history_toolbar(False)
                window._show_page(1)
                page_layout = window.pages.widget(1).layout()
                toolbar = page_layout.itemAt(0).widget()
                plot = page_layout.itemAt(2).widget()
                self.assertEqual(page_layout.stretch(0), 1)
                self.assertEqual(page_layout.stretch(2), 3)
                self.assertEqual(toolbar.layout().count(), 3)
                task_row = toolbar.layout().itemAt(0).layout()
                self.assertIs(task_row.itemAt(1).widget(), window.history_time)
                self.assertEqual(
                    window.history_time.sizeAdjustPolicy(),
                    QComboBox.AdjustToContents,
                )
                self.assertEqual(task_row.stretch(1), 0)
                self.assertEqual(task_row.stretch(2), 1)
                self.assertEqual(
                    toolbar.layout().itemAt(2).widget(), window.segment_label
                )
                self.assertGreaterEqual(toolbar.minimumHeight(), 120)
                self.assertGreaterEqual(plot.minimumHeight(), 280)

                window.current_history_run = backend.retrieve_runs(
                    date(2026, 10, 2)
                )[0]
                window.history_segments = backend.retrieve_segments(
                    window.current_history_run.request_id
                )
                window.segment_index.setRange(1, len(window.history_segments))
                window.segment_index.setValue(7)
                self.assertEqual(window.current_history_segment.capture_sequence, 8)
                self.assertTrue(window.segment_label.text().startswith("S00008 · "))

                ids = np.arange(1, 20_001, dtype=np.uint64)
                series = [
                    DeviceSeries(device_id, ids, ids.astype(float), ids.astype(float))
                    for device_id in (FIRST, SECOND)
                ]
                result = align_devices(series)
                window.alignment_result = result
                window._quality_result = result
                window._raw_step_report = diagnose_raw_steps(series)
                window._analysis_quality_preview = (
                    SimpleNamespace(
                        classification="ready", reason=None, quality={},
                        duration_seconds=None,
                    ),
                )
                window.alignment_status_label.setText(
                    window._alignment_status_text(result)
                )
                window._set_quality_status(result)
                window._refresh_history_diagnostic()
                self.assertEqual(window.alignment_status_label.text(), "已对齐 · 20,000 点")
                self.assertEqual(window.quality_status_button.text(), "质量详情")
                self.assertNotIn("✓ bunchID 正常", window.quality_status_button.text())
                self.assertTrue(window.history_diagnostic_bar.isHidden())
                self.assertTrue(window.history_diagnostic_button.isEnabled())

                window._raw_step_report = replace(
                    window._raw_step_report,
                    raw_step_compatible=False,
                    summary="原始步进需要确认",
                )
                window._refresh_history_diagnostic()
                self.assertFalse(window.history_diagnostic_bar.isHidden())

                window.resize(1280, 800)
                window._set_compact_history_toolbar(True)
                window.plot_mode.setCurrentIndex(
                    window.plot_mode.findData("device")
                )
                self.assertTrue(window.alignment_status_label.isHidden())
                window.plot_mode.setCurrentIndex(
                    window.plot_mode.findData("distribution")
                )
                self.assertFalse(window.alignment_status_label.isHidden())
            finally:
                window.close()

    def test_two_runs_and_stale_history_callbacks(self):
        from dataclasses import replace
        registry = load_registry(ROOT / "config/devices.operator.json")
        capture_registry = load_registry(ROOT / "config/devices.probe47.json")
        backend = Backend()
        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(registry, backend=backend, capture_registry=capture_registry,
                                        journal_path=Path(temporary) / "journal.jsonl")
            pending = []
            def defer(stage, operation, callback):
                pending.append((operation, callback))
            window._run_task = defer
            window.history_date.setDate(QDate(2026, 10, 2))
            window._retrieve_times()
            operation, callback = pending.pop(0)
            old_runs = operation()
            window.history_date.setDate(QDate(2026, 10, 3))
            callback(old_runs)
            self.assertEqual(window.history_runs, ())
            self.assertIsNone(window.current_history_segment)
            window.history_date.setDate(QDate(2026, 10, 2))
            window._retrieve_times()
            operation, callback = pending.pop(0)
            runs = operation()
            callback((runs[0], replace(runs[0], request_id="run-1min", duration_seconds=60),
                      replace(runs[0], request_id="historical", has_task_metadata=False,
                              duration_seconds=None, frequency_hz=None, requested_device_count=None)))
            self.assertEqual(window.history_time.count(), 3)
            self.assertIn("10 分钟", window.history_time.itemText(0))
            self.assertIn("1 分钟", window.history_time.itemText(1))
            self.assertIn("历史数据", window.history_time.itemText(2))
            old_operation, old_callback = pending.pop(0)
            window.history_time.setCurrentIndex(1)
            operation, callback = pending.pop(0)
            old_callback(old_operation())
            self.assertIsNone(window.current_history_segment)
            callback(operation())
            self.assertEqual(window.current_history_segment.request_id, "run-1min")
            window.history_selected_devices = (FIRST,)
            window.history_selection_key = window._history_key()
            window._align_selected()
            operation, callback = pending.pop(0)
            result = operation()
            window.segment_index.setValue(7)
            callback(result)
            self.assertIsNone(window.alignment_result)
            window._request_device_waveform(FIRST)
            operation, callback = pending.pop(0)
            result = operation()
            window.segment_index.setValue(8)
            callback(result)
            self.assertIsNone(window.device_waveform_result)
            self.assertIsNone(window.plot_canvas)
            window.close()

    def test_raw_step_warning_requires_dialog_confirmation_and_rejects_stale_continue(self):
        from types import SimpleNamespace

        ids = np.array([10, 20, 30, 40], dtype=np.uint64)
        report = diagnose_raw_steps([
            DeviceSeries(FIRST, ids, ids.astype(float), ids.astype(float)),
            DeviceSeries(SECOND, ids + np.array([0, 1, 3, 7], dtype=np.uint64),
                         ids.astype(float), ids.astype(float)),
        ])
        snapshot = SimpleNamespace(raw_step_report=report)

        class PreparedBackend(Backend):
            def prepare_segment(self, segment, devices):
                return snapshot

            def align_prepared_segment(self, prepared):
                raise AssertionError("alignment must wait for explicit dialog confirmation")

        with TemporaryDirectory() as temporary:
            backend = PreparedBackend()
            window = OperatorMainWindow(
                load_registry(ROOT / "config/devices.operator.json"), backend=backend,
                ui_only=True, journal_path=Path(temporary) / "journal.jsonl",
            )
            try:
                window.current_history_segment = backend.retrieve_segments("run-10min")[0]
                window.history_selected_devices = (FIRST, SECOND)
                window.history_selection_key = window._history_key()
                with patch.object(window, "_execute_history_alignment") as execute, patch.object(
                    window, "_run_task", side_effect=lambda stage, operation, callback: callback(operation())
                ):
                    window._align_selected()
                    warning = window._raw_step_warning_dialog
                    self.assertIsNotNone(warning)
                    self.assertEqual(warning.defaultButton().text(), "取消")
                    execute.assert_not_called()
                    warning.defaultButton().click()
                    self.app.processEvents()
                    self.assertIsNone(window._raw_step_warning_dialog)
                    self.assertTrue(window._preflight_confirmation_required)
                    execute.assert_not_called()

                    window._align_selected()
                    warning = window._raw_step_warning_dialog
                    next(button for button in warning.buttons()
                         if button.text() == "继续严格对齐").click()
                    self.app.processEvents()
                    execute.assert_called_once()
                    self.assertIs(execute.call_args.kwargs["prepared"], snapshot)

                    window._preflight_confirmation_required = True
                    window._align_selected()
                    warning = window._raw_step_warning_dialog
                    window._history_generation += 1
                    next(button for button in warning.buttons()
                         if button.text() == "继续严格对齐").click()
                    self.app.processEvents()
                    execute.assert_called_once()
            finally:
                window.close()

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
            self.assertEqual(window.data_duration_label.text(), "分析器推算跨度：—")
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

            self.assertEqual(window.history_time.count(), 1)
            self.assertIn("1000 Hz | 10 分钟", window.history_time.itemText(0))
            self.assertEqual((window.segment_index.minimum(), window.segment_index.maximum()), (1, 30))

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
                [(date(2026, 10, 2), ("run-10min", 0), (FIRST,))],
            )
            self.assertEqual(window.alignment_result.device_ids, (FIRST,))
            window.history_spectrum_button.click()
            self.continue_to_spectrum(window)
            self.assertEqual(window.spectrum_panel.sample_rate.value(), 1000)
            window.segment_index.setValue(7)
            self.assertIn("S00008", window.segment_label.text())
            self.assertEqual(window.history_selected_devices, ())
            self.assertIsNone(window.alignment_result)
            self.assertIsNone(window.spectrum_panel.alignment)
            self.assertEqual(window.spectrum_panel.sample_rate.value(), 0)
            def choose_partial(dialog):
                self.assertEqual(dialog.list_widget.count(), 1)
                return QDialog.Accepted
            with patch.object(BpmSelectionDialog, "exec_", choose_partial):
                window.select_bpm_button.click()
            window.align_button.click()
            self.wait_for_workers(window)
            self.assertEqual(backend.align_calls[-1][1], ("run-10min", 8))
            window.history_spectrum_button.click()
            self.continue_to_spectrum(window)
            self.assertEqual(window.spectrum_panel.sample_rate.value(), 1000)
            window.plot_mode.setCurrentIndex(window.plot_mode.findData("device"))
            window._request_device_waveform(FIRST)
            self.wait_for_workers(window)
            self.assertEqual(backend.align_calls[-1][1], ("run-10min", 8))
            self.assertEqual(window.device_waveform_key, (("segment", "run-10min", 8), FIRST))
            window.history_date.setDate(QDate(2026, 10, 3))
            self.assertEqual(window.history_selected_devices, ())
            self.assertIsNone(window.alignment_result)
            self.assertEqual(window.plot_device.count(), 0)
            self.assertEqual(window.spectrum_panel.sample_rate.value(), 0)
            window.close()

    def test_history_spectrum_handoff_tracks_task_rate_and_real_segment_identity(self):
        class MultiRunBackend(Backend):
            def retrieve_runs(self, selected_date):
                run = super().retrieve_runs(selected_date)[0]
                return (run, replace(run, request_id="task-500", frequency_hz=500),
                        replace(run, request_id="task-missing", frequency_hz=None,
                                has_task_metadata=False))

        backend = MultiRunBackend()
        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(
                load_registry(ROOT / "config/devices.operator.json"), backend=backend,
                capture_registry=load_registry(ROOT / "config/devices.probe47.json"),
                journal_path=Path(temporary) / "journal.jsonl")
            try:
                window.history_date.setDate(QDate(2026, 10, 2))
                window.retrieve_button.click()
                self.wait_for_workers(window)
                for position, rate in enumerate((1000, 500, None)):
                    window.history_time.setCurrentIndex(position)
                    self.wait_for_workers(window)
                    self.assertIsNone(window.alignment_result)
                    self.assertIsNone(window.spectrum_panel.alignment)
                    self.assertEqual(window.spectrum_panel.sample_rate.value(), 0)
                    self.assertNotIn("历史任务记录读取", window.spectrum_panel.input_note.text())
                    window.segment_index.setValue(7)
                    segment = window.current_history_segment
                    self.assertEqual(segment.capture_sequence, 8)
                    self.assertEqual(segment.request_id, window.current_history_run.request_id)
                    with patch.object(BpmSelectionDialog, "exec_", return_value=QDialog.Accepted):
                        window.select_bpm_button.click()
                    window.align_button.click()
                    self.wait_for_workers(window)
                    self.assertEqual(backend.align_calls[-1][1], (segment.request_id, 8))
                    with patch.object(window.spectrum_panel, "use_alignment",
                                      wraps=window.spectrum_panel.use_alignment) as handoff, \
                         patch.object(backend, "retrieve_runs") as query_runs, \
                         patch.object(backend, "retrieve_segments") as query_segments, \
                         patch.object(backend, "align_segment") as align:
                        window.history_spectrum_button.click()
                        self.continue_to_spectrum(window)
                        handoff.assert_called_once_with(
                            window.alignment_result, sample_rate_hz=rate,
                            history_request_id=segment.request_id, capture_sequence=segment.capture_sequence,
                        )
                        query_runs.assert_not_called()
                        query_segments.assert_not_called()
                        align.assert_not_called()
                    self.assertEqual(window.pages.currentIndex(), 2)
                    self.assertEqual(window.spectrum_panel.sample_rate.value(), rate or 0)
                    if rate:
                        self.assertIn(f"历史任务记录读取：{rate} Hz", window.spectrum_panel.input_note.text())
                    else:
                        self.assertIn("历史记录没有可用采样率", window.spectrum_panel.input_note.text())

                # A task cannot supply a rate for a segment belonging to another request.
                window.current_history_run = backend.retrieve_runs(date(2026, 10, 2))[0]
                window.history_spectrum_button.click()
                self.continue_to_spectrum(window)
                self.assertEqual(window.spectrum_panel.sample_rate.value(), 0)
                self.assertIsNone(window.spectrum_panel.history_request_id)
                self.assertIsNone(window.spectrum_panel.history_capture_sequence)
            finally:
                window.close()

    def test_invalid_spectrum_config_does_not_block_history_alignment(self):
        backend = Backend()
        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(
                load_registry(ROOT / "config/devices.operator.json"), backend=backend,
                capture_registry=load_registry(ROOT / "config/devices.probe47.json"),
                journal_path=Path(temporary) / "journal.jsonl",
            )
            try:
                window.history_date.setDate(QDate(2026, 10, 2))
                run = backend.retrieve_runs(date(2026, 10, 2))[0]
                segment = backend.retrieve_segments(run.request_id)[0]
                window.current_history_run = run
                window.current_history_segment = segment
                pending = []
                window._run_task = lambda stage, operation, callback: pending.append(
                    (operation, callback)
                )
                with patch.object(
                    window.spectrum_panel,
                    "analysis_config_after_history_handoff",
                    side_effect=ValueError("频带上下限无效"),
                ):
                    window._execute_history_alignment(segment, (FIRST,), window._history_generation)
                self.assertEqual(len(pending), 1)
                operation, callback = pending.pop()
                result, preview = operation()
                self.assertEqual(backend.align_calls[-1][2], (FIRST,))
                self.assertEqual(result.device_ids, (FIRST,))
                self.assertEqual(preview[0].classification, "invalid")
                self.assertIn("频谱参数需修正", preview[0].reason)
                callback((result, preview))
                self.assertIs(window.alignment_result, result)
                self.assertEqual(window._analysis_quality_preview[0].classification, "invalid")
            finally:
                window.close()

    def test_device_waveform_callback_is_invalidated_by_plot_mode_change(self):
        backend = Backend()
        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(
                load_registry(ROOT / "config/devices.operator.json"), backend=backend,
                capture_registry=load_registry(ROOT / "config/devices.probe47.json"),
                journal_path=Path(temporary) / "journal.jsonl",
            )
            try:
                run = backend.retrieve_runs(date(2026, 10, 2))[0]
                segment = backend.retrieve_segments(run.request_id)[0]
                window.current_history_run = run
                window.current_history_segment = segment
                window.plot_device.clear()
                window.plot_device.addItem(FIRST, FIRST)
                window.plot_mode.setCurrentIndex(window.plot_mode.findData("device"))
                pending = []
                window._run_task = lambda stage, operation, callback: pending.append(
                    (operation, callback)
                )
                window._request_device_waveform(FIRST)
                operation, callback = pending.pop()
                result = operation()
                window.plot_mode.setCurrentIndex(
                    window.plot_mode.findData("distribution")
                )
                callback(result)
                self.assertIsNone(window.device_waveform_result)
                self.assertIsNone(window.device_waveform_key)
            finally:
                window.close()

    def test_history_diagnostic_accepts_unassessed_preview(self):
        from types import SimpleNamespace

        dialog = HistoryDiagnosticDialog(
            None, None,
            (SimpleNamespace(classification="invalid", reason="参数需修正"),),
        )
        try:
            self.assertEqual(dialog.windowTitle(), "历史数据质量诊断")
        finally:
            dialog.close()

    def test_archiver_handoff_preview_matches_profile_reset_behavior(self):
        from waveform_storage.analysis.models import AnalysisConfig

        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(
                load_registry(ROOT / "config/devices.operator.json"), backend=Backend(),
                capture_registry=load_registry(ROOT / "config/devices.probe47.json"),
                journal_path=Path(temporary) / "journal.jsonl",
            )
            try:
                panel = window.spectrum_panel
                panel.source_combo.setCurrentIndex(panel.source_combo.findData("archiver"))
                panel.profile_combo.setCurrentIndex(panel.profile_combo.findData("high_band"))
                panel.low.setValue(42)
                self.assertEqual(
                    panel.analysis_config_after_history_handoff().search_low_hz, 42
                )

                panel.profile_combo.setCurrentIndex(panel.profile_combo.findData("low_v2"))
                expected = AnalysisConfig.for_profile(
                    "high_band", zero_policy=panel.zero_combo.currentData()
                )
                self.assertEqual(panel.analysis_config_after_history_handoff(), expected)
            finally:
                window.close()

    def test_spectrum_warning_escape_keeps_history_page(self):
        backend = Backend()
        result = align_devices([
            DeviceSeries(FIRST, np.array([1000, 3000, 5000], dtype=np.uint64),
                         np.array([1, 2, 3]), np.array([4, 5, 6]))
        ])
        with TemporaryDirectory() as temporary:
            window = OperatorMainWindow(
                load_registry(ROOT / "config/devices.operator.json"), backend=backend,
                capture_registry=load_registry(ROOT / "config/devices.probe47.json"),
                journal_path=Path(temporary) / "journal.jsonl",
            )
            try:
                window.load_alignment_result(result)
                original_page = window.pages.currentIndex()
                window._analyze_history_alignment()
                warning = window._spectrum_warning_dialog
                self.assertIsNotNone(warning)
                self.assertIsNotNone(warning.escapeButton())
                warning.escapeButton().click()
                self.app.processEvents()
                self.assertEqual(window.pages.currentIndex(), original_page)
                self.assertIsNone(window.spectrum_panel.alignment)
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
