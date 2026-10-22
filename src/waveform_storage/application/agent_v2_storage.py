"""Bounded catalog-only snapshots; no caller-supplied paths, shell or URLs."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import time

import paramiko

from .agent_v2_contract import APIError


@dataclass
class Download:
    stream: object
    size: int
    sha256: str
    name: str


@contextmanager
def local_stream(path, roots):
    """Walk from an administrator-configured root using no-follow directory FDs."""
    path = Path(path)
    root = next((r for r in roots if path.is_absolute() and path.is_relative_to(r)), None)
    if root is None or '..' in path.parts:
        raise APIError('FORBIDDEN', 'Catalog object is outside configured storage')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = path.relative_to(root).parts
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise APIError('FORBIDDEN', 'Catalog object must be a regular file')
            yield stream, info.st_size
    finally:
        os.close(fd)


class CatalogSnapshots:
    def __init__(self, backend, roots, version_root, *, max_bytes=64 * 1024 * 1024, timeout=30):
        self.backend = backend
        self.roots = tuple(Path(p).resolve(strict=True) for p in roots)
        self.version_root = Path(version_root).resolve()
        self.max_bytes = max_bytes
        self.timeout = timeout
        if not 0 < max_bytes <= 1024 * 1024 * 1024 or not 0 < timeout <= 300:
            raise ValueError('Invalid snapshot bounds')

    @contextmanager
    def _remote(self, path):
        settings = self.backend.settings
        root = PurePosixPath(settings.storage_data_root)
        remote = PurePosixPath(path)
        if not remote.is_absolute() or '..' in remote.parts or not remote.is_relative_to(root):
            raise APIError('FORBIDDEN', 'Catalog object is outside configured storage')
        ssh = paramiko.SSHClient()
        ssh.load_system_host_keys()
        known = os.environ.get('WAVEFORM_OPERATOR_KNOWN_HOSTS_FILE')
        if known:
            ssh.load_host_keys(known)
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            ssh.connect(settings.storage_host, port=settings.storage_port,
                        username=settings.storage_user, password=settings.storage_password,
                        timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout)
            with ssh.open_sftp() as sftp:
                sftp.get_channel().settimeout(self.timeout)
                current = PurePosixPath('/')
                for part in remote.parts[1:]:
                    current /= part
                    if stat.S_ISLNK(sftp.lstat(str(current)).st_mode):
                        raise APIError('FORBIDDEN', 'Symlinks are not downloadable')
                info = sftp.stat(str(remote))
                if not stat.S_ISREG(info.st_mode):
                    raise APIError('FORBIDDEN')
                with sftp.open(str(remote), 'rb') as stream:
                    yield stream, info.st_size
        finally:
            ssh.close()

    def snapshot(self, path, *, version=False, expected_hash=None, name='waveform.h5'):
        source = (local_stream(path, (self.version_root,)) if version else
                  local_stream(path, self.roots) if self.backend.candidate_local_files else
                  self._remote(path))
        temporary = tempfile.TemporaryFile()
        started = time.monotonic()
        hasher = hashlib.sha256()
        size = 0
        try:
            with source as (stream, declared):
                if declared <= 0 or declared > self.max_bytes:
                    raise APIError('INVALID_ARGUMENT', 'Download exceeds configured byte limit or is empty')
                while True:
                    if time.monotonic() - started > self.timeout:
                        raise APIError('TIMEOUT')
                    block = stream.read(min(1024 * 1024, self.max_bytes - size + 1))
                    if not block:
                        break
                    size += len(block)
                    if size > self.max_bytes:
                        raise APIError('INVALID_ARGUMENT', 'Download exceeds configured byte limit')
                    hasher.update(block)
                    temporary.write(block)
                if size != declared:
                    raise APIError('DATA_UNREADABLE', 'Source changed during snapshot')
            checksum = hasher.hexdigest()
            if expected_hash and checksum != expected_hash:
                raise APIError('DATA_UNREADABLE', 'Source checksum mismatch')
            temporary.seek(0)
            return Download(temporary, size, checksum, name)
        except BaseException:
            temporary.close()
            raise
