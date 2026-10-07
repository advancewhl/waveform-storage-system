"""Quality gates and analysis policies independent of input source and Qt."""

from __future__ import annotations

from pathlib import Path
import json
import math

import numpy as np
from scipy.signal import find_peaks, peak_prominences

from .models import AnalysisConfig, ChannelResult, ChannelSeries
from .numerics import welch_density


ALGORITHM_VERSION = "unified-spectrum-1.0"
MAX_RESAMPLED_POINTS = 5_000_000


def _prepare(series, config, result):
    times = np.asarray(series.times, dtype=float)
    values = np.asarray(series.values, dtype=float)
    if times.ndim != 1 or values.ndim != 1 or len(times) != len(values):
        raise ValueError("时间轴与数值长度不一致")
    result.quality["raw_points"] = len(values)
    valid = np.isfinite(times) & np.isfinite(values)
    if series.valid_mask is not None:
        if series.valid_mask.shape != values.shape:
            raise ValueError("有效点掩码形状不一致")
        valid &= series.valid_mask
    if config.zero_policy == "mask_double_zero":
        zero_mask = series.metadata.get("double_zero_mask")
        if zero_mask is not None:
            valid &= ~zero_mask
            result.warnings.append("本次显式排除 X/Y 同时为零的点")
    result.quality["removed_points"] = int((~valid).sum())
    times, values = times[valid], values[valid]
    if len(values) < config.minimum_points:
        raise ValueError(f"有效点数不足：{len(values)} < {config.minimum_points}")
    order = np.argsort(times, kind="stable")
    times, values = times[order], values[order]
    unique, inverse, counts = np.unique(times, return_inverse=True, return_counts=True)
    duplicates = len(times) - len(unique)
    result.quality["duplicate_points"] = duplicates
    if duplicates:
        values = np.bincount(inverse, weights=values) / counts
        times = unique
        result.warnings.append(f"重复时间戳合并为均值：{duplicates:,} 个")
    if len(times) < config.minimum_points:
        raise ValueError("合并重复时间戳后有效点数不足")
    result.quality["first_valid_offset_seconds"] = float(times[0])
    times = times - times[0]
    intervals = np.diff(times)
    median_dt = float(np.median(intervals))
    if median_dt <= 0:
        raise ValueError("无法确定采样间隔")
    rate = series.sample_rate_hz or 1.0 / median_dt
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("采样率无效")
    result.sample_rate_hz = rate
    result.data_points = len(values)
    result.duration_seconds = float(times[-1])
    result.quality.update(valid_points=len(values), median_interval_seconds=median_dt,
                          interval_mad_seconds=float(np.median(abs(intervals - median_dt))),
                          max_gap_seconds=float(intervals.max()), nyquist_hz=rate / 2)
    if rate / 2 <= config.search_high_hz:
        raise ValueError("Nyquist 频率不能完整覆盖搜索频带")
    if rate / 2 < config.background_high_hz:
        raise ValueError("Nyquist 频率不能完整覆盖背景频带，请调整参数")
    if times[-1] < config.minimum_seconds:
        raise ValueError(f"连续记录时长不足：{times[-1]:.3f} 秒 < {config.minimum_seconds:g} 秒")
    if intervals.max() > config.max_gap_factor / rate + 1e-9:
        raise ValueError("存在长采样缺口；本次未跨缺口插值，请选择连续数据段")
    if np.ptp(values) <= np.finfo(float).eps * max(1.0, float(np.max(abs(values)))):
        raise ValueError("常值序列无法分析频谱")
    if result.quality["interval_mad_seconds"] > 0.1 / rate:
        result.warnings.append("采样抖动较大，等间隔插值可能影响高频结果")
    count = int(np.floor(times[-1] * rate + 1e-7)) + 1
    if count > MAX_RESAMPLED_POINTS:
        raise ValueError("重采样网格过大，请核对采样率和时间单位")
    grid = np.arange(count, dtype=float) / rate
    result.quality["resampled_points"] = count
    result.quality["short_gap_fill_points"] = max(0, count - len(values))
    return grid, np.interp(grid, times, values), rate


def _band_metrics(spectrum, config):
    f, p = spectrum.frequency, spectrum.psd
    selected = (f >= config.search_low_hz) & (f <= config.search_high_hz)
    background = ((f >= config.background_low_hz) & (f <= config.background_high_hz)
                  & ~selected & (p > 0))
    if not selected.any() or background.sum() < 3:
        raise ValueError("窗口过短，搜索或背景频带内的频率点不足")
    indexes = np.flatnonzero(selected)
    index = int(indexes[np.argmax(p[indexes])])
    baseline = max(float(np.median(p[background])), np.finfo(float).tiny)
    power = max(float(p[index]), np.finfo(float).tiny)
    real_peak = index in find_peaks(p)[0]
    prominence_db = 0.0
    if real_peak:
        prominence = float(peak_prominences(p, [index])[0][0])
        prominence_db = 10 * (math.log10(power) - math.log10(max(power - prominence, np.finfo(float).tiny)))
    return {"peak_hz": float(f[index]), "peak_to_background_db": 10 * (math.log10(power) - math.log10(baseline)),
            "is_local_maximum": bool(real_peak), "prominence_db": prominence_db}


