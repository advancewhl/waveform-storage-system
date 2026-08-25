from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from uuid import UUID, uuid4

import paramiko

from waveform_storage.acquisition.protocol import (
    GET_STATUS,
    HEALTH,
    FIND_WAVEFORM_FILES,
    LIST_CAPTURE_TIMES,
    PROTOCOL_VERSION,
    CaptureRequest,
    CaptureResponse,
    ControlRequest,
    MetadataQueryRequest,
    decode_response,
    encode_control_request,
    encode_metadata_query_request,
    encode_request,
)
from waveform_storage.acquisition.runtime import (
    AcquisitionServerSettings,
    ServerSettingsError,
    load_server_registry,
)
from waveform_storage.acquisition.task import CaptureCancelled
from waveform_storage.acquisition.server import (
    CandidateAcquisitionServer,
    JsonTaskStore,
    PersistentTaskExecutor,
    RegistryPolicy,
)
from waveform_storage.application.capture_service import DeviceCaptureResult
from waveform_storage.config.registry import (
    Device,
    DeviceRegistry,
    save_registry,
)


def make_registry() -> DeviceRegistry:
    devices = []
    for index, selected in enumerate((True, False), 1):
        name = f"SBPM{index:02d}"
        devices.append(
            Device(
                id=f"AC_LA_TEST_BI_{name}",
                section="TEST",
                display_name=f"TEST/{name}",
                device_type="SBPM",
                capture_pv=(
                    f"AC-LA-TEST-BI:{name}-E:RESULT_CAPTURE"
                ),
                epics_host=f"10.200.0.{index}",
                epics_port=6310,
                ssh_host=f"10.200.0.{index}",
                ssh_port=22,
                credential_profile="bpm_device_default",
                remote_data_dir="/home/data",
                file_pattern="*.txt",
                expected_points=2,
                x_unit=None,
                y_unit=None,
                scale_factor=None,
                order=index - 1,
                enabled=True,
                acquisition_enabled=selected,
                default_selected=selected,
                tags=("TEST", "SBPM"),
            )
        )
    return DeviceRegistry(1, tuple(devices), "test")


def capture_request(request_id=None) -> CaptureRequest:
    return CaptureRequest(
        PROTOCOL_VERSION,
        request_id or str(uuid4()),
        "start_capture",
        100,
        1,
        ("TEST",),
        ("AC_LA_TEST_BI_SBPM01",),
    )


class PersistentServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release = threading.Event()
        self.cancel_requested = threading.Event()
        self.calls = []

        def runner(request):
            self.calls.append(request.request_id)
            self.release.wait(2)
            if self.cancel_requested.is_set():
                raise CaptureCancelled()
            return (
                DeviceCaptureResult(
                    "AC_LA_TEST_BI_SBPM01",
                    True,
                    self.root / "result.h5",
                ),
            )

        self.store = JsonTaskStore(self.root / "state")
        self.tasks = PersistentTaskExecutor(
            self.store,
            runner,
            canceller=self._cancel,
            prepare=self._prepare,
        )
        self.server = CandidateAcquisitionServer(
            "inproc://unused",
            RegistryPolicy(make_registry()),
            self.tasks,
        )

    def tearDown(self):
        self.release.set()
        self.server.close()
        self.temporary.cleanup()

    def handle(self, request):
        if isinstance(request, CaptureRequest):
            encoded = encode_request(request)
        else:
            encoded = encode_control_request(request)
        return self.server.handle_message(encoded)

    def _cancel(self):
        self.cancel_requested.set()
        self.release.set()

    def _prepare(self):
        self.cancel_requested.clear()
        self.release.clear()

    def wait_for_status(self, request_id, status):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = self.tasks.status(request_id)
            if response is not None and response.status == status:
                return response
            time.sleep(0.01)
        self.fail(f"task did not reach {status}")

    def test_accepts_asynchronously_and_duplicate_is_idempotent(self):
        request = capture_request()
        accepted = self.handle(request)
        duplicate = self.handle(request)

        self.assertIn(accepted.status, {"accepted", "running"})
        self.assertIn(duplicate.status, {"accepted", "running"})
        self.assertEqual(self.calls, [request.request_id])

        self.release.set()
        completed = self.wait_for_status(request.request_id, "succeeded")
        self.assertEqual(completed.error_code, None)
        record = json.loads(
            (self.root / "state" / f"{request.request_id}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [item["status"] for item in record["history"]],
            ["accepted", "running", "succeeded"],
        )
        self.assertTrue(record["device_results"][0]["success"])

    def test_busy_conflict_registry_and_control_requests_are_explicit(self):
        first = capture_request()
        self.handle(first)

        busy = self.handle(capture_request())
        self.assertEqual((busy.status, busy.error_code), (
            "rejected",
            "SERVICE_BUSY",
        ))

        conflicting = CaptureRequest(
            first.version,
            first.request_id,
            first.command,
            50,
            first.duration_seconds,
            first.area_ids,
            first.devices,
        )
        conflict = self.handle(conflicting)
        self.assertEqual(conflict.error_code, "REQUEST_ID_CONFLICT")

        partial = CaptureRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            "start_capture",
            100,
            1,
            ("TEST",),
            (
                "AC_LA_TEST_BI_SBPM01",
                "AC_LA_TEST_BI_SBPM02",
            ),
        )
        rejected = self.handle(partial)
        self.assertEqual(
            rejected.error_code,
            "REGISTRY_VALIDATION_FAILED",
        )

        limited_server = CandidateAcquisitionServer(
            "inproc://limited",
            RegistryPolicy(
                make_registry(),
                max_duration_seconds=1,
            ),
            self.tasks,
        )
        too_long = capture_request()
        too_long = CaptureRequest(
            too_long.version,
            too_long.request_id,
            too_long.command,
            too_long.frequency_hz,
            2,
            too_long.area_ids,
            too_long.devices,
        )
        limited = limited_server.handle_message(
            encode_request(too_long)
        )
        self.assertEqual(
            limited.error_code,
            "REGISTRY_VALIDATION_FAILED",
        )

        health_id = str(uuid4())
        health = self.handle(
            ControlRequest(PROTOCOL_VERSION, health_id, HEALTH)
        )
        self.assertEqual((health.status, health.request_id), (
            "succeeded",
            health_id,
        ))
        status = self.handle(
            ControlRequest(
                PROTOCOL_VERSION,
                first.request_id,
                GET_STATUS,
            )
        )
        self.assertIn(status.status, {"accepted", "running"})

    def test_invalid_json_is_rejected_with_zero_request_id(self):
        response = self.server.handle_message(b"not-json")
        self.assertEqual(response.status, "rejected")
        self.assertEqual(response.error_code, "INVALID_REQUEST")
        self.assertEqual(response.request_id, str(UUID(int=0)))

    def test_active_task_can_be_cancelled_at_a_safe_boundary(self):
        from waveform_storage.acquisition.protocol import CANCEL

        request = capture_request()
        self.handle(request)
        self.wait_for_status(request.request_id, "running")
        reply = self.handle(
            ControlRequest(
                PROTOCOL_VERSION,
                request.request_id,
                CANCEL,
            )
        )
        self.assertTrue(reply.accepted)
        completed = self.wait_for_status(request.request_id, "cancelled")
        self.assertEqual(completed.status, "cancelled")

    def test_metadata_queries_are_served_without_directory_scans(self):
        class Catalog:
            def list_capture_times(self, storage_date_local):
                self.date = storage_date_local
                return ("120721",)

            def find_waveform_files(self, **query):
                self.query = query
                return (
                    {
                        "request_id": "capture-1",
                        "device_id": "AC_LA_TEST_BI_SBPM01",
                        "file": {
                            "path": (
                                "/data/RAW-120721-"
                                "AC_LA_TEST_BI_SBPM01-S00000.h5"
                            ),
                            "storage_id": "shine_data_primary",
                        },
                    },
                )

        catalog = Catalog()
        server = CandidateAcquisitionServer(
            "inproc://metadata",
            RegistryPolicy(make_registry()),
            self.tasks,
            metadata=catalog,
        )
        times = server.handle_message(
            encode_metadata_query_request(
                MetadataQueryRequest(
                    PROTOCOL_VERSION,
                    str(uuid4()),
                    LIST_CAPTURE_TIMES,
                    "2026-08-25",
                )
            )
        )
        files = server.handle_message(
            encode_metadata_query_request(
                MetadataQueryRequest(
                    PROTOCOL_VERSION,
                    str(uuid4()),
                    FIND_WAVEFORM_FILES,
                    "2026-08-25",
                    "120721",
                    ("AC_LA_TEST_BI_SBPM01",),
                )
            )
        )
        self.assertEqual(times.payload["times"], ["120721"])
        self.assertEqual(
            files.payload["files"][0]["device_id"],
            "AC_LA_TEST_BI_SBPM01",
        )
        self.assertEqual(catalog.query["time_sec"], "120721")


