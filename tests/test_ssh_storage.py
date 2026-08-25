from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from waveform_storage.transport import ssh_storage


class FakeStream:
    def __init__(self, value=b""):
        self.value = value

    def read(self):
        return self.value


class SshStorageTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
