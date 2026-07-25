"""Order-independent bunchID alignment without Qt, SSH, HDF5, or plotting.

The module deliberately treats bunchID values as opaque unsigned integers.  It
does not guess scale factors or convert between incompatible device timebases.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from numbers import Integral
from typing import Literal, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray


AlignmentMode = Literal["strict", "coverage"]
ZeroPolicy = Literal["keep", "drop", "reject_device"]
DuplicatePolicy = Literal["reject_device", "first"]


@dataclass(frozen=True)
class DeviceSeries:
    """One BPM's bunchID and X/Y series before validation."""

    device_id: str
    bunch_ids: ArrayLike
    x: ArrayLike
    y: ArrayLike


@dataclass(frozen=True)
class AlignmentParameters:
    """Explicit alignment and input-quality policies."""

    mode: AlignmentMode = "strict"
    min_id_device_coverage: float = 1.0
    min_device_id_coverage: float = 1.0
    zero_policy: ZeroPolicy = "keep"
    duplicate_policy: DuplicatePolicy = "reject_device"

    def __post_init__(self) -> None:
        if self.mode not in ("strict", "coverage"):
            raise ValueError(f"unsupported alignment mode: {self.mode}")
        for name, value in (
            ("min_id_device_coverage", self.min_id_device_coverage),
            ("min_device_id_coverage", self.min_device_id_coverage),
        ):
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1], got {value}")
        if self.zero_policy not in ("keep", "drop", "reject_device"):
            raise ValueError(f"unsupported zero_policy: {self.zero_policy}")
        if self.duplicate_policy not in ("reject_device", "first"):
            raise ValueError(f"unsupported duplicate_policy: {self.duplicate_policy}")


@dataclass(frozen=True)
class DeviceQuality:
    """Validation evidence for one input device."""

    device_id: str
    status: Literal["usable", "skipped"]
    original_count: int
    usable_count: int
    zero_count: int
    duplicate_count: int
    was_strictly_increasing: bool
    step_values: tuple[int, ...]
    reason: str | None = None


@dataclass(frozen=True)
class SkippedDevice:
    """A device excluded from the aligned result, with an actionable reason."""

    device_id: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class AlignmentResult:
    """Structured aligned data shared by future GUI, plotting, and export code."""

    bunch_ids: NDArray[np.uint64]
    device_ids: tuple[str, ...]
    x: NDArray[np.generic]
    y: NDArray[np.generic]
    valid_mask: NDArray[np.bool_]
    skipped_devices: tuple[SkippedDevice, ...]
    quality_report: tuple[DeviceQuality, ...]
    parameters: AlignmentParameters


@dataclass(frozen=True)
class _PreparedSeries:
    device_id: str
    bunch_ids: NDArray[np.uint64]
    x: NDArray[np.generic]
    y: NDArray[np.generic]


def align_devices(
    records: Sequence[DeviceSeries],
    parameters: AlignmentParameters | None = None,
) -> AlignmentResult:
    """Validate and align BPM records using strict or coverage consensus.

    Strict mode returns the exact intersection across every usable device.

    Coverage mode repeatedly:
    1. keeps IDs present in the configured fraction of active devices;
    2. removes devices that cover too little of that candidate ID set;
    3. recomputes until the active device set is stable.

    This process is deterministic and independent of input device order.  It
    does not transform bunchID values from incompatible clocks or encodings.
    Missing values in coverage output are represented by ``valid_mask=False``;
    corresponding X/Y cells contain zero only as a storage placeholder.
    """

    params = parameters or AlignmentParameters()
    prepared, quality, skipped = _prepare_records(records, params)
    prepared.sort(key=lambda item: item.device_id)

    if not prepared:
        return _empty_result(params, quality, skipped)

    if params.mode == "strict":
        target_ids = _strict_intersection(prepared)
        if target_ids.size == 0:
            skipped.extend(
                SkippedDevice(item.device_id, "no_common_bunch_ids")
                for item in prepared
            )
            return _empty_result(params, quality, skipped)
        active = prepared
    else:
        active, target_ids, consensus_skipped = _coverage_consensus(prepared, params)
        skipped.extend(consensus_skipped)
        if not active or target_ids.size == 0:
            return _empty_result(params, quality, skipped)

    x, y, valid_mask = _extract_aligned(active, target_ids)
    return AlignmentResult(
        bunch_ids=target_ids,
        device_ids=tuple(item.device_id for item in active),
        x=x,
        y=y,
        valid_mask=valid_mask,
        skipped_devices=tuple(skipped),
        quality_report=tuple(quality),
        parameters=params,
    )


