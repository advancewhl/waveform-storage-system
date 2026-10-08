"""Candidate JSON ZMQ transport for local fake endpoints only."""

from __future__ import annotations

import json
from uuid import UUID

import zmq

from waveform_storage.acquisition.protocol import (
    CANCEL,
    FIND_WAVEFORM_FILES,
    GET_STATUS,
    GET_TIMING_FREQUENCY,
    HEALTH,
    LIST_CAPTURE_TIMES,
    LIST_CAPTURE_RUNS, LIST_CAPTURE_SEGMENTS, FIND_WAVEFORM_FILES_BY_SEGMENT,
    CaptureSegmentQueryRequest,
    CaptureResponse,
    ControlRequest,
    MetadataQueryRequest,
    PROTOCOL_VERSION,
    ProtocolValidationError,
    decode_request,
    decode_metadata_query_response,
    decode_response,
    encode_control_request,
    encode_metadata_query_request,
    encode_request,
    encode_response,
)


class ZmqTimeout(TimeoutError):
    pass


class ZmqTransport:
    def __init__(self, endpoint, *, context=None, timeout_ms=1000):
        self.endpoint = endpoint
        self.context = context or zmq.Context.instance()
        self.timeout_ms = timeout_ms

    def request(self, capture_request):
        return self._exchange(encode_request(capture_request))

    def get_status(self, request_id):
        request = ControlRequest(
            PROTOCOL_VERSION,
            request_id,
            GET_STATUS,
        )
        return self._exchange(encode_control_request(request))

    def health(self, request_id):
        request = ControlRequest(
            PROTOCOL_VERSION,
            request_id,
            HEALTH,
        )
        return self._exchange(encode_control_request(request))

    def get_timing_frequency(self, request_id):
        request = ControlRequest(
            PROTOCOL_VERSION,
            request_id,
            GET_TIMING_FREQUENCY,
        )
        return self._exchange_metadata(encode_control_request(request))

    def cancel(self, request_id):
        request = ControlRequest(
            PROTOCOL_VERSION,
            request_id,
            CANCEL,
        )
        return self._exchange(encode_control_request(request))

    def list_capture_times(self, request_id, storage_date_local):
        request = MetadataQueryRequest(
            PROTOCOL_VERSION,
            request_id,
            LIST_CAPTURE_TIMES,
            storage_date_local,
        )
        return self._exchange_metadata(encode_metadata_query_request(request))

    def find_waveform_files(
        self,
        request_id,
        storage_date_local,
        time_sec,
        device_ids,
    ):
        request = MetadataQueryRequest(
            PROTOCOL_VERSION,
            request_id,
            FIND_WAVEFORM_FILES,
            storage_date_local,
            time_sec,
            tuple(device_ids),
        )
        return self._exchange_metadata(encode_metadata_query_request(request))

    def list_capture_runs(self, request_id, storage_date_local):
        request = MetadataQueryRequest(
            PROTOCOL_VERSION, request_id, LIST_CAPTURE_RUNS, storage_date_local,
        )
        return self._exchange_metadata(encode_metadata_query_request(request))

    def list_capture_segments(self, request_id, capture_request_id):
        request = CaptureSegmentQueryRequest(
            PROTOCOL_VERSION, request_id, LIST_CAPTURE_SEGMENTS, capture_request_id,
        )
        return self._exchange_metadata(encode_metadata_query_request(request))

    def find_waveform_files_by_segment(
        self, request_id, capture_request_id, capture_sequence, device_ids,
    ):
        request = CaptureSegmentQueryRequest(
            PROTOCOL_VERSION, request_id, FIND_WAVEFORM_FILES_BY_SEGMENT,
            capture_request_id, capture_sequence, tuple(device_ids),
        )
        return self._exchange_metadata(encode_metadata_query_request(request))

    def _exchange(self, message):
        return self._exchange_with_decoder(message, decode_response)

    def _exchange_metadata(self, message):
        return self._exchange_with_decoder(
            message,
            decode_metadata_query_response,
        )

    def _exchange_with_decoder(self, message, decoder):
        socket = self.context.socket(zmq.REQ)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        try:
            socket.connect(self.endpoint)
            socket.send_string(message)
            try:
                message = socket.recv()
            except zmq.Again as exc:
                raise ZmqTimeout(
                    f"candidate endpoint timed out after {self.timeout_ms} ms"
                ) from exc
            return decoder(message)
        finally:
            socket.close()


class FakeZmqServer:
    def __init__(self, endpoint, task_registry, *, context=None):
        self.endpoint = endpoint
        self.registry = task_registry
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.REP)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.bind(endpoint)

    def serve_once(self):
        message = self.socket.recv()
        try:
            request = decode_request(message)
            response = self.registry.execute(request)
        except ProtocolValidationError as exc:
            request_id = _request_id_or_zero(message)
            response = CaptureResponse(
                PROTOCOL_VERSION,
                request_id,
                False,
                "rejected",
                str(exc),
                "INVALID_REQUEST",
            )
        self.socket.send_string(encode_response(response))
        return response

    def close(self):
        self.socket.close()


def encode_legacy_capture_message(frequency, interval, devices):
    """Exact adapter for the current production-compatible GUI string."""

    return f"{frequency},{interval},{devices}"


def _request_id_or_zero(message):
    zero = str(UUID(int=0))
    try:
        payload = json.loads(message.decode("utf-8"))
        value = payload.get("request_id")
        return str(UUID(value)) if value is not None else zero
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return zero
