"""Qt-free orchestration for retrieval, alignment, caching, and export."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol, Sequence

import numpy as np

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


@dataclass(frozen=True)
class StorageQuery:
    """All dimensions that identify one retrieval result."""

    remote_path: str
    run_id: str
    time_sec: str
    device_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.remote_path:
            raise ValueError("remote_path must not be empty")
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if not self.time_sec:
            raise ValueError("time_sec must not be empty")
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("device_ids must not contain duplicates")

    @property
    def cache_key(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (
            self.remote_path,
            self.run_id,
            self.time_sec,
            self.device_ids,
        )


class StorageGateway(Protocol):
    def find_files(self, query: StorageQuery) -> Sequence[tuple[str, str]]:
        """Return remote path/device pairs."""

    def read_series(self, pairs, reader):
        """Return (DeviceSeries records, SkippedDevice records)."""


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

    def clear_cache(self) -> None:
        self._cache_key = None
        self._cache_result = None

    def retrieve(
        self,
        query: StorageQuery,
        parameters: AlignmentParameters | None = None,
    ) -> AlignmentResult:
        if self._cache_key == query.cache_key and self._cache_result is not None:
            return self._cache_result

        order = {
            device_id: index for index, device_id in enumerate(query.device_ids)
        }
        known_pairs = []
        skipped = []
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
        known_pairs.sort(key=lambda item: order[item[1]])

        records, read_skipped = self._storage.read_series(
            known_pairs,
            self._reader,
        )
        result = self._aligner(
            records,
            parameters or AlignmentParameters(mode="strict"),
        )
        result = replace(
            result,
            skipped_devices=(
                tuple(skipped)
                + tuple(read_skipped)
                + result.skipped_devices
            ),
        )
        self._cache_key = query.cache_key
        self._cache_result = result
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
        if selected == "legacy":
            destination = (
                directory / f"{query.run_id}_{query.time_sec}_BPMdata.h5"
            )
            return Path(self._legacy_writer(destination, result))

        destination = (
            directory / f"{query.run_id}_{query.time_sec}_BPMdata_v2.h5"
        )
        return Path(
            self._v2_writer(
                destination,
                result,
                metadata={
                    "run_id": query.run_id,
                    "time_sec": query.time_sec,
                },
            )
        )
