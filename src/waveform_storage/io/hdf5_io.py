"""Versioned HDF5 I/O for raw and aligned waveform data."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

import h5py
import numpy as np

from waveform_storage.processing.alignment import (
    AlignmentParameters,
    AlignmentResult,
    DeviceQuality,
    DeviceSeries,
    SkippedDevice,
)
from waveform_storage.io.txt_parser import ParsedWaveform


SCHEMA_NAME = "site-user-waveform-alignment"
SCHEMA_VERSION = 2


class Hdf5SchemaError(ValueError):
    """Raised when an HDF5 file does not satisfy the expected schema."""


@dataclass(frozen=True)
class AlignmentFile:
    """An aligned result plus durable file-level provenance."""

    result: AlignmentResult
    metadata: dict[str, Any]
    created_utc: str


def write_single_device_hdf5(
    path: str | os.PathLike[str],
    device_id: str,
    waveform: ParsedWaveform,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Atomically write one captured BPM using the verified raw schema."""

    if not isinstance(device_id, str) or not device_id:
        raise Hdf5SchemaError("device_id must be a non-empty string")
    bunch_ids = np.asarray(waveform.bunch_ids)
    x = np.asarray(waveform.x)
    y = np.asarray(waveform.y)
    if bunch_ids.ndim != 1 or x.ndim != 1 or y.ndim != 1:
        raise Hdf5SchemaError("single-device arrays must be one-dimensional")
    if not (bunch_ids.size == x.size == y.size):
        raise Hdf5SchemaError("single-device arrays must have equal lengths")
    if not np.issubdtype(bunch_ids.dtype, np.integer):
        raise Hdf5SchemaError("single-device bunch IDs must be integers")
    if not np.issubdtype(x.dtype, np.integer) or not np.issubdtype(
        y.dtype, np.integer
    ):
        raise Hdf5SchemaError("single-device X/Y values must be integers")

    destination = Path(path)
    metadata_json = _json_dumps(dict(metadata or {}))

    def writer(h5: h5py.File) -> None:
        h5.attrs["Device"] = device_id
        h5.attrs["Data_0"] = "bpm_x"
        h5.attrs["Data_1"] = "bpm_y"
        h5.attrs["capture_metadata_json"] = metadata_json
        h5.create_dataset(
            "BunchIDData",
            data=bunch_ids.astype(np.uint64, copy=False).reshape(1, -1),
            dtype=np.uint64,
        )
        data = np.vstack((x, y)).astype(np.int64, copy=False)
        h5.create_dataset("Data", data=data, dtype=np.int64)

    _atomic_hdf5_write(destination, writer)
    return destination


def read_single_device_hdf5(path: str | os.PathLike[str]) -> DeviceSeries:
    """Read the production single-BPM schema into a pure DeviceSeries."""

    source = Path(path)
    with h5py.File(source, "r") as h5:
        _require_datasets(h5, ("BunchIDData", "Data"))
        bunch_raw = np.asarray(h5["BunchIDData"])
        data = np.asarray(h5["Data"])
        device_id = _decode_scalar_string(h5.attrs.get("Device", ""))

    if bunch_raw.ndim == 2 and bunch_raw.shape[0] == 1:
        bunch_ids = bunch_raw[0]
    elif bunch_raw.ndim == 1:
        bunch_ids = bunch_raw
    else:
        raise Hdf5SchemaError(
            f"BunchIDData must have shape (1, N) or (N,), got {bunch_raw.shape}"
        )
    if data.ndim != 2 or data.shape[0] != 2:
        raise Hdf5SchemaError(f"Data must have shape (2, N), got {data.shape}")
    if data.shape[1] != bunch_ids.size:
        raise Hdf5SchemaError(
            f"Data length {data.shape[1]} does not match bunchID length {bunch_ids.size}"
        )
    if not device_id:
        raise Hdf5SchemaError("Device root attribute is missing or empty")
    if not np.issubdtype(bunch_ids.dtype, np.integer):
        raise Hdf5SchemaError(f"BunchIDData must be integer, got {bunch_ids.dtype}")
    if np.issubdtype(bunch_ids.dtype, np.signedinteger) and np.any(bunch_ids < 0):
        raise Hdf5SchemaError("BunchIDData contains negative values")

    return DeviceSeries(
        device_id=device_id,
        bunch_ids=bunch_ids.astype(np.uint64, copy=False),
        x=data[0].copy(),
        y=data[1].copy(),
    )


