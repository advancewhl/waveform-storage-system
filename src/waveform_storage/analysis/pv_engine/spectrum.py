"""Single-PV numerical spectral analysis."""

from __future__ import annotations

import warnings as python_warnings
from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, peak_prominences, peak_widths, savgol_filter

from ..numerics import welch_density
from .classification import classify_spectrum
from .models import (
    PVRecord,
    SpectrumAnalysisConfig,
    SpectrumAnalysisResult,
    SpectrumBlockResult,
)


@dataclass(slots=True)
class _PeakMetrics:
    frequency_hz: float
    frequency_error_hz: float
    power: float
    background_power: float
    peak_to_background_db: float
    prominence: float | None
    prominence_db: float | None
    width_hz: float | None


@dataclass(slots=True)
class _PsdResult:
    frequency: np.ndarray
    psd: np.ndarray
    nperseg: int
    actual_window_seconds: float
    delta_f_hz: float


@dataclass(slots=True)
class _DetrendedPeakMetrics:
    frequency_hz: float
    frequency_error_hz: float
    is_local_maximum: bool
    prominence_db: float | None
    width_hz: float | None
    width_bins: float | None
    global_robust_z: float | None
    global_peak_percentile: float | None
    global_peak_rank: int | None
    global_peak_count: int
    baseline_db: np.ndarray
    residual_db: np.ndarray


def make_invalid_result(
    record: PVRecord,
    config: SpectrumAnalysisConfig,
    message: str,
    *,
    warnings: list[str] | None = None,
) -> SpectrumAnalysisResult:
    """Build a structured invalid result for data or worker failures."""
    merged_warnings = list(dict.fromkeys([*record.warnings, *(warnings or [])]))
    return SpectrumAnalysisResult(
        pv_name=record.pv_name,
        source_file=record.source_file,
        server=record.server,
        data_points=min(len(record.timestamps), len(record.values)),
        target_frequency_hz=config.target_frequency_hz,
        target_period_seconds=1.0 / config.target_frequency_hz,
        welch_window_seconds=config.welch_window_seconds,
        classification="invalid",
        warnings=merged_warnings,
        error_message=message,
    )


def _attach_quality_metrics(
    result: SpectrumAnalysisResult,
    data_points: int,
    duration_seconds: float | None = None,
    sampling_rate_hz: float | None = None,
) -> SpectrumAnalysisResult:
    result.data_points = data_points
    result.duration_seconds = duration_seconds
    result.sampling_rate_hz = sampling_rate_hz
    result.nyquist_frequency_hz = (
        sampling_rate_hz / 2.0 if sampling_rate_hz is not None else None
    )
    return result


def _uniform_grid(elapsed: np.ndarray, median_dt: float) -> np.ndarray:
    count = int(np.floor(elapsed[-1] / median_dt + 1e-9)) + 1
    return np.arange(count, dtype=float) * median_dt


def _clean_transients(
    values: np.ndarray, fs: float, config: SpectrumAnalysisConfig
) -> tuple[np.ndarray, np.ndarray, float | None, list[str]]:
    notes: list[str] = []
    window = max(3, int(round(config.transient_rolling_window_seconds * fs)))
    if window % 2 == 0:
        window += 1
    baseline = (
        pd.Series(values)
        .rolling(window, center=True, min_periods=1)
        .median()
        .to_numpy(float)
    )
    residual = values - baseline
    center = float(np.median(residual))
    mad = float(np.median(np.abs(residual - center)))
    robust_sigma = 1.4826 * mad
    if not np.isfinite(robust_sigma) or robust_sigma <= np.finfo(float).eps:
        notes.append("MAD 为零或不可用，未执行瞬态标记")
        return values.copy(), np.zeros(len(values), dtype=bool), None, notes

    flagged = np.abs(residual - center) > config.transient_sigma_threshold * robust_sigma
    cleaned = values.copy()
    good = ~flagged
    if flagged.any():
        if good.sum() < 2:
            notes.append("非瞬态点不足，无法插值；cleaned signal 回退为原信号")
            return values.copy(), flagged, robust_sigma, notes
        index = np.arange(len(values), dtype=float)
        cleaned[flagged] = np.interp(index[flagged], index[good], values[good])
    return cleaned, flagged, robust_sigma, notes


