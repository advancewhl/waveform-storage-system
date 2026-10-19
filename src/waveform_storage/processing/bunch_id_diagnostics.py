"""Pre-alignment evidence about raw bunchID steps.

This module reports observed integer differences only.  It never converts
bunchID values to time or changes which records participate in alignment.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from numbers import Integral
from typing import Sequence

import numpy as np

from waveform_storage.processing.alignment import DeviceSeries, SkippedDevice


@dataclass(frozen=True)
class RawDeviceStepReport:
    device_id: str
    raw_count: int
    zero_count: int
    duplicate_count: int
    usable_step_count: int
    unique_steps: tuple[int, ...]
    minimum_step: int | None
    maximum_step: int | None
    modal_step: int | None
    modal_step_count: int
    modal_step_fraction: float | None
    strictly_increasing: bool
    issue: str | None = None
    differs_from_majority: bool = False


@dataclass(frozen=True)
class RawStepReport:
    devices: tuple[RawDeviceStepReport, ...]
    missing_devices: tuple[str, ...]
    failed_devices: tuple[SkippedDevice, ...]
    majority_step: int | None
    majority_count: int
    majority_denominator: int
    has_clear_majority: bool
    raw_step_compatible: bool
    summary: str


def diagnose_raw_steps(
    records: Sequence[DeviceSeries],
    *,
    requested_devices: Sequence[str] = (),
    failed_devices: Sequence[SkippedDevice] = (),
) -> RawStepReport:
    """Summarize original adjacent bunchID differences before alignment.

    A device contributes to cross-device comparison only when its input order
    is strictly increasing and it has at least one positive adjacent step.
    A reference is reported only when one exact modal step has a strict
    majority among those eligible devices.  Invalid/missing device input,
    ambiguous per-device modes, or disagreement means continuity is left
    unconfirmed.
    """

    profiles: list[RawDeviceStepReport] = []
    seen: set[str] = set()
    duplicate_devices: set[str] = set()
    for record in records:
        device_id = str(record.device_id).strip()
        duplicate_record = bool(device_id and device_id in seen)
        if duplicate_record:
            duplicate_devices.add(device_id)
        raw_count = 0
        issues = []
        try:
            values = np.asarray(record.bunch_ids)
            if values.ndim != 1:
                raise ValueError("bunchID 不是一维数组")
            raw_count = int(values.size)
            ids = []
            zero_count = 0
            for value in values:
                if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
                    raise ValueError("bunchID 含非整数值")
                integer = int(value)
                if integer < 0 or integer > np.iinfo(np.uint64).max:
                    raise ValueError("bunchID 超出 uint64 范围")
                zero_count += integer == 0
                ids.append(integer)
        except (TypeError, ValueError, OverflowError) as exc:
            issues.append(str(exc) or "bunchID 无法读取")
            ids = []
            zero_count = 0

        duplicate_count = len(ids) - len(set(ids))
        if zero_count:
            issues.append(f"原始 bunchID 含零值 {zero_count} 个")
        if duplicate_count:
            issues.append(f"原始 bunchID 含重复值 {duplicate_count} 个")
        if duplicate_record or not device_id:
            issues.append("设备标识为空或重复，不能参与多数参考")

        increasing = len(ids) >= 2 and all(
            current > previous for previous, current in zip(ids, ids[1:])
        )
        if len(ids) < 2:
            issues.append("采样点不足，无法比较相邻数据编号的间隔")
        elif not increasing:
            issues.append("原始 bunchID 未严格递增")

        differences = [
            current - previous
            for previous, current in zip(ids, ids[1:])
            if current > previous
        ]
        counts = Counter(differences)
        unique = tuple(sorted(counts))
        modal = None
        modal_count = 0
        if counts:
            highest = max(counts.values())
            winners = [step for step, count in counts.items() if count == highest]
            if len(winners) == 1:
                modal = int(winners[0])
                modal_count = int(highest)
                if any(step % modal for step in differences):
                    issues.append("部分编号间隔不是最常见间隔的整数倍")
            else:
                issues.append("有多个同样常见的编号间隔，无法确定参考间隔")
        profiles.append(
            RawDeviceStepReport(
                device_id=device_id,
                raw_count=raw_count,
                zero_count=zero_count,
                duplicate_count=duplicate_count,
                usable_step_count=len(differences),
                unique_steps=unique,
                minimum_step=unique[0] if unique else None,
                maximum_step=unique[-1] if unique else None,
                modal_step=modal,
                modal_step_count=modal_count,
                modal_step_fraction=(modal_count / len(differences) if modal is not None else None),
                strictly_increasing=increasing,
                issue="；".join(dict.fromkeys(issues)) or None,
            )
        )
        if device_id:
            seen.add(device_id)

    read_failed_ids = {
        item.device_id for item in failed_devices
        if item.device_id and item.reason == "read_failed"
    }
    missing = tuple(sorted(set(map(str, requested_devices)) - seen - read_failed_ids))
    eligible = [
        item for item in profiles
        if (item.strictly_increasing and item.modal_step is not None
            and item.zero_count == 0 and item.duplicate_count == 0
            and item.device_id not in duplicate_devices and item.device_id)
    ]
    mode_counts = Counter(item.modal_step for item in eligible)
    majority_step = None
    majority_count = max(mode_counts.values(), default=0)
    denominator = len(eligible)
    has_majority = bool(denominator >= 2 and majority_count > denominator / 2)
    if has_majority:
        majority_step = next(
            step for step, count in mode_counts.items() if count == majority_count
        )

    if majority_step is not None:
        profiles = [
            replace(
                item,
                differs_from_majority=(
                    item.modal_step is not None
                    and item.modal_step != majority_step
                ),
            )
            for item in profiles
        ]

    incomplete = bool(missing or failed_devices or len(profiles) < len(requested_devices))
    disagreements = any(item.differs_from_majority for item in profiles)
    incompatible = any(item.issue for item in profiles)
    raw_step_compatible = bool(
        has_majority and not incomplete and not disagreements and not incompatible
        and len(eligible) == len(requested_devices or records)
    )

    if not profiles:
        summary = "未读取到原始设备序列；连续性无法确认"
    elif disagreements:
        summary = "相邻数据编号的间隔与多数设备不一致；时间基准待核实，连续性无法确认"
    elif not has_majority:
        summary = "无法确定多数设备的常见编号间隔；时间基准待核实，连续性无法确认"
    elif incomplete or incompatible:
        summary = "原始设备数据不完整，或相邻编号间隔存在疑点；时间基准待核实，连续性无法确认"
    else:
        summary = "各设备的常见编号间隔一致，其他间隔符合其整数倍；对齐后仍需检查数据是否连续"

    return RawStepReport(
        devices=tuple(profiles),
        missing_devices=missing,
        failed_devices=tuple(failed_devices),
        majority_step=majority_step,
        majority_count=majority_count,
        majority_denominator=denominator,
        has_clear_majority=has_majority,
        raw_step_compatible=raw_step_compatible,
        summary=summary,
    )
