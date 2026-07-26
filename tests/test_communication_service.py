from __future__ import annotations

import unittest
from uuid import uuid4

import zmq

from waveform_storage.acquisition.protocol import (
    CaptureRequest,
    CaptureResponse,
    PROTOCOL_VERSION,
)
from waveform_storage.acquisition.task import FakeTaskRegistry
from waveform_storage.application.capture_service import (
    CapturePlan,
    CaptureService,
)
from waveform_storage.application.communication_service import (
    CaptureCommunicationService,
    CaptureProtocolMode,
    CommunicationConfigurationError,
)
from waveform_storage.transport.device_ssh import DownloadResult
from waveform_storage.transport.epics_gateway import TriggerResult
from waveform_storage.transport.zmq_transport import (
    FakeZmqServer,
    ZmqTransport,
)


class CommunicationServiceTests(unittest.TestCase):
    def request(self, *, duration_seconds=600):
        return CaptureRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            "start_capture",
            100,
            duration_seconds,
            ("AREA_A",),
            ("BPM_A", "BPM_B"),
        )

    def test_legacy_dispatch_preserves_exact_message(self):
        messages = []
        request = self.request()
        result = CaptureCommunicationService(
            legacy_sender=lambda message: messages.append(message) or "OK"
        ).dispatch(request, CaptureProtocolMode.LEGACY)

        self.assertEqual(
            messages,
            ["100,10,['BPM_A', 'BPM_B']"],
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.status, "legacy_response")
        self.assertEqual(result.raw_response, "OK")

    def test_legacy_rejects_unrepresentable_seconds_without_sending(self):
        messages = []
        with self.assertRaises(CommunicationConfigurationError):
            CaptureCommunicationService(
                legacy_sender=messages.append
            ).dispatch(self.request(duration_seconds=61), "legacy")
        self.assertEqual(messages, [])

    def test_candidate_dispatch_keeps_request_identity_and_status(self):
        request = self.request()

        def sender(received):
            self.assertIs(received, request)
            return CaptureResponse(
                PROTOCOL_VERSION,
                request.request_id,
                True,
                "running",
                "capture started",
            )

        result = CaptureCommunicationService(
            candidate_sender=sender
        ).dispatch(request, "candidate_v2")
        self.assertEqual(result.request_id, request.request_id)
        self.assertEqual(result.status, "running")

    def test_candidate_mismatched_response_is_rejected(self):
        request = self.request()
        service = CaptureCommunicationService(
            candidate_sender=lambda _: CaptureResponse(
                PROTOCOL_VERSION,
                str(uuid4()),
                True,
                "running",
                "wrong task",
            )
        )
        with self.assertRaises(CommunicationConfigurationError):
            service.dispatch(request, "candidate_v2")

    def test_protocol_request_maps_to_application_plan(self):
        request = self.request()
        plan = CapturePlan.from_protocol(request)
        self.assertEqual(plan.request_id, request.request_id)
        self.assertEqual(plan.area_ids, request.area_ids)
        self.assertEqual(plan.device_ids, request.devices)
        self.assertEqual(plan.duration_seconds, 600)

    def test_candidate_protocol_runs_capture_service_in_local_closed_loop(self):
        class Epics:
            def trigger(self, pairs):
                return [
                    TriggerResult(device_id, True)
                    for device_id, _pv in pairs
                ]

        class Downloader:
            def download_many(self, requests):
                return [
                    DownloadResult(item, True, f"{item}.txt")
                    for item in requests
                ]

        capture = CaptureService(
            Epics(),
            Downloader(),
            parser=lambda path: f"parsed:{path}",
            writer=lambda device, parsed, plan: f"{device}.h5",
        )

        def resolve(plan):
            return (
                [(item, f"PV:{item}") for item in plan.device_ids],
                list(plan.device_ids),
            )

        context = zmq.Context()
        endpoint = f"inproc://communication-{uuid4()}"
        server = FakeZmqServer(
            endpoint,
            FakeTaskRegistry(
                lambda request: capture.capture_protocol_request(
                    request,
                    resolve,
                )
            ),
            context=context,
        )
        request = self.request(duration_seconds=60)
        import threading

        thread = threading.Thread(target=server.serve_once)
        thread.start()
        try:
            transport = ZmqTransport(
                endpoint,
                context=context,
                timeout_ms=1000,
            )
            result = CaptureCommunicationService(
                candidate_sender=transport.request
            ).dispatch(request, "candidate_v2")
        finally:
            thread.join(timeout=2)
            server.close()
            context.term()

        self.assertEqual(result.status, "succeeded")
        self.assertTrue(result.accepted)


if __name__ == "__main__":
    unittest.main()