def _prepare_records(
    records: Sequence[DeviceSeries],
    params: AlignmentParameters,
) -> tuple[list[_PreparedSeries], list[DeviceQuality], list[SkippedDevice]]:
    prepared: list[_PreparedSeries] = []
    quality: list[DeviceQuality] = []
    skipped: list[SkippedDevice] = []
    seen_device_ids: set[str] = set()

    for record in records:
        device_id = str(record.device_id).strip()
        if not device_id:
            reason = "empty_device_id"
            skipped.append(SkippedDevice("", reason))
            quality.append(_skipped_quality("", reason))
            continue

        if device_id in seen_device_ids:
            reason = "duplicate_device_id"
            skipped.append(SkippedDevice(device_id, reason))
            quality.append(_skipped_quality(device_id, reason))
            continue
        seen_device_ids.add(device_id)

        try:
            bunch_ids = _as_uint64_ids(record.bunch_ids)
            x = _as_numeric_1d(record.x, "x")
            y = _as_numeric_1d(record.y, "y")
        except (TypeError, ValueError, OverflowError) as exc:
            reason = "invalid_array"
            skipped.append(SkippedDevice(device_id, reason, str(exc)))
            quality.append(_skipped_quality(device_id, reason))
            continue

        original_count = int(bunch_ids.size)
        if x.size != original_count or y.size != original_count:
            reason = "length_mismatch"
            detail = f"bunch_ids={original_count}, x={x.size}, y={y.size}"
            skipped.append(SkippedDevice(device_id, reason, detail))
            quality.append(
                _skipped_quality(device_id, reason, original_count=original_count)
            )
            continue

        zero_count = int(np.count_nonzero(bunch_ids == 0))
        if zero_count and params.zero_policy == "reject_device":
            reason = "zero_bunch_id"
            skipped.append(
                SkippedDevice(device_id, reason, f"zero_count={zero_count}")
            )
            quality.append(
                _skipped_quality(
                    device_id,
                    reason,
                    original_count=original_count,
                    zero_count=zero_count,
                )
            )
            continue
        if zero_count and params.zero_policy == "drop":
            keep = bunch_ids != 0
            bunch_ids, x, y = bunch_ids[keep], x[keep], y[keep]

        if bunch_ids.size == 0:
            reason = "empty_after_filter"
            skipped.append(SkippedDevice(device_id, reason))
            quality.append(
                _skipped_quality(
                    device_id,
                    reason,
                    original_count=original_count,
                    zero_count=zero_count,
                )
            )
            continue

        was_increasing = bool(np.all(bunch_ids[1:] > bunch_ids[:-1]))
        order = np.argsort(bunch_ids, kind="stable")
        bunch_ids, x, y = bunch_ids[order], x[order], y[order]

        unique_ids, first_indices, counts = np.unique(
            bunch_ids,
            return_index=True,
            return_counts=True,
        )
        duplicate_count = int(np.sum(counts - 1))
        if duplicate_count and params.duplicate_policy == "reject_device":
            reason = "duplicate_bunch_id"
            skipped.append(
                SkippedDevice(device_id, reason, f"duplicate_count={duplicate_count}")
            )
            quality.append(
                _skipped_quality(
                    device_id,
                    reason,
                    original_count=original_count,
                    zero_count=zero_count,
                    duplicate_count=duplicate_count,
                    was_strictly_increasing=was_increasing,
                )
            )
            continue
        if duplicate_count:
            bunch_ids = unique_ids
            x, y = x[first_indices], y[first_indices]

        steps = tuple(
            sorted(
                {
                    int(current) - int(previous)
                    for previous, current in zip(bunch_ids[:-1], bunch_ids[1:])
                }
            )
        )
        prepared.append(_PreparedSeries(device_id, bunch_ids, x, y))
        quality.append(
            DeviceQuality(
                device_id=device_id,
                status="usable",
                original_count=original_count,
                usable_count=int(bunch_ids.size),
                zero_count=zero_count,
                duplicate_count=duplicate_count,
                was_strictly_increasing=was_increasing,
                step_values=steps,
            )
        )

    return prepared, quality, skipped


def _as_uint64_ids(values: ArrayLike) -> NDArray[np.uint64]:
    raw = np.asarray(values)
    if raw.ndim != 1:
        raise ValueError(f"bunch_ids must be 1-D, got shape {raw.shape}")

    integer_values: list[int] = []
    for value in raw:
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
            raise TypeError(f"bunchID must be an integer, got {value!r}")
        converted = int(value)
        if converted < 0:
            raise ValueError(f"bunchID must be non-negative, got {converted}")
        integer_values.append(converted)
    return np.asarray(integer_values, dtype=np.uint64)


