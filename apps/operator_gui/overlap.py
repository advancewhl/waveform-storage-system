"""Thin facade preserving the historical ``overlap`` import path."""

from waveform_storage.application.retrieval_service import (
    RetrievalService,
    StorageQuery,
)
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.plotting import plot_alignment_result
from waveform_storage.io.hdf5_io import read_single_device_hdf5
from waveform_storage.legacy.overlap_adapter import (
    alignment_bunch_id, alignment_summary, create_hdf5, dataprocess,
    get_overlap_indices_matrix, get_para, main_dataprocess, plotbunchID,
    read_h5, save_newfile, selectConfirm, showBunchID,
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


class _FacadeStorageGateway:
    def find_files(self, query):
        settings = AppSettings.from_env()
        files, devices = remote_find_h5_files(
            settings.storage_host,
            settings.storage_user,
            settings.storage_password,
            query.remote_path,
            query.time_sec,
            settings.storage_port,
        )
        return list(zip(files, devices))

    def read_series(self, pairs, reader):
        return read_h5_device_series(pairs)


_retrieval_service = RetrievalService(_FacadeStorageGateway())


def clear_retrieval_cache():
    _retrieval_service.clear_cache()


def main_alignment_process(remote_path, runID, time_sec, pv_list_sorted,
                           parameters=None):
    query = StorageQuery(
        remote_path=remote_path,
        run_id=runID,
        time_sec=time_sec,
        device_ids=tuple(pv_list_sorted),
    )
    return _retrieval_service.retrieve(query, parameters)


def save_alignment_result(result, runID, time_sec, save_path,
                          export_format="auto"):
    query = StorageQuery(
        "legacy-export",
        runID,
        time_sec,
        tuple(result.device_ids),
    )
    return str(
        _retrieval_service.export(
            result,
            query,
            save_path,
            export_format,
        )
    )
