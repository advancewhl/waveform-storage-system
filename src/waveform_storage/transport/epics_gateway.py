"""Injectable EPICS trigger gateway returning device-level outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass(frozen=True)
class TriggerResult:
    device_id: str
    success: bool
    error: str | None = None


class EpicsGateway:
    def __init__(
        self,
        context_factory,
        batch_factory,
        *,
        timeout=10,
        pulse_seconds=5,
        waiter=time.sleep,
    ):
        self._context_factory = context_factory
        self._batch_factory = batch_factory
        self._timeout = timeout
        self._pulse_seconds = pulse_seconds
        self._waiter = waiter

    def trigger(self, device_pvs):
        context = self._context_factory()
        connected = []
        results_by_device = {}
        try:
            for device_id, pv_name in device_pvs:
                try:
                    pv = context.get_pvs(pv_name)[0]
                    pv.wait_for_connection(timeout=self._timeout)
                    connected.append((device_id, pv))
                except Exception as exc:
                    results_by_device[device_id] = TriggerResult(
                        device_id, False, str(exc)
                    )

            if connected:
                try:
                    with self._batch_factory() as batch:
                        for _device_id, pv in connected:
                            batch.write(pv, 0)
                            batch.write(pv, 1)
                    self._waiter(self._pulse_seconds)
                except Exception as exc:
                    for device_id, _pv in connected:
                        results_by_device[device_id] = TriggerResult(
                            device_id, False, str(exc)
                        )
                finally:
                    try:
                        with self._batch_factory() as batch:
                            for _device_id, pv in connected:
                                batch.write(pv, 0)
                    except Exception as exc:
                        for device_id, _pv in connected:
                            results_by_device[device_id] = TriggerResult(
                                device_id, False, f"failed to reset PV: {exc}"
                            )

            for device_id, _pv in connected:
                results_by_device.setdefault(
                    device_id, TriggerResult(device_id, True)
                )
            return [
                results_by_device[device_id]
                for device_id, _pv_name in device_pvs
            ]
        finally:
            try:
                context.disconnect()
            except AttributeError:
                pass
