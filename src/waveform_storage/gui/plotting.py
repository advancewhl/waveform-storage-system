"""Plotting that consumes only an AlignmentResult."""

import matplotlib.pyplot as plt
import numpy as np


def plot_alignment_result(result, selected_index):
    if result.bunch_ids.size == 0:
        raise ValueError("the alignment result contains no bunchID")
    index = min(max(int(selected_index), 0), result.bunch_ids.size - 1)
    valid = result.valid_mask[:, index]
    devices = np.asarray(result.device_ids, dtype=object)[valid]
    figure, axis = plt.subplots(figsize=(10, 6))
    axis.plot(devices, result.x[:, index][valid], marker="o", label="X", linewidth=2)
    axis.plot(devices, result.y[:, index][valid], marker="s", label="Y", linewidth=2)
    axis.set_title(f"BunchID:{result.bunch_ids[index]} <-----> Index:{index}")
    axis.set_xticks(range(len(devices)))
    axis.set_xticklabels(devices, rotation=90)
    axis.set_xlabel("Devicename")
    axis.set_ylabel("Value")
    axis.legend()
    axis.grid(True, linestyle="--", alpha=0.7)
    figure.tight_layout()
    return figure
