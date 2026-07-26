from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from waveform_storage.processing.alignment import DeviceSeries, SkippedDevice


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = PROJECT_ROOT / "apps" / "operator_gui" / "legacy_main.py"
OVERLAP_PATH = PROJECT_ROOT / "apps" / "operator_gui" / "overlap.py"


def _load_overlap_module():
    spec = importlib.util.spec_from_file_location(
        "operator_gui_stage0_characterization",
        OVERLAP_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {OVERLAP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _main_method(name: str) -> ast.FunctionDef:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    return child
    raise AssertionError(f"MainWindow.{name} was not found")


def _assigned_value(method: ast.FunctionDef, target_name: str) -> ast.expr:
    for node in ast.walk(method):
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == target_name
            for target in node.targets
        ):
            return node.value
    raise AssertionError(f"assignment to {target_name!r} was not found")


class GuiEntryCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overlap = _load_overlap_module()

    def test_current_button_action_binding_table_is_complete(self):
        value = _assigned_value(_main_method("_connect_actions"), "action_bindings")
        self.assertIsInstance(value, ast.Tuple)
        bindings = {}
        for item in value.elts:
            self.assertIsInstance(item, ast.Tuple)
            button, handler = item.elts
            self.assertIsInstance(button, ast.Attribute)
            self.assertIsInstance(handler, ast.Attribute)
            bindings[button.attr] = handler.attr

        self.assertEqual(
            bindings,
            {
                "startButton": "startButton_click",
                "retrievalButton": "retrievalButton_click",
                "confirmButton": "confirmButton_click",
                "showidButton": "showidButton_click",
                "plotButton": "plotButton_click",
                "downloadButton": "downloadButton_click",
                "downloadRawDataButton": "rawDataDownload_click",
            },
        )

    def test_legacy_capture_message_keeps_python_list_representation(self):
        value = _assigned_value(_main_method("startButton_click"), "message")
        expression = ast.Expression(body=value)
        ast.fix_missing_locations(expression)
        message = eval(
            compile(expression, str(MAIN_PATH), "eval"),
            {"__builtins__": {}},
            {
                "frequency": 100,
                "interval": "10",
                "selected_devices": [
                    "AC_LA_INJ1_BI_SBPM08",
                    "AC_LA_BC1_BI_SBPM02",
                ],
            },
        )
        self.assertEqual(
            message,
            "100,10,['AC_LA_INJ1_BI_SBPM08', 'AC_LA_BC1_BI_SBPM02']",
        )

    def test_date_time_and_remote_path_format_are_fixed(self):
        method = _main_method("retrievalButton_click")
        run_id_value = None
        remote_path_value = None
        for node in ast.walk(method):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "runID":
                    run_id_value = node.value
                if isinstance(target, ast.Attribute) and target.attr == "remote_path":
                    remote_path_value = node.value

        self.assertIsNotNone(run_id_value)
        run_id = eval(
            compile(
                ast.fix_missing_locations(ast.Expression(run_id_value)),
                str(MAIN_PATH),
                "eval",
            ),
            {"__builtins__": {"str": str}},
            {"date": "2026-07-25"},
        )
        self.assertEqual(run_id, "20260725")

        self.assertIsInstance(remote_path_value, ast.Call)
        self.assertEqual(ast.unparse(remote_path_value.func), "os.path.join")
        self.assertEqual(
            [ast.unparse(item) for item in remote_path_value.args],
            ["'/SHINE_Data/AC/LA/'", "self.runID", "'raw/BIData/'"],
        )
        self.assertEqual(self.overlap.get_para("12:07:21"), "120721")

    def test_alignment_cache_key_contains_all_query_dimensions(self):
        value = _assigned_value(_main_method("_ensure_alignment_result"), "key")
        self.assertIsInstance(value, ast.Tuple)
        self.assertEqual(
            [ast.unparse(item) for item in value.elts],
            [
                "self.remote_path",
                "self.runID",
                "time_sec",
                "tuple(self.pv_list)",
            ],
        )

    def test_remote_time_query_uses_h5_filename_time_field(self):
        commands = []

        class FakeStream:
            def __init__(self, payload=b""):
                self.payload = payload

            def read(self):
                return self.payload

        class FakeSsh:
            def set_missing_host_key_policy(self, policy):
                self.policy = policy

            def connect(self, *args, **kwargs):
                return None

            def exec_command(self, command):
                commands.append(command)
                payload = (
                    b"/remote/RAW-120721-BPM_A-S00000.h5\n"
                    b"/remote/RAW-120722-BPM_B-S00000.h5\n"
                    b"/remote/RAW-120721-BPM_C-S00000.h5\n"
                    b"/remote/malformed.h5\n"
                )
                return FakeStream(), FakeStream(payload), FakeStream()

            def close(self):
                return None

        remote_path = "/SHINE_Data/AC/LA/20260725/raw/BIData/"
        with patch.object(
            self.overlap.paramiko,
            "SSHClient",
            return_value=FakeSsh(),
        ):
            result = self.overlap.remote_search_time_sec(
                "storage.invalid",
                "user",
                None,
                remote_path,
            )

        self.assertEqual(
            commands,
            [f"find {remote_path} -name '*.h5' -type f"],
        )
        self.assertEqual(result, ["120721", "120722"])


class RemoteReadPairingCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overlap = _load_overlap_module()

    def test_middle_read_failure_does_not_shift_later_device_path(self):
        downloaded_devices = {}
        remote_devices = {
            "/remote/a.h5": "BPM_A",
            "/remote/c.h5": "BPM_C",
        }

        class FakeTemporaryFile:
            counter = 0

            def __init__(self, *args, **kwargs):
                type(self).counter += 1
                self.name = f"temporary-{type(self).counter}.h5"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        class FakeSftp:
            def get(self, remote_path, local_path):
                if remote_path == "/remote/bad.h5":
                    raise OSError("synthetic read failure")
                downloaded_devices[local_path] = remote_devices[remote_path]

            def close(self):
                return None

        class FakeSsh:
            def set_missing_host_key_policy(self, policy):
                self.policy = policy

            def connect(self, *args, **kwargs):
                return None

            def open_sftp(self):
                return FakeSftp()

            def close(self):
                return None

        def fake_reader(path):
            device_id = downloaded_devices[str(path)]
            return DeviceSeries(
                device_id,
                np.asarray([1, 2], dtype=np.uint64),
                np.asarray([10, 11]),
                np.asarray([20, 21]),
            )

        with patch.object(
            self.overlap.paramiko,
            "SSHClient",
            return_value=FakeSsh(),
        ), patch.object(
            self.overlap.tempfile,
            "NamedTemporaryFile",
            side_effect=FakeTemporaryFile,
        ), patch.object(
            self.overlap,
            "read_single_device_hdf5",
            side_effect=fake_reader,
        ):
            records, skipped = self.overlap.read_h5_device_series(
                [
                    ("/remote/a.h5", "BPM_A"),
                    ("/remote/bad.h5", "BPM_B"),
                    ("/remote/c.h5", "BPM_C"),
                ]
            )

        self.assertEqual([item.device_id for item in records], ["BPM_A", "BPM_C"])
        self.assertEqual(
            skipped,
            [
                SkippedDevice(
                    "BPM_B",
                    "read_failed",
                    "synthetic read failure",
                )
            ],
        )

    def test_legacy_overlap_wrappers_remain_importable(self):
        for name in (
            "get_overlap_indices_matrix",
            "dataprocess",
            "create_hdf5",
            "selectConfirm",
            "showBunchID",
            "plotbunchID",
            "main_dataprocess",
            "save_newfile",
        ):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(self.overlap, name)))


if __name__ == "__main__":
    unittest.main()
