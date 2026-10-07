"""Real Qt third-page workflow including imports, history handoff and export."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PyQt5.QtWidgets import QApplication

from waveform_storage.config.registry import load_registry
from waveform_storage.gui.operator_contract import OperatorMode
from waveform_storage.gui.operator_window import OperatorMainWindow
from waveform_storage.io.hdf5_io import read_legacy_merged_hdf5
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


if __name__ == "__main__":
    unittest.main()
