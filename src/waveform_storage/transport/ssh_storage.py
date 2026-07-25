"""SSH storage operations extracted from the legacy overlap module."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import paramiko

from waveform_storage.config.settings import AppSettings
from waveform_storage.io.hdf5_io import read_single_device_hdf5
from waveform_storage.processing.alignment import DeviceSeries, SkippedDevice


class SshStorageError(RuntimeError):
    """A storage operation failed before per-file handling was possible."""


def _connect(hostname, username, password, port):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(hostname, port=port, username=username, password=password)
    except Exception as exc:
        ssh.close()
        raise SshStorageError(f"SSH connection failed: {exc}") from exc
    return ssh


def list_time_fields(hostname, username, password, remote_path, port=22):
    ssh = _connect(hostname, username, password, port)
    numbers = set()
    try:
        _, stdout, _ = ssh.exec_command(
            f"find {remote_path} -name '*.h5' -type f"
        )
        for file_path in stdout.read().decode().splitlines():
            try:
                numbers.add(os.path.basename(file_path).split("-")[1])
            except (IndexError, AttributeError):
                continue
        return sorted(numbers)
    finally:
        ssh.close()


def find_h5_files(hostname, username, password, remote_path, time_sec, port=22):
    ssh = _connect(hostname, username, password, port)
    pairs = []
    try:
        _, stdout, stderr = ssh.exec_command(
            f"find {remote_path} -name '*{time_sec}*.h5' -type f"
        )
        error = stderr.read().decode()
        if error:
            raise SshStorageError(error)
        for file_path in stdout.read().decode().splitlines():
            match = re.search(
                r"RAW-\d+-(.*?)-S\d+\.h5", file_path.rsplit("/", 1)[-1]
            )
            if match:
                pairs.append((file_path, match.group(1)))
        return pairs
    finally:
        ssh.close()


def save_time_files(
    hostname, username, password, remote_path, local_path, time_sec, port=22
):
    Path(local_path).mkdir(parents=True, exist_ok=True)
    ssh = _connect(hostname, username, password, port)
    sftp = None
    try:
        sftp = ssh.open_sftp()
        _, stdout, _ = ssh.exec_command(
            f"find {remote_path} -name '*{time_sec}*.h5' -type f"
        )
        saved = []
        for remote_file in stdout.read().decode().splitlines():
            filename = remote_file.rsplit("/", 1)[-1]
            local_file = os.path.join(local_path, filename)
            sftp.get(os.path.join(remote_path, filename), local_file)
            saved.append(local_file)
        return saved
    finally:
        if sftp is not None:
            sftp.close()
        ssh.close()


def download_device_series(
    file_device_pairs, *, reader=read_single_device_hdf5, settings=None
):
    pairs = list(file_device_pairs)
    if not pairs:
        return [], []
    active = settings or AppSettings.from_env()
    ssh = _connect(
        active.storage_host,
        active.storage_user,
        active.storage_password,
        active.storage_port,
    )
    sftp = None
    records, skipped = [], []
    try:
        sftp = ssh.open_sftp()
        for remote_file, expected_device_id in pairs:
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".h5", delete=True
                ) as temporary:
                    sftp.get(remote_file, temporary.name)
                    source = reader(temporary.name)
                if source.device_id != expected_device_id:
                    raise ValueError(
                        f"file metadata device {source.device_id!r} does not "
                        f"match filename device {expected_device_id!r}"
                    )
                records.append(
                    DeviceSeries(
                        expected_device_id,
                        source.bunch_ids,
                        source.x,
                        source.y,
                    )
                )
            except Exception as exc:
                skipped.append(
                    SkippedDevice(expected_device_id, "read_failed", str(exc))
                )
        return records, skipped
    finally:
        if sftp is not None:
            sftp.close()
        ssh.close()
