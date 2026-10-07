"""Backend contract and safe demo implementation for the operator GUI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import h5py
import numpy as np

from waveform_storage.acquisition.protocol import (
    CaptureRequest,
    CaptureResponse,
    PROTOCOL_VERSION,
    decode_request,
    encode_request,
)
from waveform_storage.application.communication_service import (
    CaptureCommunicationService,
    CaptureDispatchResult,
    CaptureProtocolMode,
)
from waveform_storage.config.registry import DeviceRegistry
from waveform_storage.io.hdf5_io import write_alignment_v2
from waveform_storage.processing.alignment import (
    AlignmentParameters,
    AlignmentResult,
    DeviceSeries,
    align_devices,
)


from waveform_storage.gui.operator_contract import OperatorBackend, OperatorMode


@dataclass
class DemoOperatorBackend:
    """Deterministic offline backend; never opens network connections."""

    registry: DeviceRegistry
    mode: OperatorMode = OperatorMode.DEMO

    def __post_init__(self) -> None:
        self._last_request_json = ""

    def start_capture(
        self,
        area_ids: tuple[str, ...],
        device_ids: tuple[str, ...],
        duration_seconds: int,
        requested_frequency_hz: float | None = None,
    ) -> CaptureDispatchResult:
        request = CaptureRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            "start_capture",
            requested_frequency_hz or 100,
            duration_seconds,
            area_ids,
            device_ids,
        )
        self._last_request_json = encode_request(request)
        decoded = decode_request(self._last_request_json)
        service = CaptureCommunicationService(
            candidate_sender=lambda received: CaptureResponse(
                PROTOCOL_VERSION,
                received.request_id,
                True,
                "succeeded",
                (
                    f"demo accepted {len(received.area_ids)} areas and "
                    f"{len(received.devices)} devices"
                ),
            )
        )
        return service.dispatch(decoded, CaptureProtocolMode.CANDIDATE_V2)

    def get_capture_status(self, request_id: str) -> CaptureResponse:
        return CaptureResponse(
            PROTOCOL_VERSION,
            request_id,
            True,
            "succeeded",
            "demo capture completed",
        )

    def retrieve_times(self, selected_date: date) -> tuple[str, ...]:
        del selected_date
        return ("083000", "120000", "154500")

    def align(
        self,
        selected_date: date,
        time_sec: str,
        device_ids: tuple[str, ...],
    ) -> AlignmentResult:
        del selected_date
        if not device_ids:
            raise ValueError("at least one selected device is required")
        base = int(time_sec or "0") * 100_000
        bunch_ids = np.arange(base + 1, base + 65, dtype=np.uint64)
        records = []
        for index, device_id in enumerate(device_ids):
            phase = np.linspace(0, np.pi * 2, bunch_ids.size)
            records.append(
                DeviceSeries(
                    device_id,
                    bunch_ids,
                    np.round(np.sin(phase + index * 0.11) * 1.8 + index * 0.04, 4),
                    np.round(np.cos(phase + index * 0.09) * 1.4 - index * 0.03, 4),
                )
            )
        return align_devices(records, AlignmentParameters(mode="strict"))

    def export_alignment(
        self,
        result: AlignmentResult,
        selected_date: date,
        time_sec: str,
        destination: str | Path,
    ) -> Path:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        run_id = selected_date.strftime("%Y%m%d")
        return write_alignment_v2(
            directory / f"{run_id}_{time_sec}_BPMdata_demo_v2.h5",
            result,
            metadata={
                "mode": self.mode.value,
                "run_id": run_id,
                "time_sec": time_sec,
            },
        )

    def download_raw(
        self,
        selected_date: date,
        time_sec: str,
        device_ids: tuple[str, ...],
        destination: str | Path,
    ) -> tuple[Path, ...]:
        directory = (
            Path(destination)
            / f"demo_raw_{selected_date.strftime('%Y%m%d')}_{time_sec}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        result = self.align(selected_date, time_sec, device_ids)
        saved = []
        for device_index, device_id in enumerate(result.device_ids):
            safe_name = device_id.replace("/", "_").replace(":", "_")
            path = directory / f"RAW-{time_sec}-{safe_name}-S00000.h5"
            with h5py.File(path, "w") as h5:
                h5.attrs["Device"] = device_id
                h5.attrs["Data_0"] = "X"
                h5.attrs["Data_1"] = "Y"
                h5.create_dataset(
                    "BunchIDData",
                    data=result.bunch_ids.reshape(1, -1),
                    dtype=np.uint64,
                )
                h5.create_dataset(
                    "Data",
                    data=np.vstack(
                        (
                            result.x[device_index],
                            result.y[device_index],
                        )
                    ),
                )
            saved.append(path)
        return tuple(saved)

    @property
    def last_request_json(self) -> str:
        return self._last_request_json
