"""Read-only capability verification against the running desktop endpoint."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from waveform_storage.transport.zmq_transport import ZmqTransport


def verify(endpoint, selected_date, destination):
    transport = ZmqTransport(endpoint, timeout_ms=15000)
    health = transport.health(str(uuid4()))
    if not health.accepted:
        raise RuntimeError('health failed: ' + health.message)
    runs = transport.list_capture_runs(str(uuid4()), selected_date)
    if not runs.accepted:
        raise RuntimeError('list_capture_runs failed: ' + runs.message)
    segments_count = files_count = 0
    for run in runs.payload['runs']:
        segments = transport.list_capture_segments(str(uuid4()), run['request_id'])
        if not segments.accepted:
            raise RuntimeError('list_capture_segments failed: ' + segments.message)
        segments_count += len(segments.payload['segments'])
        for segment in segments.payload['segments']:
            files = transport.find_waveform_files_by_segment(
                str(uuid4()), run['request_id'], segment['capture_sequence'], segment['device_ids'])
            if not files.accepted:
                raise RuntimeError('exact segment lookup failed: ' + files.message)
            items = files.payload['files']
            if len(items) != len(segment['device_ids']) or any(
                item['request_id'] != run['request_id'] or
                item['capture_sequence'] != segment['capture_sequence'] for item in items
            ):
                raise RuntimeError('exact file identity mismatch')
            files_count += len(items)
    legacy = transport.list_capture_times(str(uuid4()), selected_date)
    if not legacy.accepted:
        raise RuntimeError('legacy list_capture_times failed')
    if not runs.payload['runs'] or not segments_count or not files_count:
        raise RuntimeError('smoke requires a date with real tasks and waveform files')
    report = {'verified_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
              'endpoint': endpoint, 'date': selected_date, 'health': True,
              'list_capture_runs': True, 'list_capture_segments': True,
              'find_waveform_files_by_segment': True, 'legacy_list_capture_times': True,
              'run_count': len(runs.payload['runs']), 'segment_count': segments_count,
              'exact_file_count': files_count, 'status': 'passed'}
    Path(destination).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='tcp://127.0.0.1:5001')
    parser.add_argument('--date', required=True)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    verify(args.endpoint, args.date, args.report)
