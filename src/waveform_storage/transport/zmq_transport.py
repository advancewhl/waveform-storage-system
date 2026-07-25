"""Candidate JSON ZMQ transport for local fake endpoints only."""

from __future__ import annotations

import json
from uuid import UUID

import zmq

from waveform_storage.acquisition.protocol import (
    CaptureResponse,
    PROTOCOL_VERSION,
    ProtocolValidationError,
    decode_request,
    decode_response,
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
        socket = self.context.socket(zmq.REQ)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        try:
            socket.connect(self.endpoint)
            socket.send_string(encode_request(capture_request))
            try:
                message = socket.recv()
            except zmq.Again as exc:
                raise ZmqTimeout(
                    f"candidate endpoint timed out after {self.timeout_ms} ms"
                ) from exc
            return decode_response(message)
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
