"""Partial history traverses the real catalog, protocol, backend, and GUI."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/operator_gui'))
from candidate_backend import CandidateOperatorBackend
from PyQt5.QtCore import QDate
from PyQt5.QtWidgets import QApplication, QDialog
from waveform_storage.acquisition.server import (
    CandidateAcquisitionServer, JsonTaskStore, PersistentTaskExecutor, RegistryPolicy,
)
from waveform_storage.config.registry import load_registry
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.operator_window import BpmSelectionDialog, OperatorMainWindow
from waveform_storage.io.hdf5_io import write_single_device_hdf5
from waveform_storage.io.txt_parser import ParsedWaveform
from waveform_storage.metadata.memory_repository import InMemoryMetadataRepository
from waveform_storage.metadata.models import CaptureTaskMetadata, WaveformFileMetadata
from waveform_storage.metadata.service import CaptureMetadataService
import h5py
import numpy as np

FIRST = 'AC_LA_INJ1_BI_SBPM01'
SECOND = 'AC_LA_INJ1_BI_SBPM02'


class PartialHistoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, window):
        deadline = time.monotonic() + 10
        while window._active_workers and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(window._active_workers)

    def test_partial_catalog_protocol_gui_alignment_raw_and_export(self):
        registry = load_registry(ROOT / 'config/devices.operator.json')
        capture_registry = load_registry(ROOT / 'config/devices.probe47.json')
        request_id = str(uuid4())
        endpoint = 'inproc://partial-history-' + str(uuid4())
        now = datetime(2026, 10, 8, 8, 0, 0, tzinfo=timezone.utc)
        repository = InMemoryMetadataRepository()
        repository.upsert_task(CaptureTaskMetadata(
            request_id, 1000, 600, ('BI',), (FIRST, SECOND), 'fixture-registry', 1,
            status='failed', created_at=now))
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {
            'WAVEFORM_CANDIDATE_ZMQ_ENDPOINT': endpoint, 'WAVEFORM_CANDIDATE_LOCAL_FILES': '1',
        }):
            root = Path(temporary)
            for sequence in (0, 3):
                path = root / f'RAW-16000{sequence}-{FIRST}-S{sequence:05d}.h5'
                write_single_device_hdf5(path, FIRST, ParsedWaveform(
                    np.array([1, 2, 3], dtype=np.uint64),
                    np.array([10, 11, 12], dtype=np.int64),
                    np.array([20, 21, 22], dtype=np.int64)),
                    metadata={'request_id': request_id, 'capture_sequence': sequence,
                              'captured_at': now.isoformat()})
                repository.upsert_waveform_file(WaveformFileMetadata(
                    request_id, FIRST, 'PV:FIXTURE', now + timedelta(seconds=sequence),
                    '2026-10-08', f'16000{sequence}', 'Asia/Shanghai', 'fixture', str(path),
                    3, (1, 3), (2, 3), path.stat().st_size, capture_sequence=sequence))
            metadata = CaptureMetadataService(repository, capture_registry, ROOT / 'config/devices.probe47.json')
            capture_calls = []
            def runner(request):
                capture_calls.append(request.request_id)
                raise AssertionError('history integration must not start capture')
            tasks = PersistentTaskExecutor(JsonTaskStore(root / 'state'), runner)
            server = CandidateAcquisitionServer(endpoint, RegistryPolicy(capture_registry), tasks,
                                                 metadata=metadata, frequency_reader=lambda: 1000)
            stop = threading.Event()
            thread = threading.Thread(target=server.serve_forever, args=(stop,), daemon=True)
            thread.start()
            backend = CandidateOperatorBackend(AppSettings.from_env(project_root=ROOT))
            window = OperatorMainWindow(registry, backend=backend, capture_registry=capture_registry,
                                        journal_path=root / 'journal.jsonl')
            errors = []
            window._present_error = lambda error, **_kwargs: errors.append(error)
            try:
                self.app.processEvents()
                self.wait(window)
                window.history_date.setDate(QDate(2026, 10, 8))
                window.retrieve_button.click()
                self.wait(window)
                self.assertEqual(window.history_time.count(), 1)
                self.assertEqual(window.current_history_run.requested_device_count, 2)
                self.assertEqual([s.capture_sequence for s in window.history_segments], [0, 3])
                window.next_segment_button.click()
                self.assertEqual(window.current_history_segment.capture_sequence, 3)
                self.assertIn('1/2 BPM', window.segment_label.text())
                def choose(dialog):
                    self.assertEqual(dialog.list_widget.count(), 1)
                    self.assertEqual(dialog.selected_ids(), (FIRST,))
                    return QDialog.Accepted
                with patch.object(BpmSelectionDialog, 'exec_', choose):
                    window.select_bpm_button.click()
                self.assertEqual(window.history_selection_key, ('segment', request_id, 3))
                window.align_button.click()
                self.wait(window)
                self.assertEqual(window.align_button.text(), '继续严格对齐')
                dialog = window._raw_step_warning_dialog
                self.assertIsNotNone(dialog)
                proceed = next(button for button in dialog.buttons()
                               if button.text() == '继续严格对齐')
                proceed.click()
                self.wait(window)
                self.assertEqual(window.alignment_result.device_ids, (FIRST,))
                self.assertEqual(window.alignment_result.bunch_ids.size, 3)
                with patch('waveform_storage.gui.operator_window.QFileDialog.getExistingDirectory',
                           return_value=str(root / 'out')), patch(
                           'waveform_storage.gui.operator_window.QMessageBox.information'):
                    window.raw_button.click()
                    self.wait(window)
                    window.export_button.click()
                    self.wait(window)
                raw = list((root / 'out' / '20261008' / request_id / 'S00003').glob('*.h5'))
                self.assertEqual(len(raw), 1)
                exports = list((root / 'out').glob('*_BPMdata_v2.h5'))
                self.assertEqual(len(exports), 1)
                with h5py.File(exports[0]) as h5:
                    provenance = json.loads(h5.attrs['metadata_json'])
                self.assertEqual((provenance['request_id'], provenance['capture_sequence']), (request_id, 3))
                self.assertEqual(errors, [])
                self.assertEqual(capture_calls, [])
            finally:
                window.close()
                stop.set()
                thread.join(timeout=3)
                server.close()
                tasks.close()
