"""Start the real desktop entry and wait for packaged GUI acceptance evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = args.report.resolve()
    if report.exists():
        raise ValueError('smoke report exists; choose a new evidence path')
    desktop = Path.home() / '桌面/波形存储.desktop'
    expected = 'Exec=' + str(ROOT / 'tools/launch_operator_desktop.sh')
    if expected not in desktop.read_text().splitlines():
        raise ValueError('real desktop entry does not point to the formal launcher')
    environment = os.environ.copy()
    environment.update(WAVEFORM_HISTORY_SMOKE_REPORT=str(report),
                       WAVEFORM_HISTORY_SMOKE_DATE=args.date,
                       WAVEFORM_HISTORY_SMOKE_DESKTOP=str(desktop))
    # GLib may preserve the launching app's GIO_LAUNCHED_DESKTOP_FILE. Record
    # our actual command separately, and verify the binary's real parent launcher.
    command = ['gio', 'launch', str(desktop)]
    subprocess.run(command, env=environment, check=True)
    deadline = monotonic() + 300
    while monotonic() < deadline:
        if report.exists():
            try:
                evidence = json.loads(report.read_text())
            except json.JSONDecodeError:
                sleep(0.2)
                continue
            image = report.with_suffix('.png')
            if evidence.get('status') == 'passed' and (not image.exists() or image.stat().st_size == 0):
                sleep(0.2)
                continue
            evidence['desktop_invocation'] = command
            evidence['desktop_sha256'] = hashlib.sha256(desktop.read_bytes()).hexdigest()
            staged = report.with_suffix('.tmp')
            staged.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
            os.replace(staged, report)
            if evidence['status'] != 'passed':
                raise RuntimeError(evidence.get('error', 'GUI acceptance failed'))
            print(str(report))
            return
        sleep(1)
    raise RuntimeError('desktop GUI acceptance report was not produced')


if __name__ == '__main__':
    main()
