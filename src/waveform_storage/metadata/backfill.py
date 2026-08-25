"""Import legacy single-device HDF5 files into the metadata catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import h5py

from waveform_storage.config.registry import DeviceRegistry
from waveform_storage.metadata.models import WaveformFileMetadata
from waveform_storage.metadata.repository import MetadataRepository


_DATE_DIRECTORY = re.compile(r"^\d{8}$")
_RAW_FILENAME = re.compile(
    r"^RAW-(?P<time>\d{6})-(?P<device>[A-Z0-9_]+)-S(?P<sequence>\d+)\.h5$"
)


class HistoricalImportError(ValueError):
    """Raised when a historical directory cannot be imported safely."""


@dataclass(frozen=True)
class HistoricalImportSummary:
    source_directory: Path
    storage_date_local: str
    scanned_files: int
    imported_files: int
    capture_times: tuple[str, ...]
    device_ids: tuple[str, ...]


def import_historical_directory(
    repository: MetadataRepository,
    registry: DeviceRegistry,
    source_directory: str | Path,
    *,
    catalog_directory: str | Path | None = None,
    storage_id: str = "shine_data_primary",
    timezone_name: str = "Asia/Shanghai",
    dry_run: bool = False,
) -> HistoricalImportSummary:
    """Validate and idempotently catalog one ``YYYYMMDD`` directory.

    ``source_directory`` is where files are inspected. ``catalog_directory``
    is the path visible to the storage SSH account and written to MongoDB. It
    defaults to the resolved source directory, which is useful for local runs.
    All files are validated before the first database write.
    """

    source = Path(source_directory).resolve()
    if not source.is_dir():
        raise HistoricalImportError(f"historical directory does not exist: {source}")
    if not _DATE_DIRECTORY.fullmatch(source.name):
        raise HistoricalImportError("historical directory name must use YYYYMMDD")
    try:
        calendar_date = datetime.strptime(source.name, "%Y%m%d").date()
    except ValueError as exc:
        raise HistoricalImportError("historical directory is not a valid date") from exc

    catalog = Path(catalog_directory) if catalog_directory is not None else source
    timezone = ZoneInfo(timezone_name)
    devices = {device.id: device for device in registry.devices}
    files = sorted(source.glob("*.h5"))
    if not files:
        raise HistoricalImportError(f"no HDF5 files found in {source}")

    records = []
    for path in files:
        match = _RAW_FILENAME.fullmatch(path.name)
        if match is None:
            raise HistoricalImportError(f"unsupported historical filename: {path.name}")
        device_id = match.group("device")
        device = devices.get(device_id)
        if device is None:
            raise HistoricalImportError(
                f"historical file references unknown device: {device_id}"
            )
        time_sec = match.group("time")
        try:
            captured_at = datetime.strptime(
                f"{source.name}{time_sec}", "%Y%m%d%H%M%S"
            ).replace(tzinfo=timezone)
        except ValueError as exc:
            raise HistoricalImportError(
                f"historical filename contains invalid time: {path.name}"
            ) from exc

        with h5py.File(path, "r") as h5:
            if "BunchIDData" not in h5 or "Data" not in h5:
                raise HistoricalImportError(
                    f"required datasets are missing from {path.name}"
                )
            bunch = h5["BunchIDData"]
            data = h5["Data"]
            bunch_shape = tuple(int(value) for value in bunch.shape)
            data_shape = tuple(int(value) for value in data.shape)
            bunch_dtype = str(bunch.dtype)
            data_dtype = str(data.dtype)
            if len(data_shape) != 2 or data_shape[0] != 2:
                raise HistoricalImportError(
                    f"Data must have shape (2, N) in {path.name}"
                )
            if bunch_shape != (1, data_shape[1]):
                raise HistoricalImportError(
                    f"invalid BunchIDData/Data shapes in {path.name}: "
                    f"{bunch_shape}, {data_shape}"
                )
            stored_device = _attribute_text(h5.attrs.get("Device"))
            if stored_device != device_id:
                raise HistoricalImportError(
                    f"Device attribute does not match filename in {path.name}"
                )

        request_id = str(
            uuid5(
                NAMESPACE_URL,
                f"site-user-waveform-history:{source.name}:{time_sec}",
            )
        )
        records.append(
            WaveformFileMetadata(
                request_id=request_id,
                device_id=device_id,
                capture_pv=device.capture_pv,
                captured_at=captured_at,
                storage_date_local=calendar_date.isoformat(),
                time_sec=time_sec,
                timezone_name=timezone_name,
                storage_id=storage_id,
                path=str(catalog / path.name),
                sample_count=data_shape[1],
                bunch_id_shape=bunch_shape,
                data_shape=data_shape,
                size_bytes=path.stat().st_size,
                bunch_id_dtype=bunch_dtype,
                data_dtype=data_dtype,
                sha256=_sha256(path),
                x_unit=device.x_unit,
                y_unit=device.y_unit,
                scale_factor=device.scale_factor,
            )
        )

    if not dry_run:
        for record in records:
            repository.upsert_waveform_file(record)

    return HistoricalImportSummary(
        source,
        calendar_date.isoformat(),
        len(files),
        0 if dry_run else len(records),
        tuple(sorted({record.time_sec for record in records})),
        tuple(sorted({record.device_id for record in records})),
    )


def _attribute_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value or "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