def _compute_psd(values: np.ndarray, fs: float, config: SpectrumAnalysisConfig) -> _PsdResult:
    result = welch_density(values, fs, config.welch_window_seconds, config.welch_overlap_ratio)
    return _PsdResult(result.frequency, result.psd, result.nperseg,
                      result.actual_window_seconds, result.delta_f_hz)


def _peak_metrics(
    spectrum: _PsdResult, config: SpectrumAnalysisConfig
) -> tuple[_PeakMetrics, list[str]]:
    """Calculate the unchanged V1 local peak/background metrics."""
    notes: list[str] = []
    frequency, psd, delta_f = spectrum.frequency, spectrum.psd, spectrum.delta_f_hz
    target_mask = np.abs(frequency - config.target_frequency_hz) <= delta_f + 1e-12
    target_indices = np.flatnonzero(target_mask)
    if target_indices.size == 0:
        raise ValueError("目标频率搜索范围内没有频率 bin")
    peak_index = int(target_indices[np.argmax(psd[target_indices])])
    peak_power = float(psd[peak_index])

    band_low, band_high = config.local_band_hz
    exclude_low, exclude_high = config.background_exclusion_hz
    background_mask = (
        (frequency >= band_low)
        & (frequency <= band_high)
        & ~((frequency >= exclude_low) & (frequency <= exclude_high))
    )
    background_values = psd[background_mask]
    background_values = background_values[np.isfinite(background_values) & (background_values > 0)]
    if background_values.size == 0:
        raise ValueError("局部背景频带没有有效 PSD")
    background_power = float(np.median(background_values))
    if not np.isfinite(peak_power) or peak_power <= 0:
        raise ValueError("目标谱峰功率无效")
    peak_to_background_db = float(10.0 * np.log10(peak_power / background_power))

    prominence: float | None = None
    prominence_db: float | None = None
    width_hz: float | None = None
    try:
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("ignore")
            prominences, _, _ = peak_prominences(psd, [peak_index])
            widths, _, left_ips, right_ips = peak_widths(psd, [peak_index], rel_height=0.5)
        candidate_prominence = float(prominences[0])
        if candidate_prominence > 0 and float(widths[0]) > 0:
            prominence = candidate_prominence
            base_power = peak_power - prominence
            if base_power > 0:
                prominence_db = float(10.0 * np.log10(peak_power / base_power))
            bin_positions = np.arange(len(frequency), dtype=float)
            left_hz = float(np.interp(left_ips[0], bin_positions, frequency))
            right_hz = float(np.interp(right_ips[0], bin_positions, frequency))
            width_hz = right_hz - left_hz
        else:
            notes.append("目标候选不是可测量 prominence/width 的局部极大值")
    except Exception as exc:
        notes.append(f"谱峰特征计算失败：{type(exc).__name__}: {exc}")

    return (
        _PeakMetrics(
            frequency_hz=float(frequency[peak_index]),
            frequency_error_hz=abs(float(frequency[peak_index]) - config.target_frequency_hz),
            power=peak_power,
            background_power=background_power,
            peak_to_background_db=peak_to_background_db,
            prominence=prominence,
            prominence_db=prominence_db,
            width_hz=width_hz,
        ),
        notes,
    )


def _savgol_window_bins(
    point_count: int, delta_f: float, config: SpectrumAnalysisConfig
) -> int:
    requested = max(5, int(round(config.detrend_window_hz / delta_f)))
    if requested % 2 == 0:
        requested += 1
    minimum = config.detrend_polyorder + 2
    if minimum % 2 == 0:
        minimum += 1
    requested = max(requested, minimum)
    maximum = point_count if point_count % 2 == 1 else point_count - 1
    return min(requested, maximum)


