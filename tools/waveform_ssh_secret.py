#!/usr/bin/env python3
"""Read local SSH targets and answer SSH_ASKPASS without exposing passwords in argv."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT_SECRET_FILE = Path('/home/whloong/projects/secret.txt')
SSH_TARGET = re.compile(r'[A-Za-z0-9_.-]+@(?:\d{1,3}\.){3}\d{1,3}')


def read_secrets() -> tuple[str, str, str, str, str, str]:
    path = Path(os.environ.get('WAVEFORM_SSH_SECRET_FILE', DEFAULT_SECRET_FILE))
    lines = path.read_text(encoding='utf-8').splitlines()

    def section(label: str) -> tuple[str, str]:
        for index, line in enumerate(lines):
            if label not in line or index + 2 >= len(lines):
                continue
            target = lines[index + 1].strip()
            if not SSH_TARGET.fullmatch(target):
                continue
            password = lines[index + 2].strip()
            if password.lower().startswith('password '):
                password = password.split(None, 1)[1].strip()
            if not password:
                raise ValueError(f'{label} SSH password is unavailable')
            return target, password
        raise ValueError(f'{label} SSH target is unavailable')

    jump, jump_password = section('远程')
    waveform, waveform_password = section('波形存储')
    desktop, desktop_password = section('桌面服务器')
    return jump, jump_password, waveform, waveform_password, desktop, desktop_password


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--jump-target', action='store_true')
    mode.add_argument('--waveform-target', action='store_true')
    mode.add_argument('--desktop-target', action='store_true')
    mode.add_argument('--askpass', action='store_true')
    parser.add_argument('prompt', nargs='?')
    args = parser.parse_args()
    try:
        jump, jump_password, waveform, waveform_password, desktop, desktop_password = read_secrets()
    except (OSError, ValueError):
        print('SSH secret file is unavailable or invalid', file=sys.stderr)
        return 1
    if args.jump_target:
        print(jump)
    elif args.waveform_target:
        print(waveform)
    elif args.desktop_target:
        print(desktop)
    else:
        role = os.environ.get('WAVEFORM_SSH_ROLE')
        if role not in {'jump', 'waveform', 'desktop'}:
            print('SSH askpass role is unavailable', file=sys.stderr)
            return 1
        print({'jump': jump_password, 'waveform': waveform_password,
               'desktop': desktop_password}[role])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
