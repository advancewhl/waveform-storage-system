from __future__ import annotations

import json
import unittest
from uuid import uuid4

from waveform_storage.acquisition.protocol import (
    GET_STATUS,
    FIND_WAVEFORM_FILES,
    LIST_CAPTURE_TIMES,
    PROTOCOL_VERSION,
    CaptureRequest,
    CaptureResponse,
    ControlRequest,
    MetadataQueryRequest,
    MetadataQueryResponse,
    ProtocolValidationError,
    decode_request,
    decode_response,
    decode_metadata_query_response,
    decode_service_request,
    encode_control_request,
    encode_metadata_query_request,
    encode_metadata_query_response,
    encode_request,
    encode_response,
    validate_area_device_snapshot,
    validate_status_transition,
)


class CaptureRequestTests(unittest.TestCase):
    def test_metadata_query_round_trip_is_strict(self):
        request_id = str(uuid4())
        times = MetadataQueryRequest(
            PROTOCOL_VERSION,
            request_id,
            LIST_CAPTURE_TIMES,
            "2026-08-25",
        )
        self.assertEqual(
            decode_service_request(encode_metadata_query_request(times)),
            times,
        )
        files = MetadataQueryRequest(
            PROTOCOL_VERSION,
            request_id,
            FIND_WAVEFORM_FILES,
            "2026-08-25",
            "120721",
            ("BPM_A",),
        )
        self.assertEqual(
            decode_service_request(encode_metadata_query_request(files)),
            files,
        )
        with self.assertRaisesRegex(ProtocolValidationError, "six digits"):
            MetadataQueryRequest(
                PROTOCOL_VERSION,
                request_id,
                FIND_WAVEFORM_FILES,
                "2026-08-25",
                "12:07:21",
                ("BPM_A",),
            )

    def test_control_request_round_trip_is_strict(self):
        request = ControlRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            GET_STATUS,
        )
        self.assertEqual(
            decode_service_request(encode_control_request(request)),
            request,
        )
        payload = json.loads(encode_control_request(request))
        payload["extra"] = True
        with self.assertRaisesRegex(
            ProtocolValidationError,
            "unknown fields: extra",
        ):
            decode_service_request(json.dumps(payload))

    def test_request_round_trip_is_versioned_and_stable(self):
        request = CaptureRequest(
            version=PROTOCOL_VERSION,
            request_id=str(uuid4()),
            command="start_capture",
            frequency_hz=100.0,
            duration_seconds=600,
            area_ids=("INJECTOR",),
            devices=(
                "AC_LA_INJ1_BI_SBPM08",
                "AC_LA_INJ1_BI_SBPM10",
            ),
        )
        encoded = encode_request(request)
        self.assertEqual(decode_request(encoded), request)
        self.assertEqual(json.loads(encoded)["version"], PROTOCOL_VERSION)

    def test_request_rejects_unknown_or_missing_fields(self):
        payload = {
            "version": PROTOCOL_VERSION,
            "request_id": str(uuid4()),
            "command": "start_capture",
            "frequency_hz": 100,
            "duration_seconds": 600,
            "unexpected": True,
        }
        with self.assertRaisesRegex(
            ProtocolValidationError,
            "missing fields: area_ids, devices; unknown fields: unexpected",
        ):
            decode_request(json.dumps(payload))

    def test_request_rejects_duplicate_devices_and_invalid_duration(self):
        with self.assertRaisesRegex(
            ProtocolValidationError,
            "devices contains duplicates",
        ):
            CaptureRequest(
                version=PROTOCOL_VERSION,
                request_id=str(uuid4()),
                command="start_capture",
                frequency_hz=100,
                duration_seconds=600,
                area_ids=("INJECTOR",),
                devices=("BPM_A", "BPM_A"),
            )
        with self.assertRaisesRegex(
            ProtocolValidationError,
            "duration_seconds",
        ):
            CaptureRequest(
                version=PROTOCOL_VERSION,
                request_id=str(uuid4()),
                command="start_capture",
                frequency_hz=100,
                duration_seconds=0,
                area_ids=("INJECTOR",),
                devices=("BPM_A",),
            )

    def test_request_rejects_missing_or_duplicate_areas(self):
        with self.assertRaisesRegex(ProtocolValidationError, "must not be empty"):
            CaptureRequest(
                PROTOCOL_VERSION,
                str(uuid4()),
                "start_capture",
                100,
                600,
                (),
                ("BPM_A",),
            )

    def test_server_rejects_partial_area_device_snapshot(self):
        request = CaptureRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            "start_capture",
            100,
            600,
            ("INJECTOR",),
            ("BPM_A",),
        )
        with self.assertRaisesRegex(
            ProtocolValidationError,
            "exactly match",
        ):
            validate_area_device_snapshot(
                request,
                {"INJECTOR": ("BPM_A", "BPM_B")},
            )
        validate_area_device_snapshot(request, {"INJECTOR": ("BPM_A",)})
        with self.assertRaisesRegex(ProtocolValidationError, "area_ids contains"):
            CaptureRequest(
                PROTOCOL_VERSION,
                str(uuid4()),
                "start_capture",
                100,
                600,
                ("INJECTOR", "INJECTOR"),
                ("BPM_A",),
            )


class CaptureResponseTests(unittest.TestCase):
    def test_metadata_response_round_trip_preserves_payload(self):
        response = MetadataQueryResponse(
            PROTOCOL_VERSION,
            str(uuid4()),
            True,
            "ok",
            {"times": ["120721"]},
        )
        self.assertEqual(
            decode_metadata_query_response(
                encode_metadata_query_response(response)
            ),
            response,
        )

    def test_response_round_trip_preserves_task_identity(self):
        response = CaptureResponse(
            version=PROTOCOL_VERSION,
            request_id=str(uuid4()),
            accepted=True,
            status="running",
            message="capture started",
        )
        self.assertEqual(decode_response(encode_response(response)), response)

    def test_rejected_and_failed_responses_require_error_code(self):
        for status in ("rejected", "failed"):
            with self.subTest(status=status), self.assertRaisesRegex(
                ProtocolValidationError,
                "requires error_code",
            ):
                CaptureResponse(
                    version=PROTOCOL_VERSION,
                    request_id=str(uuid4()),
                    accepted=status != "rejected",
                    status=status,
                    message="not started",
                )

    def test_task_transitions_reject_terminal_or_skipped_states(self):
        validate_status_transition("accepted", "running")
        validate_status_transition("running", "succeeded")
        with self.assertRaisesRegex(
            ProtocolValidationError,
            "succeeded -> running",
        ):
            validate_status_transition("succeeded", "running")
        with self.assertRaisesRegex(
            ProtocolValidationError,
            "accepted -> succeeded",
        ):
            validate_status_transition("accepted", "succeeded")


if __name__ == "__main__":
    unittest.main()
