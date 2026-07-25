from __future__ import annotations

import json
import threading
import unittest
from uuid import uuid4

import zmq

from waveform_storage.acquisition.protocol import (
    CaptureRequest,
    decode_response,
)
from waveform_storage.acquisition.task import (
    CaptureCancelled,
    FakeDeviceResult,
    FakeTaskRegistry,
)
from waveform_storage.transport.zmq_transport import (
    FakeZmqServer,
    ZmqTimeout,
    ZmqTransport,
    encode_legacy_capture_message,
)


class FakeZmqLoopTests(unittest.TestCase):
    def setUp(self):
        self.context = zmq.Context()
        self.endpoint = f"inproc://waveform-{uuid4()}"

    def tearDown(self):
        self.context.term()

    def request(self, server, request):
        thread = threading.Thread(target=server.serve_once)
        thread.start()
        try:
            return ZmqTransport(
                self.endpoint,
                context=self.context,
                timeout_ms=1000,
            ).request(request)
        finally:
            thread.join(timeout=2)

    def capture_request(self):
        return CaptureRequest(
            1,
            str(uuid4()),
            "start_capture",
            100,
            1,
            ("BPM_A", "BPM_B"),
        )

    def test_success_history_and_duplicate_request_are_idempotent(self):
        calls = []
        registry = FakeTaskRegistry(
            lambda request: calls.append(request.request_id)
            or (
                FakeDeviceResult("BPM_A", True),
                FakeDeviceResult("BPM_B", True),
            )
        )
        server = FakeZmqServer(
            self.endpoint, registry, context=self.context
        )
        try:
            request = self.capture_request()
            first = self.request(server, request)
            second = self.request(server, request)
        finally:
            server.close()
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second, first)
        self.assertEqual(calls, [request.request_id])
        self.assertEqual(
            registry.history(request.request_id),
            ("accepted", "running", "succeeded"),
        )

    def test_partial_failure_exception_and_cancel_are_terminal(self):
        runners = (
            (
                lambda request: (
                    FakeDeviceResult("BPM_A", True),
                    FakeDeviceResult("BPM_B", False, "offline"),
                ),
                "failed",
            ),
            (
                lambda request: (_ for _ in ()).throw(RuntimeError("boom")),
                "failed",
            ),
            (
                lambda request: (_ for _ in ()).throw(CaptureCancelled()),
                "cancelled",
            ),
        )
        for runner, expected in runners:
            with self.subTest(expected=expected):
                endpoint = f"inproc://waveform-{uuid4()}"
                server = FakeZmqServer(
                    endpoint,
                    FakeTaskRegistry(runner),
                    context=self.context,
                )
                thread = threading.Thread(target=server.serve_once)
                thread.start()
                try:
                    response = ZmqTransport(
                        endpoint,
                        context=self.context,
                    ).request(self.capture_request())
                finally:
                    thread.join(timeout=2)
                    server.close()
                self.assertEqual(response.status, expected)

    def test_invalid_request_is_rejected(self):
        server = FakeZmqServer(
            self.endpoint,
            FakeTaskRegistry(lambda request: ()),
            context=self.context,
        )
        thread = threading.Thread(target=server.serve_once)
        thread.start()
        socket = self.context.socket(zmq.REQ)
        socket.setsockopt(zmq.LINGER, 0)
        try:
            socket.connect(self.endpoint)
            socket.send_string(json.dumps({"request_id": str(uuid4())}))
            response = decode_response(socket.recv())
        finally:
            socket.close()
            thread.join(timeout=2)
            server.close()
        self.assertEqual(response.status, "rejected")
        self.assertEqual(response.error_code, "INVALID_REQUEST")

    def test_timeout_is_explicit(self):
        request = self.capture_request()
        with self.assertRaises(ZmqTimeout):
            ZmqTransport(
                f"inproc://missing-{uuid4()}",
                context=self.context,
                timeout_ms=10,
            ).request(request)

    def test_legacy_adapter_is_exact(self):
        self.assertEqual(
            encode_legacy_capture_message(
                100,
                "10",
                ["AC_LA_INJ1_BI_SBPM08", "AC_LA_BC1_BI_SBPM02"],
            ),
            "100,10,['AC_LA_INJ1_BI_SBPM08', 'AC_LA_BC1_BI_SBPM02']",
        )


if __name__ == "__main__":
    unittest.main()
