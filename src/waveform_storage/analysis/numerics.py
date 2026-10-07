"""A single physical-window Welch implementation for both analysis modes."""

from dataclasses import dataclass

import numpy as np
from scipy.signal import welch


@dataclass(frozen=True)
class PowerSpectrum:
    frequency: np.ndarray
    psd: np.ndarray
    nperseg: int
    actual_window_seconds: float
    delta_f_hz: float


def welch_density(values, sample_rate_hz, window_seconds, overlap=0.5):
    values = np.asarray(values, dtype=float)
    if sample_rate_hz <= 0 or not np.isfinite(values).all():
        raise ValueError("采样率或频谱输入无效")
    nperseg = min(len(values), max(4, int(round(window_seconds * sample_rate_hz))))
    if nperseg < 4:
        raise ValueError("Welch 至少需要 4 个数据点")
    noverlap = min(nperseg - 1, int(round(overlap * nperseg)))
    frequency, psd = welch(values, fs=sample_rate_hz, window="hann",
                           nperseg=nperseg, noverlap=noverlap,
                           detrend="linear", scaling="density")
    if len(frequency) < 2 or not np.isfinite(psd).all():
        raise ValueError("Welch 计算结果无效")
    return PowerSpectrum(frequency, psd, nperseg, nperseg / sample_rate_hz,
                         float(frequency[1] - frequency[0]))
