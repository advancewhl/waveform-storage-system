"""A newly triggered device file may appear and settle after the first scan."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from waveform_storage.transport.device_ssh import DeviceDownload, DeviceSshGateway


class DeviceSshRetryTests(unittest.TestCase):
    def test_waits_for_file_then_waits_for_stable_size(self):
        clock = [0.0]
        scans = [0]
        stats = [10, 20, 20, 20]
        transfers = []

        def wait(seconds):
            clock[0] += seconds

        class Sftp:
            def listdir_attr(self, _directory):
                scans[0] += 1
                if scans[0] == 1:
                    return []
                return [SimpleNamespace(filename="capture.txt", st_mtime=100,
                                        st_size=20)]

            def stat(self, _path):
                return SimpleNamespace(st_size=stats.pop(0), st_mtime=100)

            def get(self, remote, local):
                transfers.append((remote, local))
                Path(local).write_text("data")

            def close(self):
                pass

        class Client:
            def open_sftp(self):
                return Sftp()

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory, patch(
            "waveform_storage.transport.device_ssh.time.monotonic",
            side_effect=lambda: clock[0],
        ):
            destination = Path(directory) / "capture.txt"
            result = DeviceSshGateway(lambda _request: Client(), waiter=wait).download_one(
                DeviceDownload("BPM_A", "host", "/remote", destination,
                               not_before_epoch=100, settle_seconds=1,
                               wait_timeout_seconds=10, poll_seconds=1)
            )
            self.assertTrue(result.success, result.error)
            self.assertGreaterEqual(scans[0], 3)
            self.assertEqual(transfers[0][0], "/remote/capture.txt")


if __name__ == "__main__":
    unittest.main()
