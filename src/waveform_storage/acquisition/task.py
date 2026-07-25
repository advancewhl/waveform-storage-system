"""In-memory candidate task execution for local fake protocol tests."""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import CaptureResponse, PROTOCOL_VERSION


class CaptureCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class FakeDeviceResult:
    device_id: str
    success: bool
    error: str | None = None


class FakeTaskRegistry:
    def __init__(self, runner):
        self._runner = runner
        self._responses = {}
        self._histories = {}

    def execute(self, request):
        cached = self._responses.get(request.request_id)
        if cached is not None:
            return cached
        history = ["accepted", "running"]
        try:
            device_results = tuple(self._runner(request))
            failed = [item for item in device_results if not item.success]
            if failed:
                response = CaptureResponse(
                    PROTOCOL_VERSION,
                    request.request_id,
                    True,
                    "failed",
                    f"{len(failed)} device(s) failed",
                    "PARTIAL_DEVICE_FAILURE",
                )
            else:
                response = CaptureResponse(
                    PROTOCOL_VERSION,
                    request.request_id,
                    True,
                    "succeeded",
                    "capture completed",
                )
        except CaptureCancelled:
            response = CaptureResponse(
                PROTOCOL_VERSION,
                request.request_id,
                True,
                "cancelled",
                "capture cancelled",
            )
        except Exception as exc:
            response = CaptureResponse(
                PROTOCOL_VERSION,
                request.request_id,
                True,
                "failed",
                str(exc),
                type(exc).__name__,
            )
        history.append(response.status)
        self._histories[request.request_id] = tuple(history)
        self._responses[request.request_id] = response
        return response

    def history(self, request_id):
        return self._histories.get(request_id, ())
