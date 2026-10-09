"""Real Qt third-page workflow including imports, history handoff and export."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
import time
import unittest
from dataclasses import asdict
from unittest.mock import Mock, patch
from zipfile import ZipFile

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
import numpy as np

from waveform_storage.config.registry import load_registry
from waveform_storage.gui.operator_contract import OperatorMode
from waveform_storage.gui.operator_window import OperatorMainWindow
from waveform_storage.io.hdf5_io import read_legacy_merged_hdf5
from waveform_storage.analysis.models import AnalysisConfig, AnalysisRun, ChannelResult
from waveform_storage.analysis.status import DISPLAY_STATUS_LABELS
from waveform_storage.metadata.history import CaptureRunSummary, CaptureSegment
from test_spectrum_analysis import hdf_fixture


ROOT = Path(__file__).resolve().parents[1]


class OfflineBackend:
    mode = OperatorMode.CANDIDATE_V2

    def __getattr__(self, name):
        raise AssertionError("local spectrum workflow must not contact capture backend: " + name)


class SpectrumGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, directory):
        return OperatorMainWindow(load_registry(ROOT / "config/devices.operator.json"),
                                  capture_registry=load_registry(ROOT / "config/devices.probe47.json"),
                                  backend=OfflineBackend(), ui_only=True,
                                  journal_path=Path(directory) / "ui.jsonl")

    def _wait(self, panel):
        deadline = time.monotonic() + 10
        while panel.job_id and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(panel.job_id, "GUI analysis did not finish")
        self.assertIsNotNone(panel.last_run, panel.status_label.text())

    def _close_window(self, window):
        self.app.processEvents()
        window.close()
        self.app.processEvents()

    def test_common_parameters_and_collapsed_advanced_parameters(self):
        with TemporaryDirectory() as d:
            window = self._window(d)
            window.spectrum_nav.click()
            window.show()
            self.app.processEvents()
            try:
                panel = window.spectrum_panel
                self.assertTrue(panel.profile_combo.isVisible())
                self.assertTrue(panel.sample_rate.isVisible())
                self.assertTrue(panel.advanced_frame.isHidden())
                self.assertFalse(panel.zero_combo.isVisible())
                self.assertEqual(panel.advanced_button.text(), "高级分析参数")
                self.assertEqual(panel.table.columnCount(), 5)
                self.assertEqual(panel.table.horizontalHeaderItem(4).text(), "命中块")
                self.assertTrue(panel.table.isColumnHidden(4))
                self.assertEqual(panel.show_block_hits.text(), "显示命中块列")
                self.assertTrue(panel.advanced_frame.isAncestorOf(panel.show_block_hits))
                self.assertFalse(panel.show_block_hits.isChecked())
                self.assertFalse(panel.show_block_hits.isVisible())
                self.assertNotIn(panel.show_block_hits, panel.parameter_widgets)
                self.assertEqual(panel.display_mode.currentData(), "linear")
                self.assertFalse(any("管理员" in button.text()
                                     for button in window.findChildren(type(panel.run_button))))

                panel.low.setValue(42)
                panel.zero_combo.setCurrentIndex(panel.zero_combo.findData("mask_double_zero"))
                panel.advanced_button.click()
                self.assertTrue(panel.advanced_frame.isVisible())
                self.assertTrue(panel.zero_combo.isVisible())
                self.assertTrue(panel.show_block_hits.isVisible())
                panel.show_block_hits.click()
                self.assertFalse(panel.table.isColumnHidden(4))
                panel.advanced_button.click()
                self.assertTrue(panel.advanced_frame.isHidden())
                self.assertEqual(panel.low.value(), 42)
                self.assertEqual(panel.zero_combo.currentData(), "mask_double_zero")
                self.assertTrue(panel.show_block_hits.isChecked())
                panel.advanced_button.click()
                self.assertTrue(panel.show_block_hits.isVisible())
                self.assertTrue(panel.show_block_hits.isChecked())

                for profile in ("low_v1", "high_band", "low_v2"):
                    panel.profile_combo.setCurrentIndex(panel.profile_combo.findData(profile))
                    self.assertTrue(panel.show_block_hits.isChecked())
                    self.assertFalse(panel.table.isColumnHidden(4))
                self.assertAlmostEqual(panel.low.value(), .142)
                self.assertAlmostEqual(panel.high.value(), .162)
                self.assertEqual(panel.window_seconds.value(), 300)
                self.assertEqual(panel.block_seconds.value(), 300)
                self.assertEqual(panel.zero_combo.currentData(), "mask_double_zero")
                panel.show_block_hits.click()
                self.assertTrue(panel.table.isColumnHidden(4))
            finally:
                self._close_window(window)

    def test_third_navigation_runs_file_analysis_and_exports_results(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "input.h5"
            hdf_fixture(p, fs=500)
            window = self._window(d)
            try:
                window.spectrum_nav.click()
                self.assertEqual(window.pages.currentIndex(), 2)
                self.assertEqual(window.page_title.text(), "频谱分析")
                self.assertFalse(window.start_button.isEnabled())
                panel = window.spectrum_panel
                panel.path_edit.setText(str(p))
                panel.run_button.click()
                self.assertTrue(all(not widget.isEnabled() for widget in panel.parameter_widgets))
                self.assertTrue(panel.show_block_hits.isEnabled())
                self._wait(panel)
                self.assertTrue(all(widget.isEnabled() for widget in panel.parameter_widgets))
                self.assertEqual(panel.table.rowCount(), 4)
                self.assertTrue(panel.table.isColumnHidden(4))
                first_run = panel.last_run
                with patch.object(panel.jobs, "submit", wraps=panel.jobs.submit) as submit:
                    panel.show_block_hits.setChecked(True)
                    submit.assert_not_called()
                    self.assertIs(panel.last_run, first_run)
                    panel.run_button.click()
                    panel.show_block_hits.setChecked(False)
                    self.assertTrue(panel.table.isColumnHidden(4))
                    panel.show_block_hits.setChecked(True)
                    self.assertFalse(panel.table.isColumnHidden(4))
                    self._wait(panel)
                    self.assertEqual(submit.call_count, 1)
                self.assertFalse(panel.table.isColumnHidden(4))
                self.assertAlmostEqual(panel.last_run.channels[0].peak_hz, 47.6)
                panel.table.selectRow(0)
                self.app.processEvents()
                self.assertTrue(panel.figure.axes[0].lines)
                output = Path(d) / "saved.zip"
                last_run = panel.last_run
                with patch("waveform_storage.gui.spectrum_panel.QFileDialog.getSaveFileName",
                           return_value=(str(output), "")) as save_dialog, \
                     patch.object(panel.jobs, "submit", wraps=panel.jobs.submit) as submit:
                    panel.export_button.click()
                    self.assertEqual(save_dialog.call_args.args[2], "input_频谱结果.zip")
                    submit.assert_not_called()
                self.assertIs(panel.last_run, last_run)
                with ZipFile(output) as archive:
                    self.assertEqual(set(archive.namelist()),
                                     {"run-manifest.json", "results.json", "spectrum-summary.csv",
                                      "data-quality.csv", "spectrum-blocks.csv", "spectra.npz", "report.md"})
                    blocks = archive.read("spectrum-blocks.csv")
                    self.assertEqual(blocks, (panel.last_run.directory / "spectrum-blocks.csv").read_bytes())
                    self.assertGreater(len(blocks.splitlines()), 1)
                original_package = output.read_bytes()
                panel.show_block_hits.setChecked(False)
                with patch("waveform_storage.gui.spectrum_panel.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                    panel.export_button.click()
                self.assertEqual(output.read_bytes(), original_package)
            finally:
                self._close_window(window)

    def test_missing_history_rate_requires_manual_input_and_uses_shared_service(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "input.h5"
            hdf_fixture(p, fs=500)
            window = self._window(d)
            try:
                window.alignment_result = read_legacy_merged_hdf5(p).result
                window.history_spectrum_button.click()
                panel = window.spectrum_panel
                self.assertEqual(window.pages.currentIndex(), 2)
                self.assertIs(panel.alignment, window.alignment_result)
                self.assertEqual(panel.sample_rate.value(), 0)
                self.assertIn("历史记录没有可用采样率", panel.input_note.text())
                panel.sample_rate.setValue(500)
                panel.run_button.click()
                self._wait(panel)
                self.assertEqual(panel.last_run.manifest["source_type"], "alignment")
                self.assertEqual(panel.last_run.manifest["sample_rate_hz_supplied"], 500)
                self.assertEqual(panel.table.rowCount(), 4)
            finally:
                self._close_window(window)

    def test_known_history_rate_auto_handoff_and_uses_shared_service(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "input.h5"
            hdf_fixture(p, fs=1000)
            window = self._window(d)
            try:
                now = datetime(2026, 10, 9, tzinfo=timezone.utc)
                window.current_history_run = CaptureRunSummary(
                    "task-1000", now, now, now, 1000, 600, "succeeded", 2, 2, 2, True)
                window.alignment_result = read_legacy_merged_hdf5(p).result
                window.current_history_segment = CaptureSegment(
                    "task-1000", 8, "080000", now, window.alignment_result.device_ids)
                window.history_spectrum_button.click()
                panel = window.spectrum_panel
                self.assertEqual(window.pages.currentIndex(), 2)
                self.assertIs(panel.alignment, window.alignment_result)
                self.assertEqual(panel.sample_rate.value(), 1000)
                self.assertEqual(panel.sample_rate.text(), "1000.000 Hz")
                self.assertIn("历史任务记录读取：1000 Hz", panel.input_note.text())
                self.assertEqual(panel.history_request_id, "task-1000")
                self.assertEqual(panel.history_capture_sequence, 8)
                panel.run_button.click()
                self._wait(panel)
                self.assertEqual(panel.last_run.manifest["source_type"], "alignment")
                self.assertEqual(panel.last_run.manifest["sample_rate_hz_supplied"], 1000)
                self.assertEqual(panel.table.rowCount(), 4)
            finally:
                self._close_window(window)

    def test_history_rate_validation_reset_and_rehandoff(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "input.h5"
            hdf_fixture(p)
            result = read_legacy_merged_hdf5(p).result
            window = self._window(d)
            try:
                panel = window.spectrum_panel
                panel.use_alignment(result, sample_rate_hz=1000)
                self.assertEqual(panel.sample_rate.value(), 1000)
                panel.reset_history_context()
                self.assertIsNone(panel.alignment)
                self.assertEqual(panel.sample_rate.value(), 0)
                self.assertNotIn("1000", panel.input_note.text())
                panel.use_alignment(result, sample_rate_hz=500)
                self.assertEqual(panel.sample_rate.value(), 500)
                self.assertEqual(panel.sample_rate.text(), "500.000 Hz")
                self.assertIn("历史任务记录读取：500 Hz", panel.input_note.text())
                for invalid_rate in (None, "1000", "invalid", True, False, 0, -500,
                                     float("nan"), float("inf"), float("-inf")):
                    with self.subTest(rate=invalid_rate):
                        panel.use_alignment(result, sample_rate_hz=1000)
                        panel.use_alignment(result, sample_rate_hz=invalid_rate)
                        self.assertEqual(panel.sample_rate.value(), 0)
                        self.assertEqual(panel.sample_rate.text(), "未提供")
                        self.assertIn("请填写该次采集的实际采样率", panel.input_note.text())
                panel.use_alignment(result, sample_rate_hz=500)
                panel._clear_alignment()
                self.assertEqual(panel.sample_rate.value(), 0)
                self.assertEqual(panel.sample_rate.specialValueText(), "读取文件记录")
                panel.use_alignment(result, sample_rate_hz=1000)
                panel.source_combo.setCurrentIndex(panel.source_combo.findData("archiver"))
                self.assertEqual(panel.sample_rate.value(), 0)
                self.assertFalse(panel.sample_rate.isEnabled())
                self.assertIsNone(panel.alignment)
                self.assertIn("真实时间戳", panel.input_note.text())
            finally:
                self._close_window(window)

    def _result_window(self, directory):
        window = self._window(directory)
        channels = []
        for name, status, display_status, peak, snr, hits, total in [
            ("BPM1_X", "observation", "probable_peak", 9.2, 2, 2, 3),
            ("BPM1_Y", "stable_candidate", "clear_peak", 47.6, 12, 10, 12),
            ("BPM2_X", "reject", "no_significant_peak", 10, 9, 0, 3),
            ("BPM2_Y", "invalid", "unanalysable", None, None, 0, 0),
            ("BPM3_X", "observation", "watch", 14.4, 3, 1, 3),
        ]:
            channels.append(ChannelResult(name, "fixture", classification=status,
                            display_status=display_status,
                            peak_hz=peak, peak_to_background_db=snr, block_hits=hits,
                            block_count=total, block_ratio=hits / total if total else None,
                            frequency=np.linspace(0, 70, 701) if peak is not None else np.array([]),
                            psd=np.ones(701) if peak is not None else np.array([])))
        panel = window.spectrum_panel
        panel.last_run = AnalysisRun("fixture", Path(directory), channels,
                                     {"config": asdict(AnalysisConfig())})
        panel._show_result()
        return window

    def test_block_hits_toggle_only_changes_visibility_and_preserves_results(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                panel = window.spectrum_panel
                panel.table.selectRow(1)
                selected = panel.table.item(panel.table.currentRow(), 0).data(Qt.UserRole)
                run = panel.last_run
                before = [asdict(channel) for channel in run.channels]
                with patch("waveform_storage.gui.spectrum_panel.AnalysisJobs") as analysis_jobs:
                    panel.jobs = Mock()
                    for busy in (False, True):
                        panel.job_id = "running" if busy else None
                        for checked in (True, False, True):
                            panel.show_block_hits.setChecked(checked)
                            self.assertEqual(panel.table.isColumnHidden(4), not checked)
                            self.assertIs(panel.last_run, run)
                            self.assertEqual(panel.table.item(panel.table.currentRow(), 0).data(Qt.UserRole), selected)
                    analysis_jobs.assert_not_called()
                    panel.jobs.submit.assert_not_called()
                for original, channel in zip(before, run.channels):
                    for key, value in asdict(channel).items():
                        if isinstance(value, np.ndarray):
                            np.testing.assert_equal(value, original[key])
                        else:
                            self.assertEqual(value, original[key])
                for checked in (False, True):
                    panel.show_block_hits.setChecked(checked)
                    panel._show_result()
                    self.assertEqual(panel.table.columnCount(), 5)
                    self.assertEqual(panel.table.isColumnHidden(4), not checked)
                    self.assertEqual(panel.table.item(panel.table.currentRow(), 0).data(Qt.UserRole), selected)
            finally:
                self._close_window(window)

    def test_filters_combine_and_empty_results_clear_the_previous_plot(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                p = window.spectrum_panel
                self.assertIn("4/5 个通道可分析", p.status_label.text())
                self.assertIn("1 个谱峰明显", p.status_label.text())
                self.assertIn("1 个谱峰较明显", p.status_label.text())
                p.plane_filter.setCurrentIndex(p.plane_filter.findData("X"))
                self.assertEqual(p.table.rowCount(), 3)
                p.state_filter.setCurrentIndex(p.state_filter.findData("no_significant_peak"))
                self.assertEqual(p.table.rowCount(), 1)
                self.assertEqual(p.table.item(0, 0).text(), "BPM2_X")
                self.assertEqual(p.figure.axes[0].get_title(loc="left"), "BPM2_X")
                p.plane_filter.setCurrentIndex(p.plane_filter.findData("Y"))
                self.assertEqual(p.table.rowCount(), 0)
                self.assertFalse(p.figure.axes)
                self.assertFalse(p.plot_export_button.isEnabled())
                self.assertIn("0/5", p.result_count.text())
                p.display_mode.setCurrentIndex(p.display_mode.findData("log"))
                self.assertFalse(p.figure.axes)
                p.state_filter.setCurrentIndex(p.state_filter.findData("unanalysable"))
                self.assertEqual(p.table.item(0, 0).text(), "BPM2_Y")
                self.assertFalse(p.plot_export_button.isEnabled())
                p.state_filter.setCurrentIndex(0)
                p.plane_filter.setCurrentIndex(0)
                self.assertEqual(p.table.rowCount(), 5)
                self.assertEqual(p.state_filter.count(), len(DISPLAY_STATUS_LABELS) + 1)
                for status, label in DISPLAY_STATUS_LABELS.items():
                    p.state_filter.setCurrentIndex(p.state_filter.findData(status))
                    self.assertEqual(p.table.rowCount(), 1)
                    self.assertEqual(p.table.item(0, 1).text(), label)
                p.plane_filter.setCurrentIndex(p.plane_filter.findData("X"))
                p.state_filter.setCurrentIndex(p.state_filter.findData("watch"))
                self.assertEqual(p.table.item(0, 0).text(), "BPM3_X")
            finally:
                self._close_window(window)

    def test_numeric_sorting_preserves_channel_identity_and_peak_annotation(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                p = window.spectrum_panel
                p.show_block_hits.setChecked(True)
                self.assertFalse(p.table.isColumnHidden(4))
                for column, expected in [(2, [9.2, 10, 14.4, 47.6]),
                                         (3, [2, 3, 9, 12]), (4, [0, 0, 1, 2, 10])]:
                    p.table.sortItems(column, Qt.AscendingOrder)
                    keys = [p.table.item(row, column).sort_key for row in range(p.table.rowCount())]
                    values = [key[1][0] for key in keys if key[0]]
                    self.assertEqual(values, expected)
                    p.table.sortItems(column, Qt.DescendingOrder)
                    keys = [p.table.item(row, column).sort_key for row in range(p.table.rowCount())]
                    self.assertEqual([key[1][0] for key in keys if key[0]], expected[::-1])
                p.table.selectRow(0);self.app.processEvents()
                self.assertEqual(p.table.item(0, 0).text(), "BPM1_Y")
                axis = p.figure.axes[0]
                self.assertEqual(axis.get_title(loc="left"), "BPM1_Y")
                self.assertTrue(any("47.6" in text.get_text() for text in axis.texts))
                channel_index = p.table.item(0, 0).data(Qt.UserRole)
                original_psd = np.ones(701)
                original_psd[0] = 0
                original_psd[1] = -1
                original_psd[2] = np.nextafter(0.0, 1.0)
                original_psd[476] = 2
                p.last_run.channels[channel_index].psd = original_psd.copy()
                self.assertIsNone(p.job_id)
                self.assertIsNone(p.jobs)
                p.display_mode.setCurrentIndex(p.display_mode.findData("log"))
                self.assertEqual(p.figure.axes[0].get_yscale(), "log")
                self.assertEqual(p.table.item(p.table.currentRow(), 0).data(Qt.UserRole), channel_index)
                self.assertEqual(p.figure.axes[0].get_title(loc="left"), "BPM1_Y")
                self.assertTrue(any("47.6" in text.get_text() for text in p.figure.axes[0].texts))
                self.assertEqual(p.figure.axes[0].get_xlabel(), "Frequency (Hz)")
                self.assertEqual(p.figure.axes[0].get_xlim(), (30.0, 60.0))
                self.assertTrue(p.figure.axes[0].patches)
                np.testing.assert_equal(p.last_run.channels[channel_index].psd, original_psd)
                output = Path(d) / "annotated.png"
                with patch("waveform_storage.gui.spectrum_panel.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                    p.plot_export_button.click()
                self.assertGreater(output.stat().st_size, 0)
                self.assertEqual(p.figure.axes[0].get_yscale(), "log")
                p.display_mode.setCurrentIndex(p.display_mode.findData("linear"))
                self.assertEqual(p.figure.axes[0].get_yscale(), "linear")
                np.testing.assert_equal(p.figure.axes[0].lines[0].get_ydata(), original_psd)
                self.assertIsNone(p.job_id)
                p.state_filter.setCurrentIndex(p.state_filter.findData("clear_peak"))
                self.assertEqual(p.table.item(0, 0).text(), "BPM1_Y")
                self.assertEqual(p.figure.axes[0].get_title(loc="left"), "BPM1_Y")
            finally:
                self._close_window(window)

    def test_plot_export_default_name_tracks_channel_and_display_mode_without_analysis(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                panel = window.spectrum_panel
                panel.table.selectRow(0)
                channel = panel.last_run.channels[0]
                original_channel = channel.channel

                with patch("waveform_storage.gui.spectrum_panel.AnalysisJobs") as analysis_jobs, \
                     patch("waveform_storage.gui.spectrum_panel.QFileDialog.getSaveFileName",
                           return_value=("", "")) as save_dialog:
                    self.assertEqual(panel._default_plot_filename(), "BPM1_X_原始.png")
                    panel.plot_export_button.click()
                    self.assertEqual(save_dialog.call_args.args[2], "BPM1_X_原始.png")

                    channel.channel = "AC_LA_INJ1_BI_SBPM01_X"
                    panel._refresh_result_table()
                    self.assertEqual(panel._default_plot_filename(),
                                     "AC_LA_INJ1_BI_SBPM01_X_原始.png")

                    panel.display_mode.setCurrentIndex(panel.display_mode.findData("log"))
                    panel.plot_export_button.click()
                    self.assertEqual(save_dialog.call_args.args[2],
                                     "AC_LA_INJ1_BI_SBPM01_X_Log.png")

                    channel.channel = "AC_LA_INJ1_BI_SBPM01_Y"
                    panel._refresh_result_table()
                    self.assertEqual(panel._default_plot_filename(),
                                     "AC_LA_INJ1_BI_SBPM01_Y_Log.png")
                    panel.display_mode.setCurrentIndex(panel.display_mode.findData("linear"))
                    panel.plot_export_button.click()
                    self.assertEqual(save_dialog.call_args.args[2],
                                     "AC_LA_INJ1_BI_SBPM01_Y_原始.png")

                    channel.channel = 'A/B\\C:D*E?F"G<H>I|J'
                    panel._refresh_result_table()
                    panel.plot_export_button.click()
                    self.assertEqual(save_dialog.call_args.args[2],
                                     "A_B_C_D_E_F_G_H_I_J_原始.png")
                    self.assertEqual(channel.channel, 'A/B\\C:D*E?F"G<H>I|J')
                    self.assertIsNone(panel.job_id)
                    self.assertIsNone(panel.jobs)
                    analysis_jobs.assert_not_called()

                panel.table.clearSelection()
                panel.table.setCurrentCell(-1, -1)
                self.assertEqual(panel._default_plot_filename(), "spectrum.png")
                with patch("waveform_storage.gui.spectrum_panel.QFileDialog.getSaveFileName",
                           return_value=("", "")) as save_dialog:
                    panel._export_plot()
                self.assertEqual(save_dialog.call_args.args[2], "spectrum.png")
                self.assertEqual(original_channel, "BPM1_X")
            finally:
                self._close_window(window)

    def test_result_package_filename_adapts_to_input_and_sanitizes_names(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                panel = window.spectrum_panel
                hdf_file = Path(d) / "abc.h5"
                hdf_file.touch()
                panel.path_edit.setText(str(hdf_file))
                self.assertEqual(panel._default_results_filename(), "abc_频谱结果.zip")

                hdf_dir = Path(d) / "run001"
                hdf_dir.mkdir()
                panel.path_edit.setText(str(hdf_dir) + "/")
                self.assertEqual(panel._default_results_filename(), "run001_频谱结果.zip")

                archiver_file = Path(d) / "export.csv"
                archiver_file.touch()
                panel.path_edit.setText(str(archiver_file))
                self.assertEqual(panel._default_results_filename(), "export_频谱结果.zip")

                archiver_dir = Path(d) / "archiver_batch"
                archiver_dir.mkdir()
                panel.path_edit.setText(str(archiver_dir))
                self.assertEqual(panel._default_results_filename(), "archiver_batch_频谱结果.zip")

                special = Path(d) / 'ex:port?name.csv'
                special.touch()
                panel.path_edit.setText(str(special))
                self.assertEqual(panel._default_results_filename(), "ex_port_name_频谱结果.zip")
                self.assertEqual(panel._safe_export_stem(".."), "")
                unsafe = 'a/b\\c:d*e?f"g<h>i|j\x00\x85 '
                self.assertEqual(panel._safe_export_stem(unsafe), "a_b_c_d_e_f_g_h_i_j_")

                panel.path_edit.clear()
                self.assertEqual(panel._default_results_filename(), "spectrum-results.zip")
                panel.path_edit.setText(str(Path(d) / "missing.h5"))
                self.assertEqual(panel._default_results_filename(), "spectrum-results.zip")
            finally:
                self._close_window(window)

    def test_history_result_package_name_uses_real_capture_sequence_and_resets(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                panel = window.spectrum_panel
                # The history naming metadata is independent of the GUI's displayed segment position.
                from waveform_storage.processing.alignment import DeviceSeries, align_devices
                history_alignment = align_devices([DeviceSeries(
                    "BPM1", np.arange(3, dtype=np.uint64), np.arange(3), np.arange(3))])
                panel.use_alignment(history_alignment, sample_rate_hz=1000,
                                    history_request_id="task-123", capture_sequence=8)
                self.assertEqual(panel._default_results_filename(), "task-123_S00008_频谱结果.zip")
                panel.use_alignment(history_alignment, history_request_id="task-456", capture_sequence=23)
                self.assertEqual(panel._default_results_filename(), "task-456_S00023_频谱结果.zip")
                panel.reset_history_context()
                panel.last_run = AnalysisRun("fixture", Path(d), [], {})
                self.assertIsNone(panel.history_request_id)
                self.assertIsNone(panel.history_capture_sequence)
                self.assertEqual(panel._default_results_filename(), "spectrum-results.zip")
            finally:
                self._close_window(window)

    def test_history_reset_without_alignment_clears_metadata_but_keeps_file_results(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                panel = window.spectrum_panel
                run = panel.last_run
                panel.history_request_id = "stale-task"
                panel.history_capture_sequence = 8
                panel.reset_history_context()
                self.assertIs(panel.last_run, run)
                self.assertIsNone(panel.history_request_id)
                self.assertIsNone(panel.history_capture_sequence)
            finally:
                self._close_window(window)

    def test_peak_display_uses_resolution_and_preserves_raw_sort_and_plot_values(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                panel = window.spectrum_panel
                channel = panel.last_run.channels[1]
                channel.peak_hz = 47.59637
                channel.resolution_hz = .01
                channel.details = {"actual_welch_seconds": 19.544}
                panel.last_run.manifest["config"]["welch_seconds"] = 20
                panel._refresh_result_table()
                row = next(row for row in range(panel.table.rowCount())
                           if panel.table.item(row, 0).text() == "BPM1_Y")
                self.assertEqual(panel.table.item(row, 2).text(), "47.60")
                self.assertEqual(panel.table.item(row, 2).sort_key, (1, (47.59637,)))
                panel.table.selectRow(row)
                self.app.processEvents()
                self.assertTrue(any("47.60" in text.get_text() for text in panel.figure.axes[0].texts))
                peak_line = next(line for line in panel.figure.axes[0].lines
                                 if len(line.get_xdata()) == 2 and
                                 np.all(np.asarray(line.get_xdata()) == 47.59637))
                np.testing.assert_array_equal(peak_line.get_xdata(), [47.59637, 47.59637])
                self.assertEqual(channel.peak_hz, 47.59637)
                self.assertIn("设置 Welch 窗口：20 s；实际 Welch 窗口：19.544 s；频率分辨率：0.01 Hz",
                              panel.welch_diagnostic.text())
                self.assertNotIn("Welch 窗口", panel.detail_label.text())
                self.assertTrue(panel.advanced_frame.isHidden())
                submit = Mock()
                panel.jobs = Mock()
                panel.jobs.submit = submit
                other_channel = panel.last_run.channels[0]
                other_channel.peak_hz = 47.550000000000004
                other_channel.resolution_hz = .2
                other_channel.details = {}
                panel._refresh_result_table()
                panel.table.selectRow(next(row for row in range(panel.table.rowCount())
                                           if panel.table.item(row, 0).text() == other_channel.channel))
                self.app.processEvents()
                other_row = next(row for row in range(panel.table.rowCount())
                                 if panel.table.item(row, 0).text() == other_channel.channel)
                self.assertEqual(panel.table.item(other_row, 2).text(), "47.6")
                panel.table.sortItems(2, Qt.AscendingOrder)
                raw_sorted = [panel.table.item(index, 0).text()
                              for index in range(panel.table.rowCount())
                              if panel.table.item(index, 0).text() in
                              {channel.channel, other_channel.channel}]
                self.assertEqual(raw_sorted, [other_channel.channel, channel.channel])
                self.assertIn("实际 Welch 窗口：— s；频率分辨率：0.2 Hz",
                              panel.welch_diagnostic.text())
                panel.advanced_button.click()
                panel.show_block_hits.click()
                panel.advanced_button.click()
                submit.assert_not_called()
                self.assertEqual(channel.peak_hz, 47.59637)
            finally:
                self._close_window(window)

    def test_resolution_aware_peak_table_and_annotation_examples(self):
        from waveform_storage.analysis.models import format_peak_frequency
        examples = [(.2, 47.6, "47.6"), (.05, 47.55, "47.55"),
                    (.0511666, 49.84, "49.84"), (.01, 49.8, "49.80")]
        for resolution, peak, expected in examples:
            self.assertEqual(format_peak_frequency(peak, resolution), expected)


if __name__ == "__main__":
    unittest.main()
