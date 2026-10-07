"""Real Qt third-page workflow including imports, history handoff and export."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from dataclasses import asdict
from unittest.mock import patch
from zipfile import ZipFile

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
import numpy as np

from waveform_storage.config.registry import load_registry
from waveform_storage.gui.operator_contract import OperatorMode
from waveform_storage.gui.operator_window import OperatorMainWindow
from waveform_storage.io.hdf5_io import read_legacy_merged_hdf5
from waveform_storage.analysis.models import AnalysisConfig, AnalysisRun, ChannelResult
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
                self._wait(panel)
                self.assertEqual(panel.table.rowCount(), 4)
                self.assertAlmostEqual(panel.last_run.channels[0].peak_hz, 47.6)
                panel.table.selectRow(0)
                self.app.processEvents()
                self.assertTrue(panel.figure.axes[0].lines)
                output = Path(d) / "saved.zip"
                with patch("waveform_storage.gui.spectrum_panel.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                    panel.export_button.click()
                with ZipFile(output) as archive:
                    self.assertIn("run-manifest.json", archive.namelist())
            finally:
                window.close()

    def test_history_handoff_requires_historical_rate_and_uses_shared_service(self):
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
                panel.sample_rate.setValue(500)
                panel.run_button.click()
                self._wait(panel)
                self.assertEqual(panel.last_run.manifest["source_type"], "alignment")
                self.assertEqual(panel.last_run.manifest["sample_rate_hz_supplied"], 500)
                self.assertEqual(panel.table.rowCount(), 4)
            finally:
                window.close()

    def _result_window(self, directory):
        window = self._window(directory)
        channels = []
        for name, status, peak, snr, hits, total in [
            ("BPM1_X", "observation", 9.2, 2, 2, 3),
            ("BPM1_Y", "stable_candidate", 47.6, 12, 10, 12),
            ("BPM2_X", "reject", 10, 9, 0, 3),
            ("BPM2_Y", "invalid", None, None, 0, 0),
        ]:
            channels.append(ChannelResult(name, "fixture", classification=status,
                            peak_hz=peak, peak_to_background_db=snr, block_hits=hits,
                            block_count=total, block_ratio=hits / total if total else None,
                            frequency=np.linspace(0, 70, 701) if peak is not None else np.array([]),
                            psd=np.ones(701) if peak is not None else np.array([])))
        panel = window.spectrum_panel
        panel.last_run = AnalysisRun("fixture", Path(directory), channels,
                                     {"config": asdict(AnalysisConfig())})
        panel._show_result()
        return window

    def test_filters_combine_and_empty_results_clear_the_previous_plot(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                p = window.spectrum_panel
                p.plane_filter.setCurrentIndex(p.plane_filter.findData("X"))
                self.assertEqual(p.table.rowCount(), 2)
                p.state_filter.setCurrentIndex(p.state_filter.findData("reject"))
                self.assertEqual(p.table.rowCount(), 1)
                self.assertEqual(p.table.item(0, 0).text(), "BPM2_X")
                self.assertEqual(p.figure.axes[0].get_title(loc="left"), "BPM2_X")
                p.plane_filter.setCurrentIndex(p.plane_filter.findData("Y"))
                self.assertEqual(p.table.rowCount(), 0)
                self.assertFalse(p.figure.axes)
                self.assertFalse(p.plot_export_button.isEnabled())
                self.assertIn("0/4", p.result_count.text())
                p.state_filter.setCurrentIndex(p.state_filter.findData("invalid"))
                self.assertEqual(p.table.item(0, 0).text(), "BPM2_Y")
                self.assertFalse(p.plot_export_button.isEnabled())
                p.state_filter.setCurrentIndex(0)
                p.plane_filter.setCurrentIndex(0)
                self.assertEqual(p.table.rowCount(), 4)
            finally:
                window.close()

    def test_numeric_sorting_preserves_channel_identity_and_peak_annotation(self):
        with TemporaryDirectory() as d:
            window = self._result_window(d)
            try:
                p = window.spectrum_panel
                for column, expected in [(2, [9.2, 10, 47.6]), (3, [2, 9, 12]), (4, [0, 0, 2, 10])]:
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
                output = Path(d) / "annotated.png"
                with patch("waveform_storage.gui.spectrum_panel.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                    p.plot_export_button.click()
                self.assertGreater(output.stat().st_size, 0)
                p.state_filter.setCurrentIndex(p.state_filter.findData("stable_candidate"))
                self.assertEqual(p.table.item(0, 0).text(), "BPM1_Y")
                self.assertEqual(p.figure.axes[0].get_title(loc="left"), "BPM1_Y")
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