def _detrended_peak_metrics(
    spectrum: _PsdResult, config: SpectrumAnalysisConfig
) -> tuple[_DetrendedPeakMetrics, list[str]]:
    """Measure a real target peak after removing a slow PSD trend in dB space."""
    notes: list[str] = []
    frequency = spectrum.frequency
    floor = np.finfo(float).tiny
    psd_db = 10.0 * np.log10(np.maximum(spectrum.psd, floor))
    delta_f = spectrum.delta_f_hz
    guard_hz = config.detrend_target_guard_bins * delta_f
    guard = np.abs(frequency - config.target_frequency_hz) <= guard_hz + 1e-12

    fit_values = psd_db.copy()
    fit_source = np.flatnonzero(~guard & np.isfinite(fit_values))
    if fit_source.size < config.detrend_polyorder + 3:
        raise ValueError("去趋势可用频率点不足")
    guard_indices = np.flatnonzero(guard)
    if guard_indices.size:
        fit_values[guard_indices] = np.interp(
            guard_indices.astype(float), fit_source.astype(float), fit_values[fit_source]
        )
    window = _savgol_window_bins(len(fit_values), delta_f, config)
    if window <= config.detrend_polyorder:
        raise ValueError("频率点不足以建立 Savitzky-Golay 趋势")
    baseline_db = savgol_filter(
        fit_values,
        window_length=window,
        polyorder=config.detrend_polyorder,
        mode="interp",
    )
    residual_db = psd_db - baseline_db

    target_mask = (
        np.abs(frequency - config.target_frequency_hz)
        <= config.target_peak_search_half_width_hz + 1e-12
    )
    target_indices = np.flatnonzero(target_mask)
    if target_indices.size == 0:
        raise ValueError("目标频率搜索范围内没有频率 bin")
    all_peaks, _ = find_peaks(residual_db)
    target_peaks = all_peaks[target_mask[all_peaks]]
    is_local_maximum = target_peaks.size > 0
    if is_local_maximum:
        # Prefer the best isolated peak in the physical target band. This
        # tolerates Welch-bin quantisation around 0.152 Hz without accepting a
        # monotonic shoulder, because candidates still must be real peaks.
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("ignore")
            target_prominences, _, _ = peak_prominences(residual_db, target_peaks)
        peak_index = int(target_peaks[np.argmax(target_prominences)])
    else:
        peak_index = int(target_indices[np.argmax(residual_db[target_indices])])
        notes.append("去趋势后目标附近不是真实局部极大值")

    prominence_db: float | None = None
    width_bins: float | None = None
    width_hz: float | None = None
    if is_local_maximum:
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("ignore")
            prominences, _, _ = peak_prominences(residual_db, [peak_index])
            widths, _, _, _ = peak_widths(residual_db, [peak_index], rel_height=0.5)
        if float(prominences[0]) > 0 and float(widths[0]) > 0:
            prominence_db = float(prominences[0])
            width_bins = float(widths[0])
            width_hz = width_bins * delta_f
        else:
            notes.append("去趋势后目标谱峰的 prominence/width 不可测")

    global_mask = (
        np.isfinite(residual_db)
        & (frequency >= config.global_min_frequency_hz)
        & ~guard
    )
    global_values = residual_db[global_mask]
    global_robust_z: float | None = None
    if global_values.size:
        center = float(np.median(global_values))
        mad = float(np.median(np.abs(global_values - center)))
        scale = 1.4826 * mad
        if np.isfinite(scale) and scale > np.finfo(float).eps:
            global_robust_z = float((residual_db[peak_index] - center) / scale)
        else:
            notes.append("去趋势全谱 MAD 为零或不可用")

    valid_peaks = all_peaks[
        (frequency[all_peaks] >= config.global_min_frequency_hz)
        & np.isfinite(residual_db[all_peaks])
    ]
    peak_count = int(valid_peaks.size)
    percentile: float | None = None
    rank: int | None = None
    if is_local_maximum and peak_count:
        heights = residual_db[valid_peaks]
        target_height = float(residual_db[peak_index])
        percentile = float(np.mean(heights <= target_height + 1e-12))
        rank = 1 + int(np.sum(heights > target_height + 1e-12))

    return (
        _DetrendedPeakMetrics(
            frequency_hz=float(frequency[peak_index]),
            frequency_error_hz=abs(float(frequency[peak_index]) - config.target_frequency_hz),
            is_local_maximum=is_local_maximum,
            prominence_db=prominence_db,
            width_hz=width_hz,
            width_bins=width_bins,
            global_robust_z=global_robust_z,
            global_peak_percentile=percentile,
            global_peak_rank=rank,
            global_peak_count=peak_count,
            baseline_db=baseline_db,
            residual_db=residual_db,
        ),
        notes,
    )


