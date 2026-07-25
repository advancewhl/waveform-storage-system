"""Injectable EPICS trigger gateway returning device-level outcomes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerResult:
    device_id: str
    success: bool
    error: str | None = None


class EpicsGateway:
    def __init__(self, context_factory, batch_factory, *, timeout=10):
        self._context_factory = context_factory
        self._batch_factory = batch_factory
        self._timeout = timeout

    def trigger(self, device_pvs):
        context = self._context_factory()
        results = []
        for device_id, pv_name in device_pvs:
            try:
                pv = context.get_pvs(pv_name)[0]
                pv.wait_for_connection(timeout=self._timeout)
                with self._batch_factory() as batch:
                    batch.write(pv, 0)
                    batch.write(pv, 1)
                with self._batch_factory() as batch:
                    batch.write(pv, 0)
                results.append(TriggerResult(device_id, True))
            except Exception as exc:
                results.append(TriggerResult(device_id, False, str(exc)))
        return results
