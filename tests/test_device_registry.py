from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from waveform_storage.config.registry import (
    Device,
    DeviceRegistry,
    RegistryValidationError,
    import_candidate_registry,
    import_legacy_registry,
    load_registry,
    render_legacy_json_lines,
    save_registry,
    validate_registry,
)


def make_device(index: int, *, selected: bool = False) -> Device:
    host_octet = index + 1
    name = f"SBPM{host_octet:03d}"
    pv = f"AC-LA-TEST-BI:{name}-E:RESULT_CAPTURE"
    return Device(
        id=f"AC_LA_TEST_BI_{name}",
        section="TEST",
        display_name=f"TEST/{name}",
        device_type="SBPM",
        capture_pv=pv,
        epics_host=f"10.200.0.{host_octet}",
        epics_port=6310,
        ssh_host=f"10.200.0.{host_octet}",
        ssh_port=22,
        credential_profile="bpm_device_default",
        remote_data_dir="/home/data",
        file_pattern="*.txt",
        expected_points=1024,
        x_unit=None,
        y_unit=None,
        scale_factor=None,
        order=index,
        enabled=True,
        acquisition_enabled=selected,
        default_selected=selected,
        tags=("TEST", "SBPM"),
    )


class RegistryValidationTests(unittest.TestCase):
    def test_accepts_200_unique_devices(self):
        registry = DeviceRegistry(
            schema_version=1,
            devices=tuple(make_device(index) for index in range(200)),
        )

        validate_registry(registry)

        self.assertEqual(len(registry.devices), 200)

    def test_rejects_duplicate_ip_and_noncontiguous_order(self):
        first = make_device(0)
        second = replace(
            make_device(1),
            epics_host=first.epics_host,
            order=5,
        )
        registry = DeviceRegistry(
            schema_version=1,
            devices=(first, second),
        )

        with self.assertRaises(RegistryValidationError) as context:
            validate_registry(registry)

        message = str(context.exception)
        self.assertIn("duplicate EPICS host", message)
        self.assertIn("orders must be contiguous", message)

    def test_json_round_trip(self):
        registry = DeviceRegistry(
            schema_version=1,
            description="test",
            devices=(make_device(0, selected=True), make_device(1)),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            save_registry(registry, path)
            loaded = load_registry(path)

        self.assertEqual(loaded, registry)


class LegacyCompatibilityTests(unittest.TestCase):
    def test_import_and_render_are_exact(self):
        all_text = (
            '{"pv":"AC-LA-TEST-BI:SBPM001-E:RESULT_CAPTURE", '
            '"ip":"10.200.0.1", "port":"6310"},\n'
            '{"pv":"AC-LA-TEST-BI:SBPM002-E:RESULT_CAPTURE", '
            '"ip":"10.200.0.2", "port":"6310"}\n'
        )
        acquisition_text = (
            '{"pv":"AC-LA-TEST-BI:SBPM001-E:RESULT_CAPTURE", '
            '"ip":"10.200.0.1", "port":"6310"}\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            all_path = Path(directory) / "all.txt"
            acquisition_path = Path(directory) / "acquisition.txt"
            all_path.write_text(all_text, encoding="utf-8", newline="\n")
            acquisition_path.write_text(
                acquisition_text,
                encoding="utf-8",
                newline="\n",
            )
            registry = import_legacy_registry(all_path, acquisition_path)

        self.assertEqual(render_legacy_json_lines(registry, "all"), all_text)
        self.assertEqual(
            render_legacy_json_lines(registry, "acquisition"),
            acquisition_text,
        )
        self.assertEqual(
            sum(item.default_selected for item in registry.devices),
            1,
        )

    def test_acquisition_must_be_subset_of_full_list(self):
        all_text = (
            '{"pv":"AC-LA-TEST-BI:SBPM001-E:RESULT_CAPTURE", '
            '"ip":"10.200.0.1", "port":"6310"}\n'
        )
        acquisition_text = (
            '{"pv":"AC-LA-TEST-BI:SBPM002-E:RESULT_CAPTURE", '
            '"ip":"10.200.0.2", "port":"6310"}\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            all_path = Path(directory) / "all.txt"
            acquisition_path = Path(directory) / "acquisition.txt"
            all_path.write_text(all_text, encoding="utf-8")
            acquisition_path.write_text(acquisition_text, encoding="utf-8")
            with self.assertRaises(RegistryValidationError):
                import_legacy_registry(all_path, acquisition_path)


class CurrentProjectRegistryTests(unittest.TestCase):
    def test_committed_registry_reproduces_current_legacy_files(self):
        root = Path(__file__).resolve().parents[1]
        registry_path = root / "config" / "devices.json"
        if not registry_path.exists():
            self.skipTest("config/devices.json has not been generated")
        registry = load_registry(registry_path)

        self.assertEqual(len(registry.devices), 47)
        self.assertEqual(
            sum(item.acquisition_enabled for item in registry.devices),
            12,
        )
        self.assertEqual(
            render_legacy_json_lines(registry, "all"),
            (root / "config" / "pv_list_ALL.txt").read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual(
            render_legacy_json_lines(registry, "acquisition"),
            (
                (root / "config" / "acquisition_pv_list.txt")
                .read_text(encoding="utf-8")
                .rstrip("\r\n")
                + "\n"
            ),
        )

    def test_candidate_registry_contains_all_audited_devices_safely(self):
        root = Path(__file__).resolve().parents[1]
        source = (
            root
            / "outputs"
            / "pv_list_2026-07-26"
            / "pv_list_ALL_2026-07-26_candidate.txt"
        )
        registry = load_registry(root / "config" / "devices.candidate.json")
        imported = import_candidate_registry(source)

        self.assertEqual(registry, imported)
        self.assertEqual(registry.schema_version, 2)
        self.assertEqual(len(registry.devices), 383)
        self.assertEqual(len({item.id for item in registry.devices}), 383)
        self.assertEqual(len({item.epics_host for item in registry.devices}), 377)
        self.assertTrue(all(item.enabled for item in registry.devices))
        self.assertFalse(any(item.acquisition_enabled for item in registry.devices))
        self.assertTrue(
            all(item.epics_port_status == "assumed" for item in registry.devices)
        )
        self.assertTrue(
            all(item.review_status == "needs_review" for item in registry.devices)
        )
        self.assertEqual(
            render_legacy_json_lines(registry, "all"),
            source.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