def _analyze_blocks(
    cleaned: np.ndarray,
    uniform_times: pd.DatetimeIndex,
    fs: float,
    config: SpectrumAnalysisConfig,
) -> tuple[list[SpectrumBlockResult], int, int, list[str]]:
    notes: list[str] = []
    block_points = max(4, int(round(config.stability_block_seconds * fs)))
    complete_blocks = len(cleaned) // block_points
    remainder = len(cleaned) % block_points
    if complete_blocks == 0:
        notes.append("数据不足一个完整 stability block")
    elif remainder:
        notes.append(f"末尾 {remainder} 个点不足完整 stability block，未计入稳定性")

    results: list[SpectrumBlockResult] = []
    stable_count = 0
    valid_count = 0
    for block_index in range(complete_blocks):
        start = block_index * block_points
        stop = start + block_points
        item = SpectrumBlockResult(
            block_index=block_index + 1,
            start_time=uniform_times[start].isoformat(),
            end_time=uniform_times[stop - 1].isoformat(),
            data_points=block_points,
        )
        try:
            spectrum = _compute_psd(cleaned[start:stop], fs, config)
            metrics, metric_notes = _peak_metrics(spectrum, config)
            item.peak_frequency_hz = metrics.frequency_hz
            item.peak_to_background_db = metrics.peak_to_background_db
            item.peak_prominence = metrics.prominence
            item.peak_width_hz = metrics.width_hz
            local_hit = metrics.peak_to_background_db >= config.exploratory_peak_threshold_db
            if config.algorithm_version == "v2":
                try:
                    detrended, detrended_notes = _detrended_peak_metrics(spectrum, config)
                    item.is_local_maximum = detrended.is_local_maximum
                    item.detrended_peak_frequency_hz = detrended.frequency_hz
                    item.detrended_prominence_db = detrended.prominence_db
                    item.detrended_peak_width_bins = detrended.width_bins
                    item.global_robust_z = detrended.global_robust_z
                    item.warnings.extend(detrended_notes)
                    if not detrended.is_local_maximum:
                        item.failed_rules.append("real_local_maximum")
                    if detrended.prominence_db is None or (
                        detrended.prominence_db
                        < config.block_detrended_prominence_threshold_db
                    ):
                        item.failed_rules.append("detrended_prominence")
                    if detrended.width_bins is None or not (
                        config.peak_width_min_bins
                        <= detrended.width_bins
                        <= config.peak_width_max_bins
                    ):
                        item.failed_rules.append("peak_width")
                    if detrended.global_robust_z is None or (
                        detrended.global_robust_z
                        < config.block_global_robust_z_threshold
                    ):
                        item.failed_rules.append("global_robust_z")
                except Exception as exc:
                    item.failed_rules.append("detrended_metrics")
                    item.warnings.append(
                        f"block V2去趋势指标无效：{type(exc).__name__}: {exc}"
                    )
                if not local_hit:
                    item.failed_rules.insert(0, "local_6db")
                item.exceeds_exploratory_threshold = not item.failed_rules
            else:
                item.exceeds_exploratory_threshold = local_hit
            item.valid = True
            item.warnings.extend(metric_notes)
            valid_count += 1
            stable_count += int(item.exceeds_exploratory_threshold)
        except Exception as exc:
            item.warnings.append(f"block 频谱无效：{type(exc).__name__}: {exc}")
        results.append(item)
    return results, stable_count, valid_count, notes


