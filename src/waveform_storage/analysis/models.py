"""Explicit time bases, reproducible profiles, and shared result contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import math

import numpy as np

from .status import display_status_for


def format_peak_frequency(peak_hz: float | None, resolution_hz: float | None) -> str:
    """Format a peak at the decimal place implied by its frequency resolution."""
    if peak_hz is None or isinstance(peak_hz, (bool, np.bool_)):
        return "—"
    try:
        peak = float(peak_hz)
    except (TypeError, ValueError, OverflowError):
        return "—"
    if not math.isfinite(peak):
        return "—"
    try:
        if isinstance(resolution_hz, (bool, np.bool_)):
            raise ValueError("boolean is not a frequency resolution")
        resolution = float(resolution_hz)
    except (TypeError, ValueError, OverflowError):
        resolution = math.nan
    if not math.isfinite(resolution) or resolution <= 0:
        # A compact significant-digit fallback avoids exposing binary float noise.
        return f"{peak:.6g}"
    decimals = min(12, max(0, math.ceil(-math.log10(resolution))))
    return f"{peak:.{decimals}f}"


@dataclass(frozen=True)
class AnalysisConfig:
    profile: str = "high_band"
    search_low_hz: float = 40.0
    search_high_hz: float = 50.0
    background_low_hz: float = 30.0
    background_high_hz: float = 60.0
    welch_seconds: float = 20.0
    block_seconds: float = 5.0
    threshold_db: float = 6.0
    minimum_points: int = 128
    minimum_seconds: float = 5.0
    minimum_blocks: int = 3
    minimum_block_ratio: float = 0.6
    peak_match_hz: float = 1.0
    max_gap_factor: float = 5.0
    zero_policy: str = "keep"

    def __post_init__(self) -> None:
        if self.profile not in {"high_band", "low_v1", "low_v2"}:
            raise ValueError("未知的分析模板")
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and (
                isinstance(value, bool) or not math.isfinite(value)
            ):
                raise ValueError(f"参数 {key} 必须为有限数值")
        if not (0 < self.background_low_hz < self.search_low_hz
                < self.search_high_hz < self.background_high_hz):
            raise ValueError("搜索频带必须位于背景频带内部，且频率为正")
        if min(self.welch_seconds, self.block_seconds, self.minimum_seconds) <= 0:
            raise ValueError("窗口、分块和最小时长必须为正")
        if self.minimum_points < 4 or self.minimum_blocks < 1:
            raise ValueError("点数和分块数量无效")
        if not isinstance(self.minimum_points, int) or not isinstance(self.minimum_blocks, int):
            raise ValueError("点数和分块数量必须为整数")
        if not 0 <= self.minimum_block_ratio <= 1 or self.max_gap_factor <= 1:
            raise ValueError("命中比例或缺口参数无效")
        if self.threshold_db < 0 or self.peak_match_hz <= 0:
            raise ValueError("谱峰门槛与频率匹配范围无效")
        if self.zero_policy not in {"keep", "mask_double_zero"}:
            raise ValueError("未知的零值策略")

    @classmethod
    def for_profile(cls, profile: str, **overrides: Any) -> "AnalysisConfig":
        values: dict[str, Any] = {"profile": profile}
        if profile in {"low_v1", "low_v2"}:
            values.update(search_low_hz=0.142, search_high_hz=0.162,
                          background_low_hz=0.12, background_high_hz=0.19,
                          welch_seconds=300.0, block_seconds=300.0,
                          minimum_seconds=300.0, peak_match_hz=0.01)
        values.update(overrides)
        return cls(**values)


@dataclass
class ChannelSeries:
    channel: str
    times: np.ndarray
    values: np.ndarray
    source: str
    sample_rate_hz: float | None = None
    time_basis: str = "relative"
    utc_origin: str | None = None
    unit: str = "原始读数"
    valid_mask: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class AnalysisRequest:
    source_type: str
    paths: tuple[str, ...] = ()
    config: AnalysisConfig = field(default_factory=AnalysisConfig)
    sample_rate_hz: float | None = None
    alignment: Any = field(default=None, repr=False, compare=False)
    sample_rate_source: str | None = None

    def __post_init__(self) -> None:
        if self.sample_rate_source not in {None, "capture_run_summary"}:
            raise ValueError("Unknown sampling-rate source")
        if self.sample_rate_source and self.source_type != "alignment":
            raise ValueError("Historical sampling-rate source requires alignment")
        if self.source_type not in {"archiver", "hdf5", "alignment"}:
            raise ValueError("请选择 Archiver 或 HDF5 数据来源")
        if self.source_type == "alignment":
            if self.alignment is None:
                raise ValueError("没有历史对齐结果")
        elif not self.paths:
            raise ValueError("请先选择输入文件或目录")
        if self.sample_rate_hz is not None and (
            isinstance(self.sample_rate_hz, bool)
            or not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0
        ):
            raise ValueError("采样率必须为正数")


@dataclass
class ChannelResult:
    channel: str
    source: str
    classification: str = "invalid"
    reason: str = ""
    sample_rate_hz: float | None = None
    duration_seconds: float | None = None
    data_points: int = 0
    peak_hz: float | None = None
    peak_to_background_db: float | None = None
    resolution_hz: float | None = None
    block_hits: int = 0
    block_count: int = 0
    block_ratio: float | None = None
    time_basis: str = "relative"
    utc_origin: str | None = None
    unit: str = "原始读数"
    quality: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    frequency: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)
    psd: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)
    display_status: str | None = None

    def __post_init__(self) -> None:
        if self.display_status is None:
            evidence = self.details.get("status_evidence", {})
            self.display_status = display_status_for(
                self.classification,
                overall_hit=bool(evidence.get("overall_hit", False)),
            )

    def summary(self) -> dict[str, Any]:
        return {key: value for key, value in vars(self).items()
                if key not in {"frequency", "psd", "blocks", "details"}}


@dataclass
class AnalysisRun:
    job_id: str
    directory: Path
    channels: list[ChannelResult]
    manifest: dict[str, Any]
