"""Qt-free orchestration for retrieval, alignment, caching, and export."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from pathlib import Path
from typing import Callable, Protocol, Sequence

import numpy as np

from waveform_storage.logging_config import log_context
from waveform_storage.io.hdf5_io import (
    read_single_device_hdf5,
    write_alignment_v2,
    write_legacy_merged_hdf5,
)
from waveform_storage.processing.alignment import (
    AlignmentParameters,
    AlignmentResult,
    SkippedDevice,
    align_devices,
)
from waveform_storage.processing.bunch_id_diagnostics import (
    RawStepReport,
    diagnose_raw_steps,
)


@dataclass(frozen=True)
class StorageQuery:
    """All dimensions that identify one retrieval result."""

    remote_path: str
    run_id: str
    time_sec: str
    device_ids: tuple[str, ...]
    request_id: str | None = None
    capture_sequence: int | None = None
    captured_at: str | None = None

    def __post_init__(self) -> None:
        if not self.remote_path:
            raise ValueError("remote_path must not be empty")
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if not self.time_sec:
            raise ValueError("time_sec must not be empty")
        if (self.request_id is None) != (self.capture_sequence is None):
            raise ValueError("request_id and capture_sequence must be specified together")
        if self.capture_sequence is not None and (
            isinstance(self.capture_sequence, bool)
            or not isinstance(self.capture_sequence, int) or self.capture_sequence < 0
        ):
            raise ValueError("capture_sequence must be a nonnegative integer")
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("device_ids must not contain duplicates")

    @property
    def cache_key(self) -> tuple:
        return (
            self.remote_path,
            self.run_id,
            self.time_sec,
            self.device_ids,
            self.request_id,
            self.capture_sequence,
        )


class StorageGateway(Protocol):
    def find_files(self, query: StorageQuery) -> Sequence[tuple[str, str]]:
        """Return remote path/device pairs."""

    def read_series(self, pairs, reader):
        """Return (DeviceSeries records, SkippedDevice records)."""


@dataclass(frozen=True)
class PreparedRetrieval:
    """One already-read raw retrieval, reusable for diagnosis and alignment."""

    query: StorageQuery
    records: tuple
    skipped_devices: tuple[SkippedDevice, ...]
    missing_devices: tuple[str, ...]
    raw_step_report: RawStepReport


class RetrievalService:
    """Coordinate one retrieval without depending on Qt or concrete SSH code."""

    def __init__(
        self,
        storage_gateway: StorageGateway,
        *,
        reader: Callable = read_single_device_hdf5,
        aligner: Callable = align_devices,
        legacy_writer: Callable = write_legacy_merged_hdf5,
        v2_writer: Callable = write_alignment_v2,
    ) -> None:
        self._storage = storage_gateway
        self._reader = reader
        self._aligner = aligner
        self._legacy_writer = legacy_writer
        self._v2_writer = v2_writer
        self._cache_key = None
        self._cache_result = None
        self._prepared_key = None
        self._prepared_result: PreparedRetrieval | None = None

    def clear_cache(self) -> None:
        self._cache_key = None
        self._cache_result = None
        self._prepared_key = None
        self._prepared_result = None

    def prepare(self, query: StorageQuery) -> PreparedRetrieval:
        """Read one query once and retain its raw records for later alignment."""
        if self._prepared_key == query.cache_key and self._prepared_result is not None:
            return self._prepared_result

        order = {device_id: index for index, device_id in enumerate(query.device_ids)}
        known_pairs = []
        skipped = []
        found_devices = set()
        for remote_file, device_id in self._storage.find_files(query):
            if device_id not in order:
                skipped.append(
                    SkippedDevice(
                        device_id,
                        "unknown_device",
                        "device is absent from the configured full device list",
                    )
                )
                continue
            known_pairs.append((remote_file, device_id))
            found_devices.add(device_id)
        known_pairs.sort(key=lambda item: order[item[1]])
        records, read_skipped = self._storage.read_series(known_pairs, self._reader)
        read_skipped = tuple(read_skipped)
        failed_ids = {item.device_id for item in read_skipped}
        missing = tuple(
            device_id for device_id in query.device_ids
            if device_id not in found_devices and device_id not in failed_ids
        )
        missing_skipped = tuple(
            SkippedDevice(device_id, "file_not_found", "no waveform file was returned")
            for device_id in missing
        )
        all_skipped = tuple(skipped) + read_skipped + missing_skipped
        snapshot = PreparedRetrieval(
            query=query,
            records=tuple(records),
            skipped_devices=all_skipped,
            missing_devices=missing,
            raw_step_report=diagnose_raw_steps(
                records,
                requested_devices=query.device_ids,
                failed_devices=read_skipped + tuple(skipped) + missing_skipped,
            ),
        )
        self._prepared_key = query.cache_key
        self._prepared_result = snapshot
        # A new raw snapshot invalidates the old aligned result, even when the
        # query key is the same but its cache was explicitly refreshed.
        self._cache_key = None
        self._cache_result = None
        return snapshot

    def align_prepared(
        self,
        prepared: PreparedRetrieval,
        parameters: AlignmentParameters | None = None,
    ) -> AlignmentResult:
        """Strictly align the exact raw records retained by ``prepare``."""
        result = self._aligner(
            prepared.records,
            parameters or AlignmentParameters(mode="strict"),
        )
        result = replace(
            result,
            skipped_devices=prepared.skipped_devices + result.skipped_devices,
        )
        self._cache_key = prepared.query.cache_key
        self._cache_result = result
        return result

    def retrieve(
        self,
        query: StorageQuery,
        parameters: AlignmentParameters | None = None,
    ) -> AlignmentResult:
        logger = log_context(
            logging.getLogger("waveform_storage.retrieval"),
            request_id=None,
            task_id=None,
            stage="retrieval",
            device=None,
            duration=None,
            status="running",
            error_type=None,
        )
        if self._cache_key == query.cache_key and self._cache_result is not None:
            logger.info("retrieval cache hit")
            return self._cache_result

        result = self.align_prepared(self.prepare(query), parameters)
        log_context(
            logging.getLogger("waveform_storage.retrieval"),
            request_id=None,
            task_id=None,
            stage="retrieval",
            device=None,
            duration=None,
            status="succeeded",
            error_type=None,
        ).info("retrieval completed")
        return result

    def export(
        self,
        result: AlignmentResult,
        query: StorageQuery,
        destination_dir,
        export_format: str = "auto",
    ) -> Path:
        selected = str(export_format).strip().lower()
        if selected not in {"auto", "legacy", "v2"}:
            raise ValueError("export_format must be one of: auto, legacy, v2")
        if selected == "auto":
            selected = "legacy" if np.all(result.valid_mask) else "v2"

        directory = Path(destination_dir)
        stem = f"{query.run_id}_{query.time_sec}"
        if query.request_id is not None:
            stem += f"_S{query.capture_sequence:05d}_{query.request_id}"
            selected = "v2"
        if selected == "legacy":
            destination = (
                directory / f"{stem}_BPMdata.h5"
            )
            return Path(self._legacy_writer(destination, result))

        destination = (
            directory / f"{stem}_BPMdata_v2.h5"
        )
        return Path(
            self._v2_writer(
                destination,
                result,
                metadata={
                    "run_id": query.run_id,
                    "time_sec": query.time_sec,
                    **({"request_id": query.request_id,
                        "capture_sequence": query.capture_sequence,
                        "captured_at": query.captured_at} if query.request_id else {}),
                },
            )
        )
