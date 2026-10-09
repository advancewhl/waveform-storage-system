"""Shared user-facing status policy for spectrum results."""

from __future__ import annotations

from typing import Literal


DisplayStatus = Literal[
    "clear_peak",
    "probable_peak",
    "watch",
    "no_significant_peak",
    "unanalysable",
]

DISPLAY_STATUS_LABELS: dict[DisplayStatus, str] = {
    "clear_peak": "谱峰明显",
    "probable_peak": "谱峰较明显",
    "watch": "观测项",
    "no_significant_peak": "无显著谱峰",
    "unanalysable": "无法分析",
}


def display_status_for(classification: str, *, overall_hit: bool = False) -> DisplayStatus:
    """Map the compatible machine classification and evidence to one display status.

    ``classification`` retains its existing machine contract. ``overall_hit`` means
    the profile's complete overall-peak criteria passed, excluding repeatability.
    """
    if classification == "invalid":
        return "unanalysable"
    if classification == "stable_candidate":
        return "clear_peak"
    if classification == "observation":
        return "probable_peak" if overall_hit else "watch"
    if classification == "reject":
        return "no_significant_peak"
    return "unanalysable"
