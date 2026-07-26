"""Deployment adapters for the experimental operator window."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from uuid import uuid4

from caproto.threading.client import Context

from waveform_storage.acquisition.protocol import (
    CaptureRequest,
    PROTOCOL_VERSION,
)
from waveform_storage.application.communication_service import (
    CaptureCommunicationService,
    CaptureProtocolMode,
)
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.operator_backend import OperatorMode
from waveform_storage.transport.ssh_storage import (
    list_time_fields,
    save_time_files,
)
from waveform_storage.transport.zmq_transport import ZmqTransport

from overlap import main_alignment_process, save_alignment_result
from zmq_client_dealer import send_start
from waveform_storage.legacy.gui_capture_adapter import (
    read_legacy_frequency,
    send_legacy_message,
)


class ConnectedOperatorBackend:
    """Explicit legacy or candidate-v2 deployment adapter.

    Construction never sends traffic. Candidate v2 requires an explicit
    endpoint so it cannot accidentally target the legacy production service.
    """

    def __init__(
        self,
        settings: AppSettings,
        *,
        mode: OperatorMode,
    ) -> None:
        if mode not in {OperatorMode.LEGACY, OperatorMode.CANDIDATE_V2}:
            raise ValueError("connected backend requires legacy or candidate_v2")
        self.settings = settings
        self.mode = mode
        self.candidate_endpoint = os.environ.get(
            "WAVEFORM_CANDIDATE_ZMQ_ENDPOINT"
        )
        if mode is OperatorMode.CANDIDATE_V2 and not self.candidate_endpoint:
            raise ValueError(
                "candidate_v2 requires WAVEFORM_CANDIDATE_ZMQ_ENDPOINT"
            )

    def start_capture(
        self,
        area_ids: tuple[str, ...],
        device_ids: tuple[str, ...],
        duration_seconds: int,
    ):
        frequency = read_legacy_frequency(
            Context,
            timeout_address="192.0.2.1:6310",
        )
        if not frequency:
            raise RuntimeError("timing-system frequency PV is unavailable")
        request = CaptureRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            "start_capture",
            float(frequency),
            duration_seconds,
            area_ids,
            device_ids,
        )
        if self.mode is OperatorMode.LEGACY:
            service = CaptureCommunicationService(
                legacy_sender=lambda message: send_legacy_message(
                    send_start,
                    server_host=self.settings.zmq_server_host,
                    server_port=self.settings.zmq_server_port,
                    message=message,
                )
            )
            return service.dispatch(request, CaptureProtocolMode.LEGACY)

        transport = ZmqTransport(
            self.candidate_endpoint,
            timeout_ms=self.settings.zmq_receive_timeout_ms,
        )
        return CaptureCommunicationService(
            candidate_sender=transport.request
        ).dispatch(request, CaptureProtocolMode.CANDIDATE_V2)

    def retrieve_times(self, selected_date: date) -> tuple[str, ...]:
        return tuple(
            list_time_fields(
                self.settings.storage_host,
                self.settings.storage_user,
                self.settings.storage_password,
                self._remote_path(selected_date),
                self.settings.storage_port,
            )
        )

    def align(self, selected_date, time_sec, device_ids):
        run_id = selected_date.strftime("%Y%m%d")
        return main_alignment_process(
            self._remote_path(selected_date),
            run_id,
            time_sec,
            device_ids,
        )

    def export_alignment(
        self,
        result,
        selected_date,
        time_sec,
        destination,
    ) -> Path:
        return Path(
            save_alignment_result(
                result,
                selected_date.strftime("%Y%m%d"),
                time_sec,
                destination,
                export_format=self.settings.alignment_export_format,
            )
        )

    def download_raw(
        self,
        selected_date,
        time_sec,
        device_ids,
        destination,
    ) -> tuple[Path, ...]:
        del device_ids
        local_path = (
            Path(destination)
            / selected_date.strftime("%Y%m%d")
            / time_sec
        )
        saved = save_time_files(
            self.settings.storage_host,
            self.settings.storage_user,
            self.settings.storage_password,
            self._remote_path(selected_date),
            local_path,
            time_sec,
            self.settings.storage_port,
        )
        return tuple(Path(item) for item in saved)

    def _remote_path(self, selected_date: date) -> str:
        return (
            f"{self.settings.storage_data_root}/"
            f"{selected_date.strftime('%Y%m%d')}/raw/BIData"
        )