def _high_frequency(values, rate, config, result):
    spectrum = welch_density(values, rate, config.welch_seconds)
    overall = _band_metrics(spectrum, config)
    result.frequency, result.psd = spectrum.frequency, spectrum.psd
    result.resolution_hz = spectrum.delta_f_hz
    result.peak_hz = overall["peak_hz"]
    result.peak_to_background_db = overall["peak_to_background_db"]
    block_size = max(4, int(round(config.block_seconds * rate)))
    for index in range(len(values) // block_size):
        start, stop = index * block_size, (index + 1) * block_size
        item = {"block_index": index + 1, "start_seconds": start / rate,
                "end_seconds": (stop - 1) / rate, "data_points": block_size,
                "hit": False, "valid": False}
        try:
            psd = welch_density(values[start:stop], rate, config.welch_seconds)
            metrics = _band_metrics(psd, config)
            item.update(metrics, valid=True)
            item["hit"] = bool(metrics["peak_to_background_db"] >= config.threshold_db
                               and metrics["is_local_maximum"]
                               and metrics["prominence_db"] >= 3.0
                               and abs(metrics["peak_hz"] - result.peak_hz) <= config.peak_match_hz)
        except ValueError as exc:
            item["invalid_reason"] = str(exc)
        result.blocks.append(item)
    result.block_count = sum(bool(b["valid"]) for b in result.blocks)
    result.block_hits = sum(bool(b["hit"]) for b in result.blocks)
    result.block_ratio = result.block_hits / result.block_count if result.block_count else None
    if len(values) % block_size:
        result.warnings.append("末尾不足完整分块，未计入重复性判断")
    overall_hit = (overall["peak_to_background_db"] >= config.threshold_db
                   and overall["is_local_maximum"] and overall["prominence_db"] >= 3.0)
    repeated = (result.block_hits >= config.minimum_blocks
                and result.block_ratio is not None
                and result.block_ratio >= config.minimum_block_ratio)
    result.classification = "stable_candidate" if overall_hit and repeated else (
        "observation" if overall_hit or result.block_hits else "reject")
    result.reason = {
        "stable_candidate": "整体峰显著，且在本次记录的独立秒级分块中重复出现",
        "observation": "整体或部分分块命中，需增加采集复核",
        "reject": "本次未通过所设频谱门槛",
    }[result.classification]
    result.details = {**overall, "actual_welch_seconds": spectrum.actual_window_seconds,
                      "scope": "within_capture", "prominence_threshold_db": 3.0}


def _low_frequency(grid, values, rate, config, result):
    from .pv_engine.models import PVRecord, SpectrumAnalysisConfig
    from .pv_engine.spectrum import analyze_single_pv

    path = Path(__file__).with_name("pv_engine") / "algorithm-presets.json"
    params = json.loads(path.read_text(encoding="utf-8"))[config.profile]
    params.update(algorithm_version="v2" if config.profile == "low_v2" else "v1",
                  target_frequency_hz=(config.search_low_hz + config.search_high_hz) / 2,
                  local_band_hz=(config.background_low_hz, config.background_high_hz),
                  background_exclusion_hz=(config.search_low_hz, config.search_high_hz),
                  target_peak_search_half_width_hz=(config.search_high_hz - config.search_low_hz) / 2,
                  welch_window_seconds=config.welch_seconds,
                  stability_block_seconds=config.block_seconds,
                  exploratory_peak_threshold_db=config.threshold_db,
                  stable_min_block_count=config.minimum_blocks,
                  stable_min_ratio=config.minimum_block_ratio,
                  minimum_data_points=config.minimum_points,
                  minimum_duration_seconds=config.minimum_seconds,
                  enable_plots=True)
    record = PVRecord(result.channel, grid, values, result.source, relative_time=True)
    analysis = analyze_single_pv(record, SpectrumAnalysisConfig(**params))
    result.classification = analysis.classification
    result.reason = analysis.error_message or analysis.decision_reason
    result.peak_hz = analysis.peak_frequency_hz
    result.peak_to_background_db = analysis.clean_peak_to_background_db
    result.resolution_hz = analysis.frequency_resolution_hz
    result.block_hits, result.block_count = analysis.stable_block_count, analysis.total_block_count
    result.block_ratio = analysis.stability_ratio
    result.warnings.extend(analysis.warnings)
    result.blocks = [block.to_dict() for block in analysis.block_results]
    result.details = {"original_metrics": analysis.to_dict(), "effective_parameters": params,
                      "scope": "within_record", "time_labels": "relative_duration"}
    if analysis.plot_data:
        result.frequency = analysis.plot_data["clean_frequency"]
        result.psd = analysis.plot_data["clean_psd"]


def analyze_channel(series: ChannelSeries, config: AnalysisConfig) -> ChannelResult:
    metadata = {key: value for key, value in series.metadata.items() if not isinstance(value, np.ndarray)}
    result = ChannelResult(series.channel, series.source, time_basis=series.time_basis,
                           utc_origin=series.utc_origin, unit=series.unit,
                           metadata=metadata, warnings=list(series.warnings))
    try:
        if series.error:
            raise ValueError(series.error)
        grid, values, rate = _prepare(series, config, result)
        if config.profile == "high_band":
            _high_frequency(values, rate, config, result)
        else:
            _low_frequency(grid, values, rate, config, result)
    except Exception as exc:
        result.classification = "invalid"
        result.reason = f"{type(exc).__name__}：{exc}"
    return result
