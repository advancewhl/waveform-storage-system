"""Build immutable operator/server releases and verify operator activation."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = ['health', 'list_capture_runs', 'list_capture_segments', 'find_waveform_files_by_segment']


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def source_manifest():
    names = set()
    for name in ('operator', 'server'):
        names.update(line.strip() for line in (ROOT / f'packaging/{name}-formal-files.txt').read_text().splitlines()
                     if line.strip() and not line.startswith('#'))
    names.update(('packaging/operator-executable.spec', 'packaging/acquisition-v2.spec',
                  'packaging/operator-formal-files.txt', 'packaging/server-formal-files.txt',
                  'packaging/requirements-acquisition-v2-build.txt', 'tools/waveform-operator.desktop',
                  'tools/release_waveform.py', 'tools/query_waveform_mongodb.py',
                  'tools/waveform_log_dashboard.py', 'tools/waveform_log_dashboard.html',
                  'tools/launch_operator_desktop.sh', 'tools/run_candidate_gui_47.sh'))
    return {name: digest(ROOT / name) for name in sorted(names)}


def run(command, log, *, environment=None):
    with Path(log).open('w') as stream:
        subprocess.run(command, cwd=ROOT, env=environment, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def metadata(directory, kind, release_id, binary_name, sources, versions):
    binary = directory / binary_name
    (directory / 'SOURCE-MANIFEST.json').write_text(json.dumps(sources, indent=2) + '\n')
    info = {
        'kind': kind, 'built_local_date': datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
        'built_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'release_id': release_id,
        'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_state': 'working-tree', 'source_manifest_sha256': digest(directory / 'SOURCE-MANIFEST.json'),
        'binary_sha256': digest(binary), 'runtime_versions': versions,
        'protocol_capabilities': CAPABILITIES,
    }
    (directory / 'BUILD-INFO').write_text(json.dumps(info, indent=2) + '\n')
    files = sorted(path for path in directory.rglob('*') if path.is_file() and path.name != 'MANIFEST.sha256')
    (directory / 'MANIFEST.sha256').write_text(''.join(f'{digest(path)}  {path.relative_to(directory)}\n' for path in files))
    return info


def activate_operator(directory):
    directory = Path(directory).resolve()
    releases = (ROOT / 'outputs/operator-local/releases').resolve()
    if not directory.is_relative_to(releases) or directory.name != 'waveform-operator':
        raise ValueError('operator release must be inside outputs/operator-local/releases')
    info = json.loads((directory / 'BUILD-INFO').read_text())
    if digest(directory / 'waveform-operator') != info['binary_sha256']:
        raise ValueError('operator binary checksum differs from BUILD-INFO')
    subprocess.run(['sha256sum', '-c', 'MANIFEST.sha256'], cwd=directory,
                   stdout=subprocess.DEVNULL, check=True)
    current = ROOT / 'outputs/operator-local/current'
    previous = str(current.resolve()) if current.exists() else None
    staged = current.with_name('current.next')
    if staged.exists() or staged.is_symlink():
        raise ValueError('a staged activation already exists')
    staged.symlink_to(directory)
    os.replace(staged, current)
    if current.resolve() != directory:
        raise RuntimeError('operator current activation failed')
    return {'previous_operator_release': previous, 'current': str(current.resolve()),
            'binary_sha256': info['binary_sha256']}


def build_pair(release_id, python_bin):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]+', release_id):
        raise ValueError('release ID must contain lowercase letters, digits and hyphens')
    evidence = ROOT / 'outputs/release-pairs' / release_id
    operator = ROOT / 'outputs/operator-local/releases' / release_id
    server = ROOT / 'outputs/server-releases' / release_id
    if any(path.exists() for path in (evidence, operator, server)):
        raise ValueError('release directories already exist; choose a new release ID')
    evidence.mkdir(parents=True)
    sources = source_manifest()
    environment = os.environ.copy()
    environment.update(QT_QPA_PLATFORM='offscreen', MPLCONFIGDIR='/tmp/waveform-release-mpl',
                       PYINSTALLER_CONFIG_DIR='/tmp/waveform-release-pyinstaller')
    run([python_bin, 'tools/run_quality.py'], evidence / 'quality.log', environment=environment)
    run([python_bin, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(operator),
         '--workpath', '/tmp/waveform-release-operator-build', 'packaging/operator-executable.spec'],
        evidence / 'operator-build.log', environment=environment)
    server.mkdir(parents=True)
    docker_script = '''set -e
python -m pip install --disable-pip-version-check --requirement packaging/requirements-acquisition-v2-build.txt
python -m PyInstaller --noconfirm --distpath "$SERVER_RELEASE_OUTPUT" --workpath /tmp/server-release-build packaging/acquisition-v2.spec
python -c 'import sys,json,importlib.metadata as m; from pathlib import Path; import os; Path(os.environ["SERVER_RELEASE_OUTPUT"],"runtime-versions.json").write_text(json.dumps({"python":sys.version,"dependencies":{p:m.version(p) for p in ["PyInstaller","caproto","h5py","numpy","paramiko","pymongo","pyzmq","cryptography"]}},indent=2))'
chown -R "$RELEASE_OWNER" "$SERVER_RELEASE_OUTPUT"
'''
    command = ['docker', 'run', '--rm', '--network', 'host']
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NO_PROXY', 'no_proxy'):
        command += ['--env', name]
    command += ['--env', f'SERVER_RELEASE_OUTPUT=/workspace/{server.relative_to(ROOT)}',
                '--env', f'RELEASE_OWNER={os.getuid()}:{os.getgid()}',
                '--volume', f'{ROOT}:/workspace', '--workdir', '/workspace',
                'python:3.10-buster', 'bash', '-lc', docker_script]
    run(command, evidence / 'server-build.log')
    if source_manifest() != sources:
        raise RuntimeError('source files changed during build; release is not activatable')
    versions = json.loads(subprocess.check_output([python_bin, '-c',
        'import sys,json,importlib.metadata as m; print(json.dumps({"python":sys.version,"dependencies":{p:m.version(p) for p in ["PyInstaller","PyQt5","h5py","numpy","matplotlib","pandas","scipy","paramiko","pyzmq","caproto","cryptography"]}}))'], text=True))
    server_versions = json.loads((server / 'runtime-versions.json').read_text())
    server_versions['build_image'] = 'python:3.10-buster'
    server_versions['build_image_digest'] = subprocess.check_output(
        ['docker', 'image', 'inspect', '--format', '{{.Id}}', 'python:3.10-buster'], text=True).strip()
    pair = {'release_id': release_id, 'protocol': 'v2 + history-run-segment extension',
            'operator': metadata(operator / 'waveform-operator', 'operator', release_id,
                                 'waveform-operator', sources, versions),
            'server': metadata(server / 'waveform-acquisition-v2', 'server', release_id,
                               'waveform-acquisition-v2', sources,
                               server_versions),
            'deployment_status': 'BUILD COMPLETE; DEPLOYMENT NOT VERIFIED',
            'compatibility': 'operator and server must support all recorded protocol capabilities'}
    (evidence / 'release-pair.json').write_text(json.dumps(pair, indent=2) + '\n')
    print(str(evidence / 'release-pair.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build-pair')
    build.add_argument('--release-id', required=True)
    build.add_argument('--python', default='/home/whloong/miniconda3/envs/waveform-storage/bin/python')
    activate = sub.add_parser('activate-operator')
    activate.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.command == 'build-pair':
        build_pair(args.release_id, args.python)
    else:
        print(json.dumps(activate_operator(args.directory), indent=2))


if __name__ == '__main__':
    main()