def write_alignment_v2(
    path: str | os.PathLike[str],
    result: AlignmentResult,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Atomically write the version-2 aligned schema."""

    destination = Path(path)
    _validate_alignment_result(result)
    metadata_dict = dict(metadata or {})
    metadata_json = _json_dumps(metadata_dict)
    parameters_json = _json_dumps(asdict(result.parameters))
    created_utc = datetime.now(timezone.utc).isoformat()

    def writer(h5: h5py.File) -> None:
        h5.attrs["schema_name"] = SCHEMA_NAME
        h5.attrs["schema_version"] = SCHEMA_VERSION
        h5.attrs["created_utc"] = created_utc
        h5.attrs["parameters_json"] = parameters_json
        h5.attrs["metadata_json"] = metadata_json

        h5.create_dataset("bunch_ids", data=result.bunch_ids, dtype=np.uint64)
        h5.create_dataset("x", data=result.x)
        h5.create_dataset("y", data=result.y)
        h5.create_dataset("valid_mask", data=result.valid_mask, dtype=np.bool_)
        _write_text_dataset(h5, "device_ids", result.device_ids)
        _write_text_dataset(
            h5,
            "skipped_devices_json",
            (_json_dumps(asdict(item)) for item in result.skipped_devices),
        )
        _write_text_dataset(
            h5,
            "quality_report_json",
            (_json_dumps(asdict(item)) for item in result.quality_report),
        )

    _atomic_hdf5_write(destination, writer)
    return destination


def read_alignment_v2(path: str | os.PathLike[str]) -> AlignmentFile:
    """Read and validate a version-2 aligned file."""

    source = Path(path)
    with h5py.File(source, "r") as h5:
        schema_name = _decode_scalar_string(h5.attrs.get("schema_name", ""))
        schema_version = int(h5.attrs.get("schema_version", -1))
        if schema_name != SCHEMA_NAME or schema_version != SCHEMA_VERSION:
            raise Hdf5SchemaError(
                f"expected {SCHEMA_NAME} v{SCHEMA_VERSION}, "
                f"got {schema_name!r} v{schema_version}"
            )
        _require_datasets(
            h5,
            (
                "bunch_ids",
                "x",
                "y",
                "valid_mask",
                "device_ids",
                "skipped_devices_json",
                "quality_report_json",
            ),
        )
        bunch_ids = np.asarray(h5["bunch_ids"], dtype=np.uint64)
        x = np.asarray(h5["x"])
        y = np.asarray(h5["y"])
        valid_mask = np.asarray(h5["valid_mask"], dtype=np.bool_)
        device_ids = tuple(_read_text_dataset(h5["device_ids"]))
        skipped = tuple(
            SkippedDevice(**json.loads(item))
            for item in _read_text_dataset(h5["skipped_devices_json"])
        )
        quality = tuple(
            _quality_from_json(item)
            for item in _read_text_dataset(h5["quality_report_json"])
        )
        parameters = AlignmentParameters(
            **json.loads(_decode_scalar_string(h5.attrs["parameters_json"]))
        )
        metadata = json.loads(_decode_scalar_string(h5.attrs["metadata_json"]))
        created_utc = _decode_scalar_string(h5.attrs["created_utc"])

    result = AlignmentResult(
        bunch_ids=bunch_ids,
        device_ids=device_ids,
        x=x,
        y=y,
        valid_mask=valid_mask,
        skipped_devices=skipped,
        quality_report=quality,
        parameters=parameters,
    )
    _validate_alignment_result(result)
    return AlignmentFile(result=result, metadata=metadata, created_utc=created_utc)


def write_legacy_merged_hdf5(
    path: str | os.PathLike[str],
    result: AlignmentResult,
) -> Path:
    """Write the legacy merged layout without truncating bunchID or metadata.

    Legacy files have no missing-value mask.  The adapter therefore refuses a
    coverage result containing missing cells instead of silently writing zeros
    as if they were real measurements.
    """

    destination = Path(path)
    _validate_alignment_result(result)
    if not np.all(result.valid_mask):
        raise Hdf5SchemaError(
            "legacy merged schema cannot represent missing aligned values"
        )

    data_dtype = np.result_type(result.x.dtype, result.y.dtype)
    combined = np.empty(
        (2 * len(result.device_ids), result.bunch_ids.size),
        dtype=data_dtype,
    )
    for index in range(len(result.device_ids)):
        combined[2 * index] = result.x[index]
        combined[2 * index + 1] = result.y[index]

    def writer(h5: h5py.File) -> None:
        string_dtype = h5py.string_dtype(encoding="utf-8")
        h5.attrs.create(
            "Device",
            np.asarray(result.device_ids, dtype=object),
            dtype=string_dtype,
        )
        for index, device_id in enumerate(result.device_ids):
            h5.attrs[f"Data[0:]_{index}"] = f"{device_id}_X"
            h5.attrs[f"Data[1:]_{index}"] = f"{device_id}_Y"
        h5.create_dataset(
            "BunchIDData",
            data=result.bunch_ids.reshape(1, -1),
            dtype=np.uint64,
        )
        h5.create_dataset("Data", data=combined, dtype=data_dtype)

    _atomic_hdf5_write(destination, writer)
    return destination


def read_legacy_merged_hdf5(
    path: str | os.PathLike[str],
) -> AlignmentFile:
    """Read a legacy interleaved merged file with strict metadata checks."""

    source = Path(path)
    with h5py.File(source, "r") as h5:
        _require_datasets(h5, ("BunchIDData", "Data"))
        bunch_raw = np.asarray(h5["BunchIDData"])
        data = np.asarray(h5["Data"])
        device_ids = _decode_device_attribute(h5.attrs.get("Device"))
        if bunch_raw.ndim != 2 or bunch_raw.shape[0] != 1:
            raise Hdf5SchemaError(
                f"legacy BunchIDData must have shape (1, N), got {bunch_raw.shape}"
            )
        if data.ndim != 2 or data.shape[0] != 2 * len(device_ids):
            raise Hdf5SchemaError(
                f"legacy Data rows {data.shape} do not match "
                f"{len(device_ids)} devices"
            )
        if data.shape[1] != bunch_raw.shape[1]:
            raise Hdf5SchemaError("legacy Data and BunchIDData lengths differ")
        for index, device_id in enumerate(device_ids):
            expected_x = f"{device_id}_X"
            expected_y = f"{device_id}_Y"
            actual_x = _decode_scalar_string(
                h5.attrs.get(f"Data[0:]_{index}", "")
            )
            actual_y = _decode_scalar_string(
                h5.attrs.get(f"Data[1:]_{index}", "")
            )
            if actual_x != expected_x or actual_y != expected_y:
                raise Hdf5SchemaError(
                    f"legacy metadata mismatch at device row {index}: "
                    f"{actual_x!r}, {actual_y!r}"
                )

    bunch_ids = bunch_raw[0].astype(np.uint64, copy=False)
    x = data[0::2].copy()
    y = data[1::2].copy()
    result = AlignmentResult(
        bunch_ids=bunch_ids,
        device_ids=tuple(device_ids),
        x=x,
        y=y,
        valid_mask=np.ones(x.shape, dtype=np.bool_),
        skipped_devices=(),
        quality_report=(),
        parameters=AlignmentParameters(mode="strict"),
    )
    _validate_alignment_result(result)
    return AlignmentFile(
        result=result,
        metadata={"source_schema": "legacy-merged"},
        created_utc="",
    )


def _validate_alignment_result(result: AlignmentResult) -> None:
    if result.bunch_ids.ndim != 1:
        raise Hdf5SchemaError(
            f"bunch_ids must be 1-D, got {result.bunch_ids.shape}"
        )
    if result.bunch_ids.dtype != np.dtype(np.uint64):
        raise Hdf5SchemaError(
            f"bunch_ids must be uint64, got {result.bunch_ids.dtype}"
        )
    expected_shape = (len(result.device_ids), result.bunch_ids.size)
    for name, value in (
        ("x", result.x),
        ("y", result.y),
        ("valid_mask", result.valid_mask),
    ):
        if value.shape != expected_shape:
            raise Hdf5SchemaError(
                f"{name} shape {value.shape} != expected {expected_shape}"
            )
    if result.valid_mask.dtype != np.dtype(np.bool_):
        raise Hdf5SchemaError(
            f"valid_mask must be bool, got {result.valid_mask.dtype}"
        )
    if len(set(result.device_ids)) != len(result.device_ids):
        raise Hdf5SchemaError("device_ids contains duplicates")
    if any(not device_id for device_id in result.device_ids):
        raise Hdf5SchemaError("device_ids contains an empty value")


def _atomic_hdf5_write(destination: Path, writer: Any) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid4().hex}.part"
    )
    try:
        with h5py.File(temporary, "w") as h5:
            writer(h5)
            h5.flush()
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _require_datasets(h5: h5py.File, names: Sequence[str]) -> None:
    missing = [name for name in names if name not in h5]
    if missing:
        raise Hdf5SchemaError(f"missing datasets: {', '.join(missing)}")


def _write_text_dataset(
    h5: h5py.File,
    name: str,
    values: Sequence[str] | Any,
) -> None:
    items = tuple(str(value) for value in values)
    dtype = h5py.string_dtype(encoding="utf-8")
    h5.create_dataset(name, data=np.asarray(items, dtype=object), dtype=dtype)


def _read_text_dataset(dataset: h5py.Dataset) -> list[str]:
    return [_decode_scalar_string(value) for value in np.asarray(dataset)]


def _decode_scalar_string(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.bytes_):
        return bytes(value).decode("utf-8")
    return str(value)


def _decode_device_attribute(value: Any) -> list[str]:
    if value is None:
        raise Hdf5SchemaError("legacy Device attribute is missing")
    array = np.asarray(value)
    if array.ndim == 0:
        devices = [_decode_scalar_string(array.item())]
    elif array.ndim == 1:
        devices = [_decode_scalar_string(item) for item in array]
    else:
        raise Hdf5SchemaError(
            f"legacy Device attribute must be scalar or 1-D, got {array.shape}"
        )
    if not devices or any(not item for item in devices):
        raise Hdf5SchemaError("legacy Device attribute is empty")
    return devices


def _quality_from_json(value: str) -> DeviceQuality:
    payload = json.loads(value)
    payload["step_values"] = tuple(payload.get("step_values", ()))
    return DeviceQuality(**payload)


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise Hdf5SchemaError(f"value is not JSON serializable: {exc}") from exc
