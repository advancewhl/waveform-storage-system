"""Compatibility facade for the historical GUI ZMQ client import."""

from waveform_storage.legacy.zmq_client_dealer_adapter import (
    get_local_ip,
    send_start,
    send_start_waiting,
)

__all__ = ["get_local_ip", "send_start", "send_start_waiting"]
