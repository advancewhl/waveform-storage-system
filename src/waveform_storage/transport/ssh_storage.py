"""SSH storage operations extracted from the legacy overlap module."""

from __future__ import annotations

import os
import re
import select
import shlex
import stat
import subprocess
import tempfile
import time
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


class _ControlSftpSocket:
    """Expose an OpenSSH control-master SFTP channel to Paramiko."""

    def __init__(self, command: str) -> None:
        # PyInstaller prepends its bundled OpenSSL libraries to LD_LIBRARY_PATH.
        # The host's ssh binary must use the host libraries instead.
        environment = os.environ.copy()
        original_library_path = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if original_library_path is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = original_library_path
        self._process = subprocess.Popen(
            shlex.split(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=environment,
        )
        self._timeout_seconds = 30.0

    def send(self, data: bytes) -> int:
        try:
            assert self._process.stdin is not None
            written = self._process.stdin.write(data)
        except (BrokenPipeError, OSError) as exc:
            raise SshStorageError(f"SFTP SSH write failed: {exc}") from exc
        if written != len(data):
            raise SshStorageError("SFTP SSH wrote an incomplete packet")
        return written

    def recv(self, size: int) -> bytes:
        assert self._process.stdout is not None
        deadline = time.monotonic() + self._timeout_seconds
        result = bytearray()
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SshStorageError("SFTP SSH response timed out")
            ready, _, _ = select.select([self._process.stdout], [], [], remaining)
            if not ready:
                raise SshStorageError("SFTP SSH response timed out")
            chunk = os.read(self._process.stdout.fileno(), size - len(result))
            if not chunk:
                status = self._process.poll()
                detail = ""
                if status is not None and self._process.stderr is not None:
                    detail = self._process.stderr.read(512).decode(
                        "utf-8", errors="replace"
                    ).strip()
                raise SshStorageError(
                    f"SFTP SSH stream closed (exit {status}): {detail}"
                )
            result.extend(chunk)
        return bytes(result)

    def get_name(self) -> str:
        return "ssh-control-sftp"

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
        for stream in (
            self._process.stdin,
            self._process.stdout,
            self._process.stderr,
        ):
            if stream is not None:
                stream.close()


def _sftp_over_control_socket(
    hostname: str, username: str, port: int, control_socket: str
):
    socket_path = Path(control_socket)
    try:
        socket_stat = socket_path.stat()
    except OSError as exc:
        raise SshStorageError("SSH tunnel control socket is unavailable") from exc
    if (
        not socket_path.is_absolute()
        or not stat.S_ISSOCK(socket_stat.st_mode)
        or socket_stat.st_uid != os.getuid()
    ):
        raise SshStorageError("SSH tunnel control socket is invalid")
    arguments = [
        "ssh", "-S", str(socket_path),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
    ]
    known_hosts = os.environ.get("WAVEFORM_OPERATOR_KNOWN_HOSTS_FILE")
    if known_hosts:
        arguments.extend((
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", "GlobalKnownHostsFile=/dev/null",
        ))
    arguments.extend(("-p", str(port), "-s", f"{username}@{hostname}", "sftp"))
    command = shlex.join(arguments)
    channel = _ControlSftpSocket(command)
    try:
        return paramiko.SFTPClient(channel)
    except Exception as exc:
        channel.close()
        raise SshStorageError(f"SSH tunnel SFTP failed: {exc}") from exc


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


def save_files(
    hostname,
    username,
    password,
    file_device_pairs,
    local_path,
    port=22,
    *,
    control_socket=None,
):
    """Download the exact catalog-approved paths without a remote directory scan."""

    pairs = list(file_device_pairs)
    if not pairs:
        return []
    destination = Path(local_path)
    destination.mkdir(parents=True, exist_ok=True)
    ssh = None if control_socket else _connect(hostname, username, password, port)
    sftp = None
    try:
        sftp = (
            _sftp_over_control_socket(hostname, username, port, control_socket)
            if control_socket else ssh.open_sftp()
        )
        saved = []
        for remote_file, _device_id in pairs:
            filename = Path(remote_file).name
            local_file = destination / filename
            sftp.get(remote_file, str(local_file))
            saved.append(str(local_file))
        return saved
    finally:
        if sftp is not None:
            sftp.close()
        if ssh is not None:
            ssh.close()


def download_device_series(
    file_device_pairs, *, reader=read_single_device_hdf5, settings=None,
    control_socket=None,
):
    pairs = list(file_device_pairs)
    if not pairs:
        return [], []
    active = settings or AppSettings.from_env()
    ssh = None if control_socket else _connect(
        active.storage_host, active.storage_user,
        active.storage_password, active.storage_port,
    )
    sftp = None
    records, skipped = [], []
    try:
        sftp = (
            _sftp_over_control_socket(
                active.storage_host, active.storage_user,
                active.storage_port, control_socket,
            ) if control_socket else ssh.open_sftp()
        )
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
                        metadata=getattr(source, "metadata", {}),
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
        if ssh is not None:
            ssh.close()
