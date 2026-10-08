"""Opt-in acceptance run through the real operator widgets and backend."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
from time import monotonic
from datetime import datetime
from zoneinfo import ZoneInfo

import h5py
from PyQt5.QtCore import QDate, QTimer, Qt
from PyQt5.QtWidgets import QDialog, QFileDialog, QMessageBox

from waveform_storage.gui.operator_window import BpmSelectionDialog


def install_history_smoke(window, app, report_path: Path, selected_date: str, project_root: Path):
    """Run query/read/export only; never invoke capture or modify remote data."""
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = report_path.parent / ('data-' + str(uuid4()))
    data_dir.mkdir()
    executable = Path('/proc/self/exe').resolve()
    current = (project_root / 'outputs/operator-local/current/waveform-operator').resolve()
    evidence = {
        'started_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'pid': os.getpid(), 'frozen': bool(getattr(sys, 'frozen', False)),
        'executable': str(executable), 'current_binary': str(current),
        'binary_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
        'desktop_file': os.environ.get('WAVEFORM_HISTORY_SMOKE_DESKTOP'),
        'inherited_desktop_file': os.environ.get('GIO_LAUNCHED_DESKTOP_FILE'),
        'launcher_pid': os.getppid(),
        'launcher_command': Path(f'/proc/{os.getppid()}/cmdline').read_bytes().decode().split('\0')[:-1],
        'endpoint': window.backend.candidate_endpoint, 'date': selected_date,
        'file_queries': [], 'steps': {}, 'capture_requests': 0, 'data_directory': str(data_dir),
    }
    timer = QTimer(window)
    timer.setInterval(200)
    state = {'stage': 'start', 'deadline': monotonic() + 240}
    original_pairs = window.backend._candidate_segment_file_pairs

    def pairs(request_id, sequence, devices):
        result = original_pairs(request_id, sequence, devices)
        evidence['file_queries'].append({'request_id': request_id, 'capture_sequence': sequence,
                                         'device_ids': list(devices), 'file_count': len(result)})
        return result

    window.backend._candidate_segment_file_pairs = pairs

    def forbidden_capture(*_args, **_kwargs):
        raise RuntimeError('capture is forbidden during history acceptance')

    window.backend.start_capture = forbidden_capture
    window.start_button.setEnabled(False)

    def finish(error=None):
        timer.stop()
        evidence['status'] = 'failed' if error else 'passed'
        if error:
            evidence['error'] = str(error)
        evidence['finished_at'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        window.grab().save(str(report_path.with_suffix('.png')))
        window.close()
        app.exit(1 if error else 0)

    window._present_error = lambda error, **_kwargs: finish(getattr(error, 'summary', error))
    QFileDialog.getExistingDirectory = lambda *_args, **_kwargs: str(data_dir)
    QMessageBox.information = lambda *_args, **_kwargs: QMessageBox.Ok

    def select_one(dialog):
        actual = {dialog.list_widget.item(i).data(Qt.UserRole)
                  for i in range(dialog.list_widget.count())}
        # Selection dialog order comes from the approved registry, not capture-area selection.
        expected = set(window.current_history_segment.device_ids) & {
            device.id for device in window.capture_registry.devices}
        if actual != expected or not actual:
            raise RuntimeError('BPM dialog does not match available segment files')
        for i in range(dialog.list_widget.count()):
            dialog.list_widget.item(i).setCheckState(Qt.Checked if i == 0 else Qt.Unchecked)
        evidence['available_bpm_count'] = len(actual)
        return QDialog.Accepted

    BpmSelectionDialog.exec_ = select_one

    def step():
        try:
            if monotonic() > state['deadline']:
                raise RuntimeError('history acceptance timed out at ' + state['stage'])
            if window._active_workers or window._busy:
                return
            stage = state['stage']
            if stage == 'start':
                if not evidence['frozen'] or executable != current:
                    raise RuntimeError('acceptance must run the current packaged executable')
                desktop = Path(evidence['desktop_file'] or '/missing-desktop')
                if desktop.resolve() != (Path.home() / '桌面/波形存储.desktop').resolve():
                    raise RuntimeError('acceptance must start from the real desktop entry')
                launcher = str(project_root / 'tools/launch_operator_desktop.sh')
                if launcher not in evidence['launcher_command']:
                    raise RuntimeError('packaged process was not launched by the formal desktop launcher')
                expected_exec = 'Exec=' + launcher
                if expected_exec not in desktop.read_text().splitlines():
                    raise RuntimeError('desktop entry bypasses the formal launcher')
                window._show_page(1)
                window.history_date.setDate(QDate.fromString(selected_date, 'yyyy-MM-dd'))
                state['stage'] = 'runs'
                window.retrieve_button.click()
            elif stage == 'runs':
                if not window.history_runs or not window.current_history_segment:
                    raise RuntimeError('no real tasks/segments loaded')
                evidence['task_count'] = len(window.history_runs)
                preferred = next((i for i, run in enumerate(window.history_runs)
                                  if run.frequency_hz == 1000 and run.duration_seconds == 600), 0)
                window.history_time.setCurrentIndex(preferred)
                state['stage'] = 'segments'
            elif stage == 'segments':
                run = window.current_history_run
                evidence['run_text'] = window.history_time.currentText()
                evidence['segment_count'] = len(window.history_segments)
                if len(window.history_segments) < 2:
                    raise RuntimeError('acceptance requires a run with at least two segments')
                window.next_segment_button.click()
                if window.segment_index.value() != 2:
                    raise RuntimeError('segment navigation failed')
                if window.history_selected_devices or window.alignment_result is not None:
                    raise RuntimeError('segment change did not reset selection/alignment')
                partial = next((i for i, segment in enumerate(window.history_segments)
                                if run.requested_device_count is not None
                                and len(segment.device_ids) < run.requested_device_count), None)
                evidence['partial_segment_present'] = partial is not None
                if partial is not None:
                    window.segment_index.setValue(partial + 1)
                evidence['steps'].update(task_loading=True, segment_switching=True)
                state['stage'] = 'selection'
            elif stage == 'selection':
                window.select_bpm_button.click()
                if not window.history_selected_devices:
                    raise RuntimeError('BPM selection failed')
                evidence['identity'] = list(window._history_key())
                evidence['selected_devices'] = list(window.history_selected_devices)
                evidence['steps']['bpm_selection'] = True
                state['stage'] = 'alignment'
                window.align_button.click()
            elif stage == 'alignment':
                if window.alignment_result is None or window.alignment_result.bunch_ids.size == 0:
                    raise RuntimeError('alignment did not produce data')
                evidence['aligned_sample_count'] = int(window.alignment_result.bunch_ids.size)
                evidence['steps']['alignment'] = True
                state['stage'] = 'raw'
                window.raw_button.click()
            elif stage == 'raw':
                segment = window.current_history_segment
                query = window.backend._segment_query(segment, window.history_selected_devices)
                directory = data_dir / query.run_id / segment.request_id / f'S{segment.capture_sequence:05d}'
                raw_files = list(directory.glob('RAW-*.h5'))
                if len(raw_files) != len(window.history_selected_devices):
                    raise RuntimeError('raw download produced no files')
                for path in raw_files:
                    with h5py.File(path) as h5:
                        if 'Data' not in h5 or 'BunchIDData' not in h5:
                            raise RuntimeError('downloaded raw HDF5 is invalid')
                evidence['raw_files'] = [str(path) for path in raw_files]
                evidence['raw_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in raw_files}
                evidence['steps']['raw_download'] = True
                state['stage'] = 'export'
                window.export_button.click()
            elif stage == 'export':
                exports = list(data_dir.glob('*_BPMdata_v2.h5'))
                if len(exports) != 1:
                    raise RuntimeError('alignment export did not produce exactly one file')
                with h5py.File(exports[0]) as h5:
                    metadata = json.loads(h5.attrs['metadata_json'])
                identity = evidence['identity']
                if (metadata['request_id'], metadata['capture_sequence']) != tuple(identity[1:]):
                    raise RuntimeError('export identity differs from the selected segment')
                if any((item['request_id'], item['capture_sequence']) != tuple(identity[1:])
                       for item in evidence['file_queries']):
                    raise RuntimeError('query identity differs from the selected segment')
                evidence['export_file'] = str(exports[0])
                evidence['export_sha256'] = hashlib.sha256(exports[0].read_bytes()).hexdigest()
                evidence['export_metadata'] = metadata
                evidence['steps']['export'] = True
                finish()
        except Exception as exc:
            finish(exc)

    timer.timeout.connect(step)
    timer.start()
    window._history_smoke_timer = timer
