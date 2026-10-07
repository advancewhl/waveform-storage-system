from __future__ import annotations

import unittest
import tempfile
import os
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from waveform_storage.transport import ssh_storage


class FakeStream:
    def __init__(self, value=b""):
        self.value = value

    def read(self):
        return self.value


class SshStorageTests(unittest.TestCase):
    def test_sftp_subprocess_restores_host_libraries(self):
        command = shlex.join((
            sys.executable, "-c",
            "import os,sys;sys.stdout.write(os.environ.get('LD_LIBRARY_PATH',''))",
        ))
        with patch.dict(os.environ, {
            "LD_LIBRARY_PATH": "/bundled",
            "LD_LIBRARY_PATH_ORIG": "/host",
        }):
            channel = ssh_storage._ControlSftpSocket(command)
            try:
                self.assertEqual(channel.recv(5), b"/host")
            finally:
                channel.close()

    def test_sftp_subprocess_exit_does_not_spin_on_eof(self):
        command = shlex.join((
            sys.executable, "-c",
            "import sys;sys.stderr.write('missing symbol');sys.exit(127)",
        ))
        channel = ssh_storage._ControlSftpSocket(command)
        try:
            with self.assertRaisesRegex(
                ssh_storage.SshStorageError, "missing symbol"
            ):
                channel.recv(4)
        finally:
            channel.close()

    def test_exact_catalog_paths_are_downloaded_without_find(self):
        transfers = []
        sftp = type("Sftp", (), {})()
        sftp.get = lambda remote, local: transfers.append((remote, local))
        sftp.close = lambda: None
        client = type("Client", (), {})()
        client.set_missing_host_key_policy = lambda policy: None
        client.connect = lambda *args, **kwargs: None
        client.open_sftp = lambda: sftp
        client.closed = False
        client.close = lambda: setattr(client, "closed", True)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            ssh_storage.paramiko,
            "SSHClient",
            return_value=client,
        ):
            saved = ssh_storage.save_files(
                "host",
                "user",
                None,
                (("/catalog/exact-a.h5", "BPM_A"),),
                directory,
            )
            expected_local = str(Path(directory) / "exact-a.h5")
        self.assertEqual(
            transfers,
            [("/catalog/exact-a.h5", expected_local)],
        )
        self.assertEqual(saved, [expected_local])
        self.assertTrue(client.closed)

    def test_file_discovery_keeps_path_and_device_paired_and_closes(self):
        client = type("Client", (), {})()
        client.set_missing_host_key_policy = lambda policy: None
        client.connect = lambda *args, **kwargs: None
        client.exec_command = lambda command: (
            FakeStream(),
            FakeStream(
                b"/r/RAW-120721-BPM_A-S00000.h5\n/r/malformed.h5\n"
            ),
            FakeStream(),
        )
        client.closed = False
        client.close = lambda: setattr(client, "closed", True)

        with patch.object(ssh_storage.paramiko, "SSHClient", return_value=client):
            pairs = ssh_storage.find_h5_files(
                "host", "user", None, "/r", "120721"
            )

        self.assertEqual(pairs, [("/r/RAW-120721-BPM_A-S00000.h5", "BPM_A")])
        self.assertTrue(client.closed)

    def test_connection_error_is_structured_and_client_is_closed(self):
        client = type("Client", (), {})()
        client.set_missing_host_key_policy = lambda policy: None
        client.connect = lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("offline")
        )
        client.closed = False
        client.close = lambda: setattr(client, "closed", True)

        with patch.object(ssh_storage.paramiko, "SSHClient", return_value=client):
            with self.assertRaises(ssh_storage.SshStorageError):
                ssh_storage.list_time_fields("host", "user", None, "/r")
        self.assertTrue(client.closed)

    def test_history_download_reuses_control_socket_without_new_login(self):
        transfers = []
        sftp = SimpleNamespace(
            get=lambda remote, local: transfers.append((remote, local)),
            close=lambda: None,
        )
        settings = SimpleNamespace(
            storage_host="storage.invalid",
            storage_user="operator",
            storage_password=None,
            storage_port=22,
        )
        source = SimpleNamespace(
            device_id="BPM_A",
            bunch_ids=np.array([1, 2], dtype=np.uint64),
            x=np.array([3, 4]),
            y=np.array([5, 6]),
        )
        with patch.object(
            ssh_storage, "_sftp_over_control_socket", return_value=sftp
        ) as via_control, patch.object(
            ssh_storage, "_connect", side_effect=AssertionError("direct SSH used")
        ):
            records, skipped = ssh_storage.download_device_series(
                (("/data/BPM_A.h5", "BPM_A"),),
                reader=lambda _path: source,
                settings=settings,
                control_socket="/run/user/1001/storage.sock",
            )
        self.assertEqual(len(records), 1)
        self.assertEqual(skipped, [])
        self.assertEqual(transfers[0][0], "/data/BPM_A.h5")
        via_control.assert_called_once_with(
            "storage.invalid", "operator", 22,
            "/run/user/1001/storage.sock",
        )


if __name__ == "__main__":
    unittest.main()
