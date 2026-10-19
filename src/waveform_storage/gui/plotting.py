"""Polished plots that consume only an AlignmentResult."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator


BLUE = "#458cc4"
GRAY = "#7f919e"
INK = "#111111"
MUTED = "#2f2f2f"
GRID = "#c8c8c8"


def plot_alignment_result(result, selected_index):
    """Plot one bunchID across all valid BPM devices."""

    if result.bunch_ids.size == 0:
        raise ValueError("the alignment result contains no bunchID")
    index = min(max(int(selected_index), 0), result.bunch_ids.size - 1)
    valid = result.valid_mask[:, index]
    devices = np.asarray(result.device_ids, dtype=object)[valid]
    positions = np.arange(len(devices))
    marker_size = 4 if len(devices) <= 80 else 2
    figure, axis = _new_figure()
    axis.plot(
        devices,
        result.x[:, index][valid],
        marker="o",
        markersize=marker_size,
        label="X position",
        linewidth=2.1,
        color=BLUE,
    )
    axis.plot(
        devices,
        result.y[:, index][valid],
        marker="s",
        markersize=marker_size,
        label="Y position",
        linewidth=2.1,
        color=GRAY,
    )
    axis.set_title(
        f"bunchID {int(result.bunch_ids[index])}",
        loc="left",
        pad=9,
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    # Every device series remains in the plot. Labels are sparse and wrapped
    # at a semantic separator so they stay readable without consuming most of
    # the canvas height; the selector and details expose the full device list.
    label_count = min(len(devices), 6)
    tick_indices = np.unique(
        np.linspace(0, len(devices) - 1, label_count, dtype=int)
    )
    axis.set_xticks(tick_indices)
    axis.set_xticklabels(
        [_wrap_device_label(str(value)) for value in devices[tick_indices]],
        rotation=0,
        ha="center",
        fontsize=7.5,
        color=MUTED,
    )
    axis.set_xlabel(
        f"BPM device  ·  {len(devices)} visible",
        color=INK,
        fontsize=9,
        fontweight="semibold",
    )
    axis.set_ylabel(
        "Position value",
        color=INK,
        fontsize=9,
        fontweight="semibold",
    )
    axis.yaxis.set_major_locator(MaxNLocator(nbins=8))
    _finish_axis(axis, grid_axis="y")
    # Recalculate margins when Qt resizes the canvas. The full signal remains
    # plotted; sparse labels retain the first and last device identifiers.
    figure.set_layout_engine("constrained")
    return figure


def plot_device_waveform(result, device_id: str):
    """Plot one BPM's X/Y waveform against its aligned bunchID values."""

    if result.bunch_ids.size == 0:
        raise ValueError("the alignment result contains no bunchID")
    try:
        row = result.device_ids.index(device_id)
    except ValueError as exc:
        raise ValueError(f"unknown device: {device_id}") from exc
    valid = result.valid_mask[row]
    if not np.any(valid):
        raise ValueError(f"device has no valid aligned samples: {device_id}")

    bunch_ids = np.asarray(
        [int(value) for value in result.bunch_ids[valid]],
        dtype=object,
    )
    x_values = result.x[row, valid]
    y_values = result.y[row, valid]
    marker_step = max(1, len(bunch_ids) // 18)
    figure, axis = _new_figure()
    axis.plot(
        bunch_ids,
        x_values,
        label="X position",
        color=BLUE,
        linewidth=2.0,
        marker="o",
        markevery=marker_step,
        markersize=4,
    )
    axis.plot(
        bunch_ids,
        y_values,
        label="Y position",
        color=GRAY,
        linewidth=2.0,
        marker="s",
        markevery=marker_step,
        markersize=4,
    )
    axis.set_title(
        f"BPM {device_id} waveform",
        loc="left",
        pad=9,
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    tick_count = min(7, len(bunch_ids))
    tick_indices = np.unique(
        np.linspace(0, len(bunch_ids) - 1, tick_count, dtype=int)
    )
    tick_values = [int(bunch_ids[index]) for index in tick_indices]
    axis.set_xticks(tick_values)
    axis.set_xticklabels(
        [str(value) for value in tick_values],
        rotation=14,
        ha="right",
        fontsize=8,
        color=MUTED,
    )
    axis.set_xlabel(
        f"BunchID  ·  {len(bunch_ids)} aligned samples",
        color=INK,
        fontsize=9,
        fontweight="semibold",
    )
    axis.set_ylabel(
        "Position value",
        color=INK,
        fontsize=9,
        fontweight="semibold",
    )
    axis.yaxis.set_major_locator(MaxNLocator(nbins=8))
    axis.axhline(0, color="#4b4b4b", linewidth=1.1, alpha=0.8)
    _finish_axis(axis, grid_axis="both")
    figure.set_layout_engine("constrained")
    return figure


def _new_figure():
    figure, axis = plt.subplots(
        figsize=(11, 6),
        dpi=110,
        facecolor="#ffffff",
    )
    axis.set_facecolor("#ffffff")
    return figure, axis


def _wrap_device_label(device_id: str) -> str:
    """Wrap a device label while retaining every identifier character."""
    semantic_marker = "_BI_"
    marker_index = device_id.find(semantic_marker)
    if marker_index > 0:
        split = marker_index + len(semantic_marker)
        return device_id[:split] + "\n" + device_id[split:]
    if len(device_id) <= 14:
        return device_id
    separators = [index for index, char in enumerate(device_id) if char == "_"]
    if not separators:
        return device_id
    split = min(separators, key=lambda index: abs(index + 1 - len(device_id) / 2))
    return device_id[:split + 1] + "\n" + device_id[split + 1:]


def _finish_axis(axis, *, grid_axis: str) -> None:
    axis.grid(
        axis=grid_axis,
        linestyle=(0, (3, 4)),
        linewidth=0.7,
        color=GRID,
        alpha=0.65,
    )
    axis.tick_params(
        axis="both",
        colors=INK,
        labelsize=9,
        length=4,
        width=1.0,
        direction="out",
        pad=7,
    )
    for spine in axis.spines.values():
        spine.set_visible(True)
        spine.set_color("#303030")
        spine.set_linewidth(1.05)
    legend = axis.legend(
        loc="upper right",
        ncol=2,
        frameon=True,
        fancybox=False,
        borderpad=0.65,
        columnspacing=1.4,
        handlelength=2.4,
        fontsize=9,
    )
    legend.get_frame().set_facecolor("#ffffff")
    legend.get_frame().set_edgecolor("#303030")
    legend.get_frame().set_linewidth(0.9)
    legend.get_frame().set_alpha(1.0)
    for text in legend.get_texts():
        text.set_color(INK)
