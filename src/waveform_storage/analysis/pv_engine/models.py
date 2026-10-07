"""Dataclasses shared by the spectral analysis pipeline."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

import numpy as np


Classification = Literal["stable_candidate", "observation", "reject", "invalid"]
AlgorithmVersion = Literal["v1", "v2"]


def _json_value(value: Any) -> Any:
    """Convert dataclass results to strict JSON-compatible Python values."""
    if is_dataclass(value):
        return {
            item.name: _json_value(getattr(value, item.name))
            for item in fields(value)
            if item.name != "plot_data"
        }
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass(slots=True)
class PVRecord:
    pv_name: str
    timestamps: Sequence[Any]
    values: Sequence[Any]
    source_file: str = ""
    server: str = ""
    warnings: list[str] = field(default_factory=list)
    relative_time: bool = False


@dataclass(frozen=True, slots=True)
class SpectrumAnalysisConfig:
    algorithm_version: AlgorithmVersion = "v1"
    target_frequency_hz: float = 0.152
    welch_window_seconds: float = 300.0
    welch_overlap_ratio: float = 0.5
    stability_block_seconds: float = 300.0
    local_band_hz: tuple[float, float] = (0.12, 0.19)
    background_exclusion_hz: tuple[float, float] = (0.142, 0.162)
    transient_rolling_window_seconds: float = 21.0
    transient_sigma_threshold: float = 8.0
    exploratory_peak_threshold_db: float = 6.0
    stable_min_block_count: int = 3
    stable_min_ratio: float = 0.6
    minimum_data_points: int = 128
    minimum_duration_seconds: float = 300.0
    max_gap_factor: float = 5.0
    detrend_window_hz: float = 0.05
    detrend_polyorder: int = 2
    detrend_target_guard_bins: int = 3
    target_peak_search_half_width_hz: float = 0.01
    detrended_prominence_threshold_db: float = 3.0
    block_detrended_prominence_threshold_db: float = 2.0
    global_robust_z_threshold: float = 3.5
    block_global_robust_z_threshold: float = 2.5
    global_peak_percentile_threshold: float = 0.95
    global_min_frequency_hz: float = 0.02
    peak_width_min_bins: float = 1.0
    peak_width_max_bins: float = 6.0
    max_workers: int = field(default_factory=lambda: min(10, os.cpu_count() or 1))
    enable_plots: bool = False
    plot_stable_candidates_and_bpm_only: bool = False
    plot_output_directory: str = "spectrum_results/plots"

    def __post_init__(self) -> None:
        low, high = self.local_band_hz
        exclude_low, exclude_high = self.background_exclusion_hz
        if self.algorithm_version not in {"v1", "v2"}:
            raise ValueError("algorithm_version must be 'v1' or 'v2'")
        if self.target_frequency_hz <= 0:
            raise ValueError("target_frequency_hz must be positive")
        if self.welch_window_seconds <= 0 or self.stability_block_seconds <= 0:
            raise ValueError("window and block durations must be positive")
        if not 0 <= self.welch_overlap_ratio < 1:
            raise ValueError("welch_overlap_ratio must be in [0, 1)")
        if not (0 <= low < exclude_low < exclude_high < high):
            raise ValueError("local/background exclusion bands are inconsistent")
        if not (low <= self.target_frequency_hz <= high):
            raise ValueError("target_frequency_hz must be inside local_band_hz")
        if not (exclude_low <= self.target_frequency_hz <= exclude_high):
            raise ValueError("target_frequency_hz must be inside background_exclusion_hz")
        if self.transient_rolling_window_seconds <= 0 or self.transient_sigma_threshold <= 0:
            raise ValueError("transient settings must be positive")
        if self.stable_min_block_count < 1 or not 0 <= self.stable_min_ratio <= 1:
            raise ValueError("stable classification settings are invalid")
        if self.minimum_data_points < 4 or self.minimum_duration_seconds <= 0:
            raise ValueError("minimum data requirements are invalid")
        if self.max_gap_factor <= 1 or self.max_workers < 1:
            raise ValueError("max_gap_factor and max_workers must be positive")
        if self.detrend_window_hz <= 0 or self.detrend_polyorder < 1:
            raise ValueError("detrend settings are invalid")
        if self.detrend_target_guard_bins < 1:
            raise ValueError("detrend_target_guard_bins must be positive")
        if not 0 < self.target_peak_search_half_width_hz <= (
            self.background_exclusion_hz[1] - self.background_exclusion_hz[0]
        ) / 2:
            raise ValueError("target peak search half width is invalid")
        if self.detrended_prominence_threshold_db < 0:
            raise ValueError("detrended prominence threshold must be non-negative")
        if self.block_detrended_prominence_threshold_db < 0:
            raise ValueError("block detrended prominence threshold must be non-negative")
        if self.global_robust_z_threshold < 0 or self.block_global_robust_z_threshold < 0:
            raise ValueError("global robust z thresholds must be non-negative")
        if not 0 <= self.global_peak_percentile_threshold <= 1:
            raise ValueError("global_peak_percentile_threshold must be in [0, 1]")
        if self.global_min_frequency_hz < 0:
            raise ValueError("global_min_frequency_hz must be non-negative")
        if not 0 < self.peak_width_min_bins <= self.peak_width_max_bins:
            raise ValueError("peak width bin limits are invalid")

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(slots=True)
class SpectrumBlockResult:
    block_index: int
    start_time: str
    end_time: str
    data_points: int
    peak_frequency_hz: float | None = None
    peak_to_background_db: float | None = None
    peak_prominence: float | None = None
    peak_width_hz: float | None = None
    is_local_maximum: bool = False
    detrended_peak_frequency_hz: float | None = None
    detrended_prominence_db: float | None = None
    detrended_peak_width_bins: float | None = None
    global_robust_z: float | None = None
    failed_rules: list[str] = field(default_factory=list)
    exceeds_exploratory_threshold: bool = False
    valid: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(slots=True)
class SpectrumAnalysisResult:
    pv_name: str
    source_file: str = ""
    server: str = ""
    data_points: int = 0
    duration_seconds: float | None = None
    sampling_rate_hz: float | None = None
    nyquist_frequency_hz: float | None = None
    target_frequency_hz: float = 0.152
    target_period_seconds: float | None = None
    welch_window_seconds: float = 300.0
    actual_welch_window_seconds: float | None = None
    frequency_resolution_hz: float | None = None
    peak_frequency_hz: float | None = None
    peak_frequency_error_hz: float | None = None
    raw_peak_to_background_db: float | None = None
    clean_peak_to_background_db: float | None = None
    peak_prominence: float | None = None
    peak_prominence_db: float | None = None
    peak_width_hz: float | None = None
    is_local_maximum: bool = False
    detrended_peak_frequency_hz: float | None = None
    detrended_peak_frequency_error_hz: float | None = None
    detrended_prominence_db: float | None = None
    detrended_peak_width_hz: float | None = None
    detrended_peak_width_bins: float | None = None
    global_robust_z: float | None = None
    global_peak_percentile: float | None = None
    global_peak_rank: int | None = None
    global_peak_count: int = 0
    transient_count: int = 0
    transient_ratio: float = 0.0
    block_results: list[SpectrumBlockResult] = field(default_factory=list)
    stable_block_count: int = 0
    total_block_count: int = 0
    stability_ratio: float | None = None
    classification: Classification = "invalid"
    decision_reason: str = ""
    failed_rules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_message: str | None = None
    plot_data: dict[str, Any] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(slots=True)
class BatchAnalysisResult:
    total_pv_count: int
    completed_pv_count: int
    invalid_pv_count: int
    stable_candidate_count: int
    observation_count: int
    reject_count: int
    elapsed_seconds: float
    results: list[SpectrumAnalysisResult]
    config: dict[str, Any] = field(default_factory=dict)
    batch_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)
