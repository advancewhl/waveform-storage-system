from __future__ import annotations

import unittest
from pathlib import Path

from waveform_storage.config.settings import AppSettings, SettingsError


class AppSettingsTests(unittest.TestCase):
    def test_defaults_preserve_current_application_values(self):
        root = Path("D:/example/project")
        settings = AppSettings.from_env({}, project_root=root)

        self.assertEqual(settings.project_root, root)
        self.assertEqual(settings.config_dir, root / "config")
        self.assertEqual(settings.zmq_server_host, "192.0.2.2")
        self.assertEqual(settings.zmq_server_port, 5000)
        self.assertEqual(settings.zmq_receive_timeout_ms, 3000)
        self.assertEqual(settings.storage_host, "192.0.2.2")
        self.assertEqual(settings.storage_port, 22)
        self.assertEqual(settings.storage_user, "storage-user")
        self.assertEqual(settings.storage_data_root, "/SHINE_Data/AC/LA")
        self.assertEqual(settings.device_remote_dir, "/home/data")
        self.assertEqual(settings.local_data_root, Path("/SHINE_Data/AC/LA"))
        self.assertEqual(settings.ssh_connect_timeout_seconds, 30)
        self.assertEqual(settings.epics_connect_timeout_seconds, 10)
        self.assertEqual(settings.alignment_export_format, "auto")

    def test_existing_environment_names_are_parsed(self):
        settings = AppSettings.from_env(
            {
                "WAVEFORM_CONFIG_DIR": "D:/config",
                "WAVEFORM_ZMQ_SERVER_HOST": "zmq.invalid",
                "WAVEFORM_STORAGE_HOST": "storage.invalid",
                "WAVEFORM_STORAGE_USER": "operator",
                "WAVEFORM_STORAGE_PASSWORD": "storage-secret",
                "WAVEFORM_DEVICE_SSH_USER": "device",
                "WAVEFORM_DEVICE_SSH_PASSWORD": "device-secret",
                "WAVEFORM_DEVICE_REMOTE_DIR": "/data",
                "WAVEFORM_LOCAL_DATA_ROOT": "D:/waveforms",
                "WAVEFORM_ALIGNMENT_EXPORT_FORMAT": "V2",
            },
            project_root="D:/project",
        )

        self.assertEqual(settings.config_dir, Path("D:/config"))
        self.assertEqual(settings.zmq_server_host, "zmq.invalid")
        self.assertEqual(settings.storage_host, "storage.invalid")
        self.assertEqual(settings.storage_user, "operator")
        self.assertEqual(settings.storage_password, "storage-secret")
        self.assertEqual(settings.device_ssh_user, "device")
        self.assertEqual(settings.device_ssh_password, "device-secret")
        self.assertEqual(settings.device_remote_dir, "/data")
        self.assertEqual(settings.local_data_root, Path("D:/waveforms"))
        self.assertEqual(settings.alignment_export_format, "v2")

    def test_optional_ports_and_timeouts_are_validated(self):
        settings = AppSettings.from_env(
            {
                "WAVEFORM_ZMQ_SERVER_PORT": "5100",
                "WAVEFORM_STORAGE_PORT": "2222",
                "WAVEFORM_DEVICE_SSH_PORT": "2200",
                "WAVEFORM_ZMQ_RECEIVE_TIMEOUT_MS": "4500",
                "WAVEFORM_SSH_CONNECT_TIMEOUT_SECONDS": "45",
                "WAVEFORM_EPICS_CONNECT_TIMEOUT_SECONDS": "15",
            },
            project_root="D:/project",
        )
        self.assertEqual(settings.zmq_server_port, 5100)
        self.assertEqual(settings.storage_port, 2222)
        self.assertEqual(settings.device_ssh_port, 2200)
        self.assertEqual(settings.zmq_receive_timeout_ms, 4500)
        self.assertEqual(settings.ssh_connect_timeout_seconds, 45)
        self.assertEqual(settings.epics_connect_timeout_seconds, 15)

    def test_invalid_port_timeout_and_export_format_are_rejected(self):
        invalid_cases = (
            {"WAVEFORM_ZMQ_SERVER_PORT": "0"},
            {"WAVEFORM_STORAGE_PORT": "not-an-integer"},
            {"WAVEFORM_SSH_CONNECT_TIMEOUT_SECONDS": "-1"},
            {"WAVEFORM_ALIGNMENT_EXPORT_FORMAT": "csv"},
        )
        for environ in invalid_cases:
            with self.subTest(environ=environ):
                with self.assertRaises(SettingsError):
                    AppSettings.from_env(environ, project_root="D:/project")

    def test_credentials_are_not_represented_or_serialized(self):
        settings = AppSettings.from_env(
            {
                "WAVEFORM_STORAGE_PASSWORD": "storage-secret",
                "WAVEFORM_DEVICE_SSH_PASSWORD": "device-secret",
            },
            project_root="D:/project",
        )

        representation = repr(settings)
        public_values = settings.public_values()
        self.assertNotIn("storage-secret", representation)
        self.assertNotIn("device-secret", representation)
        self.assertNotIn("password", " ".join(public_values))


if __name__ == "__main__":
    unittest.main()
