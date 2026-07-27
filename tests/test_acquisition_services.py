from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from waveform_storage.application.capture_service import (
    CaptureRequest,
    CaptureService,
)
from waveform_storage.io.txt_parser import TxtParseError, parse_waveform_txt
from waveform_storage.transport.device_ssh import (
    DeviceDownload,
    DeviceSshGateway,
    DownloadResult,
)
from waveform_storage.transport.epics_gateway import EpicsGateway, TriggerResult


class TxtParserTests(unittest.TestCase):
    def test_strict_parser_preserves_raw_values_without_scaling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wave.txt"
            path.write_text("1000 -2000 3\n4000 5000 6\n", encoding="utf-8")
            result = parse_waveform_txt(path, expected_points=2)
        self.assertEqual(result.x.tolist(), [1000, 4000])
        self.assertEqual(result.y.tolist(), [-2000, 5000])
        self.assertEqual(result.bunch_ids.tolist(), [3, 6])

    def test_bad_column_count_line_count_and_range_are_rejected(self):
        cases = ("1 2\n", "1 2 3\n4 5 6\n", "1 2 -1\n")
        for content in cases:
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "bad.txt"
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(TxtParseError):
                        parse_waveform_txt(path, expected_points=1)


class EpicsGatewayTests(unittest.TestCase):
    def test_device_failure_does_not_block_later_device(self):
        class Pv:
            def __init__(self, fail=False):
                self.fail = fail

            def wait_for_connection(self, timeout):
                if self.fail:
                    raise TimeoutError("offline")

        class Context:
            def get_pvs(self, name):
                return [Pv(name == "BAD")]

        class Batch:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def write(self, pv, value):
                pass

        results = EpicsGateway(
            Context,
            Batch,
            pulse_seconds=0,
        ).trigger(
            [("A", "BAD"), ("B", "GOOD")]
        )
        self.assertEqual(
            [(item.device_id, item.success) for item in results],
            [("A", False), ("B", True)],
        )


class DeviceSshGatewayTests(unittest.TestCase):
    def test_selector_download_and_resources_are_closed(self):
        events = []

        class Sftp:
            def listdir(self, remote_dir):
                return ["old.txt", "done.txt"]

            def get(self, remote, local):
                events.append((remote, local))

            def close(self):
                events.append("sftp-close")

        class Client:
            def open_sftp(self):
                return Sftp()

            def close(self):
                events.append("client-close")

        gateway = DeviceSshGateway(
            lambda host: Client(),
            lambda device, files: "done.txt",
            max_workers=1,
        )
        request = DeviceDownload("BPM_A", "host", "/data", Path("local.txt"))
        result = gateway.download_one(request)
        self.assertTrue(result.success)
        self.assertIn(("\/data/done.txt".replace("\\", ""), "local.txt"), events)
        self.assertEqual(events[-2:], ["sftp-close", "client-close"])

    def test_default_selector_requires_new_stable_completed_file(self):
        events = []

        class Attr:
            def __init__(self, filename, modified, size):
                self.filename = filename
                self.st_mtime = modified
                self.st_size = size

        class Sftp:
            def listdir_attr(self, remote_dir):
                return [
                    Attr("old.txt", 90, 10),
                    Attr("new.part", 110, 10),
                    Attr("new.txt", 110, 20),
                ]

            def stat(self, remote_path):
                return Attr(Path(remote_path).name, 110, 20)

            def get(self, remote, local):
                events.append((remote, local))

            def close(self):
                events.append("sftp-close")

        class Client:
            def open_sftp(self):
                return Sftp()

            def close(self):
                events.append("client-close")

        gateway = DeviceSshGateway(
            lambda host: Client(),
            max_workers=1,
            waiter=lambda _seconds: None,
        )
        request = DeviceDownload(
            "BPM_A",
            "host",
            "/data",
            Path("local.txt"),
            not_before_epoch=100,
            settle_seconds=1,
        )
        result = gateway.download_one(request)

        self.assertTrue(result.success)
        self.assertEqual(events[0], ("/data/new.txt", "local.txt"))


class CaptureServiceTests(unittest.TestCase):
    def test_partial_device_failure_is_structured(self):
        class Epics:
            def trigger(self, pairs):
                return [
                    TriggerResult("A", True),
                    TriggerResult("B", False, "offline"),
                ]

        class Downloader:
            def download_many(self, requests):
                return [DownloadResult("A", True, Path("a.txt"))]

        service = CaptureService(
            Epics(),
            Downloader(),
            parser=lambda path: "parsed",
            writer=lambda device, parsed, request: f"{device}.h5",
        )
        result = service.capture(
            CaptureRequest("request", 100, 1, ("A", "B")),
            [("A", "PV_A"), ("B", "PV_B")],
            [],
        )
        self.assertEqual(
            [(item.device_id, item.success) for item in result],
            [("A", True), ("B", False)],
        )
        self.assertEqual(result[0].output_path, Path("A.h5"))


class LegacyAcquisitionCharacterizationTests(unittest.TestCase):
    def test_legacy_risks_remain_explicitly_isolated(self):
        root = Path(__file__).resolve().parents[1]
        legacy = (
            root / "src" / "waveform_storage" / "legacy"
            / "acquisition_agent_adapter.py"
        ).read_text(encoding="utf-8")
        entry = (
            root / "apps" / "acquisition_agent"
            / "main_file_transport_h5df.py"
        ).read_text(encoding="utf-8")
        self.assertIn("files_name[0]", legacy)
        self.assertIn("timestr - start_time", legacy)
        self.assertIn("shape=(1, 1024)", legacy)
        self.assertIn("/1000", legacy)
        self.assertNotIn("paramiko", entry)
        self.assertNotIn("h5py", entry)


if __name__ == "__main__":
    unittest.main()