def analyze_single_pv(
    record: PVRecord, config: SpectrumAnalysisConfig | None = None
) -> SpectrumAnalysisResult:
    """Analyze one PV without writing files, plotting, or mutating its input."""
    config = config or SpectrumAnalysisConfig()
    notes = list(record.warnings)
    if len(record.timestamps) != len(record.values):
        return make_invalid_result(record, config, "timestamps 与 values 长度不一致")

    try:
        times = (pd.to_timedelta(pd.Series(record.timestamps), unit="s", errors="coerce")
                 if record.relative_time else
                 pd.to_datetime(pd.Series(record.timestamps), utc=True, errors="coerce"))
        values = pd.to_numeric(pd.Series(record.values), errors="coerce")
    except Exception as exc:
        return make_invalid_result(record, config, f"输入转换失败：{type(exc).__name__}: {exc}")
    valid = times.notna() & values.notna() & np.isfinite(values.to_numpy(float))
    removed = int((~valid).sum())
    if removed:
        notes.append(f"移除 {removed} 个时间或数值无效的点")
    frame = pd.DataFrame({"time": times[valid], "value": values[valid].astype(float)})
    frame = frame.sort_values("time", kind="stable")
    duplicates = int(frame["time"].duplicated(keep=False).sum())
    if duplicates:
        notes.append(f"发现 {duplicates} 个重复时间戳记录，已按时间戳求均值")
        frame = frame.groupby("time", as_index=False, sort=True)["value"].mean()
    frame = frame.reset_index(drop=True)

    if len(frame) < config.minimum_data_points:
        return _attach_quality_metrics(
            make_invalid_result(
                record,
                config,
                f"有效数据点不足：{len(frame)} < {config.minimum_data_points}",
                warnings=notes,
            ),
            len(frame),
        )
    elapsed = (frame["time"] - frame["time"].iloc[0]).dt.total_seconds().to_numpy(float)
    duration = float(elapsed[-1])
    dt = np.diff(elapsed)
    positive_dt = dt[np.isfinite(dt) & (dt > 0)]
    if positive_dt.size == 0:
        return _attach_quality_metrics(
            make_invalid_result(record, config, "无法计算有效采样间隔", warnings=notes),
            len(frame),
            duration,
        )
    median_dt = float(np.median(positive_dt))
    fs = 1.0 / median_dt
    nyquist = fs / 2.0
    if duration < config.minimum_duration_seconds:
        return _attach_quality_metrics(
            make_invalid_result(
                record,
                config,
                f"数据时长不足：{duration:.3f} s < {config.minimum_duration_seconds:.3f} s",
                warnings=notes,
            ),
            len(frame),
            duration,
            fs,
        )
    if not np.isfinite(fs) or nyquist < config.target_frequency_hz:
        return _attach_quality_metrics(
            make_invalid_result(
                record,
                config,
                f"Nyquist {nyquist:.6g} Hz 无法覆盖目标频率 {config.target_frequency_hz:g} Hz",
                warnings=notes,
            ),
            len(frame),
            duration,
            fs,
        )
    if nyquist < config.local_band_hz[1]:
        notes.append("Nyquist 未覆盖完整局部背景频带，背景估计可能不可用")
    max_gap = float(np.max(positive_dt))
    if max_gap > config.max_gap_factor * median_dt:
        notes.append(
            f"最大采样间隔 {max_gap:.3f} s 超过中位间隔的 {config.max_gap_factor:g} 倍，线性插值可能跨越长缺口"
        )

    grid = _uniform_grid(elapsed, median_dt)
    raw = np.interp(grid, elapsed, frame["value"].to_numpy(float))
    start_time = frame["time"].iloc[0]
    uniform_times = (pd.TimedeltaIndex(start_time + pd.to_timedelta(grid, unit="s"))
                     if record.relative_time else
                     pd.DatetimeIndex(start_time + pd.to_timedelta(grid, unit="s")))
    cleaned, transient_flags, _robust_sigma, transient_notes = _clean_transients(raw, fs, config)
    notes.extend(transient_notes)

    try:
        raw_spectrum = _compute_psd(raw, fs, config)
        clean_spectrum = _compute_psd(cleaned, fs, config)
        raw_metrics, raw_notes = _peak_metrics(raw_spectrum, config)
        clean_metrics, clean_notes = _peak_metrics(clean_spectrum, config)
        notes.extend(f"raw: {item}" for item in raw_notes)
        notes.extend(f"cleaned: {item}" for item in clean_notes)
    except Exception as exc:
        result = make_invalid_result(
            record, config, f"整体频谱分析失败：{type(exc).__name__}: {exc}", warnings=notes
        )
        return _attach_quality_metrics(result, len(frame), duration, fs)
    detrended_metrics: _DetrendedPeakMetrics | None = None
    if config.algorithm_version == "v2":
        try:
            detrended_metrics, detrended_notes = _detrended_peak_metrics(
                clean_spectrum, config
            )
            notes.extend(f"V2: {item}" for item in detrended_notes)
        except Exception as exc:
            notes.append(f"V2去趋势指标无效：{type(exc).__name__}: {exc}")

    requested_points = int(round(config.welch_window_seconds * fs))
    if clean_spectrum.nperseg < requested_points:
        notes.append(
            f"数据短于请求的 Welch 窗口，nperseg 缩短为 {clean_spectrum.nperseg}"
        )

    blocks, stable_count, valid_block_count, block_notes = _analyze_blocks(
        cleaned, uniform_times, fs, config
    )
    notes.extend(block_notes)
    stability_ratio = (
        stable_count / valid_block_count if valid_block_count > 0 else None
    )
    result = SpectrumAnalysisResult(
        pv_name=record.pv_name,
        source_file=record.source_file,
        server=record.server,
        data_points=len(frame),
        duration_seconds=duration,
        sampling_rate_hz=fs,
        nyquist_frequency_hz=nyquist,
        target_frequency_hz=config.target_frequency_hz,
        target_period_seconds=1.0 / config.target_frequency_hz,
        welch_window_seconds=config.welch_window_seconds,
        actual_welch_window_seconds=clean_spectrum.actual_window_seconds,
        frequency_resolution_hz=clean_spectrum.delta_f_hz,
        peak_frequency_hz=clean_metrics.frequency_hz,
        peak_frequency_error_hz=clean_metrics.frequency_error_hz,
        raw_peak_to_background_db=raw_metrics.peak_to_background_db,
        clean_peak_to_background_db=clean_metrics.peak_to_background_db,
        peak_prominence=clean_metrics.prominence,
        peak_prominence_db=clean_metrics.prominence_db,
        peak_width_hz=clean_metrics.width_hz,
        is_local_maximum=(
            detrended_metrics.is_local_maximum if detrended_metrics is not None else False
        ),
        detrended_peak_frequency_hz=(
            detrended_metrics.frequency_hz if detrended_metrics is not None else None
        ),
        detrended_peak_frequency_error_hz=(
            detrended_metrics.frequency_error_hz if detrended_metrics is not None else None
        ),
        detrended_prominence_db=(
            detrended_metrics.prominence_db if detrended_metrics is not None else None
        ),
        detrended_peak_width_hz=(
            detrended_metrics.width_hz if detrended_metrics is not None else None
        ),
        detrended_peak_width_bins=(
            detrended_metrics.width_bins if detrended_metrics is not None else None
        ),
        global_robust_z=(
            detrended_metrics.global_robust_z if detrended_metrics is not None else None
        ),
        global_peak_percentile=(
            detrended_metrics.global_peak_percentile
            if detrended_metrics is not None
            else None
        ),
        global_peak_rank=(
            detrended_metrics.global_peak_rank if detrended_metrics is not None else None
        ),
        global_peak_count=(
            detrended_metrics.global_peak_count if detrended_metrics is not None else 0
        ),
        transient_count=int(transient_flags.sum()),
        transient_ratio=float(transient_flags.mean()),
        block_results=blocks,
        stable_block_count=stable_count,
        total_block_count=valid_block_count,
        stability_ratio=stability_ratio,
        warnings=notes,
    )
    result.classification = classify_spectrum(result, config)
    keep_plot_data = (
        not config.plot_stable_candidates_and_bpm_only
        or result.classification == "stable_candidate"
        or "bpm" in result.pv_name.lower()
    )
    if config.enable_plots and keep_plot_data:
        result.plot_data = {
            "uniform_times": uniform_times,
            "raw": raw,
            "cleaned": cleaned,
            "transient_flags": transient_flags,
            "raw_frequency": raw_spectrum.frequency,
            "raw_psd": raw_spectrum.psd,
            "clean_frequency": clean_spectrum.frequency,
            "clean_psd": clean_spectrum.psd,
        }
        if detrended_metrics is not None:
            result.plot_data["clean_baseline_db"] = detrended_metrics.baseline_db
            result.plot_data["clean_residual_db"] = detrended_metrics.residual_db
    return result