class RestartRecoveryTests(unittest.TestCase):
    def test_incomplete_task_is_failed_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonTaskStore(directory)
            request = capture_request()
            store.create(
                request,
                CaptureResponse(
                    PROTOCOL_VERSION,
                    request.request_id,
                    True,
                    "accepted",
                    "accepted",
                ),
            )
            restarted = JsonTaskStore(directory)
            tasks = PersistentTaskExecutor(restarted, lambda _request: ())
            try:
                response = tasks.status(request.request_id)
            finally:
                tasks.close()

        self.assertEqual(response.status, "failed")
        self.assertEqual(
            response.error_code,
            "INTERRUPTED_BY_RESTART",
        )


class ServerSettingsTests(unittest.TestCase):
    def test_requires_known_hosts_and_keeps_secrets_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "devices.json"
            registry.write_text("{}\n", encoding="utf-8")
            known_hosts = root / "known_hosts"
            key = paramiko.RSAKey.generate(1024)
            known_hosts.write_text(
                f"device {key.get_name()} {key.get_base64()}\n",
                encoding="utf-8",
            )
            environment = {
                "WAVEFORM_SERVER_REGISTRY": str(registry),
                "WAVEFORM_DEVICE_SSH_KNOWN_HOSTS": str(known_hosts),
                "WAVEFORM_DEVICE_SSH_PASSWORD": "not-logged",
                "WAVEFORM_LOCAL_DATA_ROOT": str(root / "data"),
            }
            settings = AcquisitionServerSettings.from_env(
                environment,
                project_root=root,
            )

            self.assertNotIn("not-logged", repr(settings))
            self.assertNotIn(
                "device_ssh_password",
                settings.public_values(),
            )
            with self.assertRaisesRegex(
                ServerSettingsError,
                "ALLOW_REMOTE_BIND",
            ):
                AcquisitionServerSettings.from_env(
                    {
                        **environment,
                        "WAVEFORM_SERVER_ENDPOINT": "tcp://0.0.0.0:5001",
                    },
                    project_root=root,
                )
            with self.assertRaisesRegex(
                ServerSettingsError,
                "KNOWN_HOSTS",
            ):
                AcquisitionServerSettings.from_env(
                    {
                        "WAVEFORM_SERVER_REGISTRY": str(registry),
                        "WAVEFORM_LOCAL_DATA_ROOT": str(root / "data"),
                    },
                    project_root=root,
                )

    def test_preflight_requires_keys_for_all_deployment_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "devices.json"
            save_registry(
                DeviceRegistry(
                    1,
                    (make_registry().devices[0],),
                    "gray",
                ),
                registry_path,
            )
            known_hosts = root / "known_hosts"
            key = paramiko.RSAKey.generate(1024)
            known_hosts.write_text(
                f"10.200.0.1 {key.get_name()} {key.get_base64()}\n",
                encoding="utf-8",
            )
            settings = AcquisitionServerSettings.from_env(
                {
                    "WAVEFORM_SERVER_REGISTRY": str(registry_path),
                    "WAVEFORM_DEVICE_SSH_KNOWN_HOSTS": str(known_hosts),
                    "WAVEFORM_LOCAL_DATA_ROOT": str(root / "data"),
                },
                project_root=root,
            )
            loaded = load_server_registry(settings)
            self.assertEqual(len(loaded.devices), 1)

            known_hosts.write_text(
                f"other {key.get_name()} {key.get_base64()}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ServerSettingsError,
                "missing acquisition device",
            ):
                load_server_registry(settings)


class WireServerTests(unittest.TestCase):
    def test_zmq_loop_handles_health_and_stops_cleanly(self):
        import zmq

        with tempfile.TemporaryDirectory() as directory:
            context = zmq.Context()
            endpoint = f"inproc://server-{uuid4()}"
            tasks = PersistentTaskExecutor(
                JsonTaskStore(directory),
                lambda _request: (),
            )
            server = CandidateAcquisitionServer(
                endpoint,
                RegistryPolicy(make_registry()),
                tasks,
                context=context,
            )
            stop = threading.Event()
            thread = threading.Thread(
                target=server.serve_forever,
                args=(stop,),
            )
            thread.start()
            socket = context.socket(zmq.REQ)
            socket.setsockopt(zmq.RCVTIMEO, 1000)
            request = ControlRequest(
                PROTOCOL_VERSION,
                str(uuid4()),
                HEALTH,
            )
            try:
                socket.connect(endpoint)
                socket.send_string(encode_control_request(request))
                response = decode_response(socket.recv())
            finally:
                socket.close()
                stop.set()
                thread.join(2)
                server.close()
                context.term()

        self.assertEqual(response.status, "succeeded")
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
