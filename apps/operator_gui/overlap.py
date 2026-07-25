"""Thin facade preserving the historical ``overlap`` import path."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.plotting import plot_alignment_result
from waveform_storage.io.hdf5_io import (
    read_single_device_hdf5,
    write_alignment_v2,
    write_legacy_merged_hdf5,
)
from waveform_storage.legacy.overlap_adapter import (
    alignment_bunch_id, alignment_summary, create_hdf5, dataprocess,
    get_overlap_indices_matrix, get_para, main_dataprocess, plotbunchID,
    read_h5, save_newfile, selectConfirm, showBunchID,
)
from waveform_storage.processing.alignment import (
    AlignmentParameters, SkippedDevice, align_devices,
)
from waveform_storage.transport import ssh_storage as _storage

paramiko = _storage.paramiko
tempfile = _storage.tempfile


class AlignmentResultCache:
    def __init__(self):
        self._key = self._result = None

    def clear(self):
        self._key = self._result = None

    def get_or_load(self, key, loader):
        if self._key != key or self._result is None:
            self._result, self._key = loader(), key
        return self._result


def remote_save_timesec_h5_files(hostname, username, password, remote_path,
                                  local_path, time_sec, port=22):
    try:
        return _storage.save_time_files(
            hostname, username, password, remote_path, local_path, time_sec, port
        )
    except _storage.SshStorageError:
        print("something wrong in connect")


def remote_search_time_sec(hostname, username, password, remote_path):
    try:
        return _storage.list_time_fields(hostname, username, password, remote_path)
    except _storage.SshStorageError as exc:
        print(f"连接错误: {exc}")
        return []


def remote_find_h5_files(hostname, username, password, remote_path, time_sec,
                         port=22):
    try:
        pairs = _storage.find_h5_files(
            hostname, username, password, remote_path, time_sec, port
        )
    except _storage.SshStorageError as exc:
        print(f"连接错误: {exc}")
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


def read_h5_device_series(pairs):
    return _storage.download_device_series(
        pairs, reader=read_single_device_hdf5, settings=AppSettings.from_env()
    )


def main_alignment_process(remote_path, runID, time_sec, pv_list_sorted,
                           parameters=None):
    settings = AppSettings.from_env()
    files, devices = remote_find_h5_files(
        settings.storage_host, settings.storage_user, settings.storage_password,
        remote_path, time_sec, settings.storage_port,
    )
    order = {device: index for index, device in enumerate(pv_list_sorted)}
    known, skipped = [], []
    for remote_file, device in zip(files, devices):
        if device in order:
            known.append((remote_file, device))
        else:
            skipped.append(SkippedDevice(
                device, "unknown_device",
                "device is absent from the configured full device list",
            ))
    known.sort(key=lambda pair: order[pair[1]])
    records, read_skipped = read_h5_device_series(known)
    result = align_devices(
        records, parameters or AlignmentParameters(mode="strict")
    )
    return replace(result, skipped_devices=(
        tuple(skipped) + tuple(read_skipped) + result.skipped_devices
    ))


def save_alignment_result(result, runID, time_sec, save_path,
                          export_format="auto"):
    selected = str(export_format).strip().lower()
    if selected not in {"auto", "legacy", "v2"}:
        raise ValueError("export_format must be one of: auto, legacy, v2")
    if selected == "auto":
        selected = "legacy" if np.all(result.valid_mask) else "v2"
    if selected == "legacy":
        path = Path(save_path) / f"{runID}_{time_sec}_BPMdata.h5"
        return str(write_legacy_merged_hdf5(path, result))
    path = Path(save_path) / f"{runID}_{time_sec}_BPMdata_v2.h5"
    return str(write_alignment_v2(
        path, result, metadata={"run_id": runID, "time_sec": time_sec}
    ))
