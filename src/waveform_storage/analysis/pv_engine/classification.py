"""Configurable classification policy, kept separate from PSD computation."""

from __future__ import annotations

from .models import Classification, SpectrumAnalysisConfig, SpectrumAnalysisResult


def v2_failed_rules(
    result: SpectrumAnalysisResult, config: SpectrumAnalysisConfig
) -> list[str]:
    """Return auditable V2 gate failures in stable display order."""
    failures: list[str] = []
    if result.clean_peak_to_background_db is None or (
        result.clean_peak_to_background_db < config.exploratory_peak_threshold_db
    ):
        failures.append("local_6db")
    if not result.is_local_maximum:
        failures.append("real_local_maximum")
    if result.detrended_prominence_db is None or (
        result.detrended_prominence_db < config.detrended_prominence_threshold_db
    ):
        failures.append("detrended_prominence")
    width = result.detrended_peak_width_bins
    if width is None or not config.peak_width_min_bins <= width <= config.peak_width_max_bins:
        failures.append("peak_width")
    if result.global_robust_z is None or (
        result.global_robust_z < config.global_robust_z_threshold
    ):
        failures.append("global_robust_z")
    if result.global_peak_percentile is None or (
        result.global_peak_percentile < config.global_peak_percentile_threshold
    ):
        failures.append("global_peak_percentile")
    if result.stable_block_count < config.stable_min_block_count:
        failures.append("stable_block_count")
    if result.stability_ratio is None or result.stability_ratio < config.stable_min_ratio:
        failures.append("stability_ratio")
    return failures


def classify_spectrum(
    result: SpectrumAnalysisResult, config: SpectrumAnalysisConfig
) -> Classification:
    """Classify a valid numeric result using exploratory thresholds."""
    if result.error_message is not None or result.clean_peak_to_background_db is None:
        return "invalid"

    overall_hit = (
        result.clean_peak_to_background_db >= config.exploratory_peak_threshold_db
    )
    if config.algorithm_version == "v2":
        result.failed_rules = v2_failed_rules(result, config)
        if not result.failed_rules:
            result.decision_reason = f"通过局部 {config.exploratory_peak_threshold_db:g} dB、去趋势谱峰形状、全局显著性和时间稳定性条件"
            return "stable_candidate"
        spectral_failures = {
            "local_6db",
            "real_local_maximum",
            "detrended_prominence",
            "peak_width",
            "global_robust_z",
            "global_peak_percentile",
        }
        spectral_hits = len(spectral_failures.difference(result.failed_rules))
        if overall_hit or result.stable_block_count > 0 or spectral_hits >= 4:
            result.decision_reason = "部分V2条件通过，需观察：" + ",".join(result.failed_rules)
            return "observation"
        result.decision_reason = "V2 关键谱峰形状或显著性条件未通过：" + ",".join(result.failed_rules)
        return "reject"

    stable = (
        overall_hit
        and result.stable_block_count >= config.stable_min_block_count
        and result.stability_ratio is not None
        and result.stability_ratio >= config.stable_min_ratio
    )
    if stable:
        result.decision_reason = f"通过 V1 局部 {config.exploratory_peak_threshold_db:g} dB 和时间稳定性条件"
        return "stable_candidate"
    if overall_hit or result.stable_block_count > 0:
        result.decision_reason = "V1局部或时间块部分命中"
        return "observation"
    result.decision_reason = f"V1 局部 {config.exploratory_peak_threshold_db:g} dB 和时间块均未命中"
    return "reject"