def _as_numeric_1d(values: ArrayLike, name: str) -> NDArray[np.generic]:
    array = np.asarray(values)
    if array.ndim != 1:
        raise ValueError(f"{name} must be 1-D, got shape {array.shape}")
    if not np.issubdtype(array.dtype, np.number):
        raise TypeError(f"{name} must contain numeric values, got {array.dtype}")
    return array


def _skipped_quality(
    device_id: str,
    reason: str,
    *,
    original_count: int = 0,
    zero_count: int = 0,
    duplicate_count: int = 0,
    was_strictly_increasing: bool = False,
) -> DeviceQuality:
    return DeviceQuality(
        device_id=device_id,
        status="skipped",
        original_count=original_count,
        usable_count=0,
        zero_count=zero_count,
        duplicate_count=duplicate_count,
        was_strictly_increasing=was_strictly_increasing,
        step_values=(),
        reason=reason,
    )


def _strict_intersection(records: Sequence[_PreparedSeries]) -> NDArray[np.uint64]:
    result = records[0].bunch_ids.copy()
    for record in records[1:]:
        result = np.intersect1d(result, record.bunch_ids, assume_unique=True)
        if result.size == 0:
            break
    return result.astype(np.uint64, copy=False)


def _coverage_consensus(
    records: list[_PreparedSeries],
    params: AlignmentParameters,
) -> tuple[list[_PreparedSeries], NDArray[np.uint64], list[SkippedDevice]]:
    active = list(records)
    skipped: list[SkippedDevice] = []

    while active:
        target_ids = _ids_at_coverage(active, params.min_id_device_coverage)
        if target_ids.size == 0:
            skipped.extend(
                SkippedDevice(item.device_id, "no_consensus_bunch_ids")
                for item in active
            )
            return [], target_ids, skipped

        retained: list[_PreparedSeries] = []
        removed: list[tuple[_PreparedSeries, float]] = []
        for item in active:
            overlap_count = int(
                np.intersect1d(
                    target_ids,
                    item.bunch_ids,
                    assume_unique=True,
                ).size
            )
            coverage = overlap_count / int(target_ids.size)
            if coverage >= params.min_device_id_coverage:
                retained.append(item)
            else:
                removed.append((item, coverage))

        if not removed:
            return active, target_ids, skipped

        for item, coverage in removed:
            skipped.append(
                SkippedDevice(
                    item.device_id,
                    "low_consensus_coverage",
                    (
                        f"coverage={coverage:.6f}, "
                        f"required={params.min_device_id_coverage:.6f}"
                    ),
                )
            )
        active = retained

    return [], np.empty(0, dtype=np.uint64), skipped


def _ids_at_coverage(
    records: Sequence[_PreparedSeries],
    minimum_fraction: float,
) -> NDArray[np.uint64]:
    required = max(1, ceil(minimum_fraction * len(records)))
    all_ids = np.concatenate([item.bunch_ids for item in records])
    values, counts = np.unique(all_ids, return_counts=True)
    return values[counts >= required].astype(np.uint64, copy=False)


def _extract_aligned(
    records: Sequence[_PreparedSeries],
    target_ids: NDArray[np.uint64],
) -> tuple[NDArray[np.generic], NDArray[np.generic], NDArray[np.bool_]]:
    x_dtype = np.result_type(*(item.x.dtype for item in records))
    y_dtype = np.result_type(*(item.y.dtype for item in records))
    shape = (len(records), int(target_ids.size))
    x = np.zeros(shape, dtype=x_dtype)
    y = np.zeros(shape, dtype=y_dtype)
    valid_mask = np.zeros(shape, dtype=np.bool_)

    for row, item in enumerate(records):
        positions = np.searchsorted(item.bunch_ids, target_ids)
        in_bounds = positions < item.bunch_ids.size
        safe_positions = np.minimum(positions, item.bunch_ids.size - 1)
        valid = in_bounds & (item.bunch_ids[safe_positions] == target_ids)
        valid_mask[row] = valid
        x[row, valid] = item.x[safe_positions[valid]]
        y[row, valid] = item.y[safe_positions[valid]]

    return x, y, valid_mask


def _empty_result(
    params: AlignmentParameters,
    quality: Sequence[DeviceQuality],
    skipped: Sequence[SkippedDevice],
) -> AlignmentResult:
    return AlignmentResult(
        bunch_ids=np.empty(0, dtype=np.uint64),
        device_ids=(),
        x=np.empty((0, 0), dtype=np.float64),
        y=np.empty((0, 0), dtype=np.float64),
        valid_mask=np.empty((0, 0), dtype=np.bool_),
        skipped_devices=tuple(skipped),
        quality_report=tuple(quality),
        parameters=params,
    )
