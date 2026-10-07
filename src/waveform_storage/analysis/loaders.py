"""Archiver and HDF5 adapters with explicit, auditable time bases."""

from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path, PurePosixPath
import json
import math
import re
from zipfile import ZipFile

import h5py
import numpy as np
import pandas as pd

from .models import AnalysisRequest, ChannelSeries


TEXT_SUFFIXES = {".csv", ".tsv", ".txt"}
TIME_COLUMNS = ("smpl_time", "timestamp", "datetime", "time", "date_time", "ts")
VALUE_COLUMNS = ("float_val", "num_val", "value", "val", "reading")
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_CHANNELS = 10_000
MAX_POINTS = 5_000_000
MAX_DATASET_ELEMENTS = 8_000_000


def _string(value):
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _failure(name, source, exc):
    return ChannelSeries(name, np.array([]), np.array([]), source,
                         error=f"读取失败：{type(exc).__name__}：{exc}")


def _archiver_frame(data, name, source):
    normalized = {str(col).lstrip("\ufeff").strip().lower(): col for col in data.columns}
    time_col = next((normalized[key] for key in TIME_COLUMNS if key in normalized), None)
    if time_col is None:
        raise ValueError("找不到时间列；请使用 time 或 smpl_time")
    if len(data) > MAX_POINTS:
        raise ValueError("单通道点数超过限制")
    if pd.api.types.is_numeric_dtype(data[time_col]):
        raise ValueError("Archiver 时间列须为带日期的时间戳，不能使用行号或未注明单位的数字")
    timestamps = pd.to_datetime(data[time_col], utc=True, errors="coerce", format="mixed")
    if not timestamps.notna().any():
        raise ValueError("时间列没有有效时间戳")
    origin = timestamps.min()
    times = (timestamps - origin).dt.total_seconds().to_numpy(float)
    values = None
    used = []
    for key in VALUE_COLUMNS:
        if key in normalized:
            current = pd.to_numeric(data[normalized[key]], errors="coerce")
            values = current if values is None else values.combine_first(current)
            used.append(key)
    if values is None:
        candidates = [(col, pd.to_numeric(data[col], errors="coerce"))
                      for col in data if col != time_col]
        candidates = [(col, v) for col, v in candidates if v.notna().any()]
        if len(candidates) != 1:
            raise ValueError("请提供唯一数值列，如 value、float_val 或 num_val")
        col, values = candidates[0]
        used = [str(col)]
    warnings = []
    stamp_text = data[time_col].astype(str)
    has_timezone = stamp_text.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True)
    if not has_timezone.all():
        warnings.append("部分时间戳缺少时区，本次按 UTC 解释；频谱间隔仍由时间戳计算")
    if len(used) > 1:
        warnings.append("数值按列优先级回退：" + "、".join(used))
    return ChannelSeries(name, times, values.to_numpy(float), source,
                         time_basis="utc", utc_origin=origin.isoformat(),
                         metadata={"value_columns": used, "time_column": str(time_col),
                                   "timezone_assumed_utc": not bool(has_timezone.all())},
                         warnings=warnings)


def _read_text(payload, suffix):
    return pd.read_csv(payload, sep="\t" if suffix == ".tsv" else None,
                       engine="python", encoding="utf-8-sig")


def _pv_name(path):
    match = re.match(r"^\d+_(\d+)_(\d+)_(.+)\.txt$", path.name)
    if match:
        names = sorted(path.parent.glob("pv_name_*.txt"))
        if names:
            index = (int(match[1]) - 1) * 500 + int(match[2]) - 1
            records = names[0].read_text(encoding="utf-8-sig").splitlines()
            if 0 <= index < len(records) and records[index].strip():
                return records[index].strip()
        return match[3]
    return path.stem


def _skip_member(name):
    stem = PurePosixPath(name).stem.lower()
    return stem.startswith("pv_name_") or stem in {
        "readme", "ordered_pvs", "bpm_order", "single_value_pvs"
    }


def load_archiver(path):
    if path.suffix.lower() != ".zip":
        yield _archiver_frame(_read_text(path, path.suffix.lower()), _pv_name(path), str(path))
        return
    with ZipFile(path) as archive:
        total = 0
        members = []
        for member in archive.infolist():
            p = PurePosixPath(member.filename)
            if member.is_dir() or p.suffix.lower() not in TEXT_SUFFIXES or _skip_member(p.name):
                continue
            if p.is_absolute() or ".." in p.parts:
                raise ValueError("ZIP 包含不安全路径")
            total += member.file_size
            if member.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("ZIP 解压后大小超过限制")
            members.append(member)
        if len(members) > MAX_CHANNELS:
            raise ValueError("ZIP 文件数量超过限制")
        for member in sorted(members, key=lambda x: x.filename):
            p = PurePosixPath(member.filename)
            label = str(path) + "!/" + member.filename
            try:
                frame = _read_text(BytesIO(archive.read(member)), p.suffix.lower())
                yield _archiver_frame(frame, p.stem, label)
            except Exception as exc:
                yield _failure(p.stem, label, exc)


def _sampling_rate(metadata, explicit):
    recorded = metadata.get("sample_rate_hz", metadata.get("frequency_hz"))
    if recorded is not None:
        if isinstance(recorded, bool) or not isinstance(recorded, (int, float)):
            raise ValueError("文件中的采样率字段无效")
        if not math.isfinite(recorded) or recorded <= 0:
            raise ValueError("文件中的采样率必须为正数")
    if recorded is not None and explicit is not None and not math.isclose(
        float(recorded), explicit, rel_tol=1e-6
    ):
        raise ValueError("填写的采样率与文件记录不一致，请核对该次采集")
    rate = float(recorded) if recorded is not None else explicit
    if rate is None:
        raise ValueError("该 HDF5 没有采样率记录，请填写该次采集的实际采样率")
    return rate, "file_metadata" if recorded is not None else "user_confirmed"


def _id_times(ids, sample_rate):
    ids = np.asarray(ids)
    if ids.ndim != 1 or not np.issubdtype(ids.dtype, np.integer):
        raise ValueError("bunchID 必须为一维整数数组")
    if len(ids) < 2 or len(ids) > MAX_POINTS or np.any(ids == 0):
        raise ValueError("bunchID 点数无效或含零 ID，请核对原始数据")
    differences = [int(b) - int(a) for a, b in zip(ids[:-1], ids[1:])]
    if min(differences) <= 0:
        raise ValueError("bunchID 必须唯一并严格递增，不能压缩或重排波形时基")
    step = Counter(differences).most_common(1)[0][0]
    if any(difference % step for difference in differences):
        raise ValueError("bunchID 间隔不是同一基本步长的整数倍，无法建立采样时间轴")
    slots = np.array([int(value) - int(ids[0]) for value in ids], dtype=float) / step
    return slots / sample_rate, step


def _waveform_series(ids, x, y, devices, source, rate, rate_source,
                     mask=None, metadata=None):
    if not devices or len(set(devices)) != len(devices) or any(not name for name in devices):
        raise ValueError("设备名称为空或重复")
    if x.shape != y.shape or x.ndim != 2 or x.shape[0] != len(devices):
        raise ValueError("设备数量与 X/Y 数据形状不一致")
    if x.shape[1] != len(ids):
        raise ValueError("X/Y 与 bunchID 长度不一致")
    if mask is not None and mask.shape != x.shape:
        raise ValueError("有效点掩码与 X/Y 形状不一致")
    times, step = _id_times(ids, rate)
    for index, device in enumerate(devices):
        double_zero = (x[index] == 0) & (y[index] == 0)
        common = dict(metadata or {})
        common.update(bunch_id_step=step, bunch_id_first=int(ids[0]),
                      bunch_id_last=int(ids[-1]), sampling_rate_source=rate_source,
                      double_zero_count=int(double_zero.sum()))
        notes = ["时间轴为相对时间；采样率来自" + (
            "文件记录，需结合采集语义核对" if rate_source == "file_metadata"
            else "用户填写的该次采集频率")]
        if double_zero.any():
            notes.append(f"X/Y 同时为零的点有 {int(double_zero.sum()):,} 个；默认保留")
        for values, plane in [(x[index], "X"), (y[index], "Y")]:
            series = ChannelSeries(f"{device}_{plane}", times.copy(),
                                   np.asarray(values, dtype=float), source,
                                   sample_rate_hz=rate,
                                   valid_mask=mask[index].copy() if mask is not None else None,
                                   metadata={**common, "plane": plane}, warnings=list(notes))
            series.metadata["double_zero_mask"] = double_zero
            yield series


def _read_dataset(h5, key):
    dataset = h5[key]
    if dataset.size > MAX_DATASET_ELEMENTS or (
        dataset.ndim > 0 and dataset.shape[-1] > MAX_POINTS
    ):
        raise ValueError("HDF5 解码后的点数超过限制")
    if dataset.dtype.kind not in "biuf":
        raise ValueError("HDF5 数据类型不是数值或布尔类型")
    return np.asarray(dataset)


def load_hdf5(path, explicit_rate):
    with h5py.File(path, "r") as h5:
        metadata = {}
        for key in ("capture_metadata_json", "metadata_json"):
            if key in h5.attrs:
                value = json.loads(_string(h5.attrs[key]))
                if not isinstance(value, dict):
                    raise ValueError("HDF5 元数据必须为 JSON 对象")
                metadata.update(value)
        for key in ("sample_rate_hz", "frequency_hz"):
            if key in h5.attrs:
                value = h5.attrs[key]
                metadata[key] = value.item() if isinstance(value, np.generic) else value
        rate, rate_source = _sampling_rate(metadata, explicit_rate)
        if _string(h5.attrs.get("schema_name", "")) == "site-user-waveform-alignment":
            if int(h5.attrs.get("schema_version", -1)) != 2:
                raise ValueError("不支持该 HDF5 schema 版本")
            ids = _read_dataset(h5, "bunch_ids")
            devices = [_string(value) for value in h5["device_ids"]]
            x, y = _read_dataset(h5, "x"), _read_dataset(h5, "y")
            mask = _read_dataset(h5, "valid_mask")
            if mask.dtype.kind != "b":
                raise ValueError("v2 有效点掩码必须为布尔类型")
        else:
            raw_ids = _read_dataset(h5, "BunchIDData")
            if raw_ids.ndim != 2 or raw_ids.shape[0] != 1:
                raise ValueError("BunchIDData 形状必须为 (1, N)")
            ids = raw_ids[0]
            data = _read_dataset(h5, "Data")
            names = h5.attrs.get("Device")
            devices = [_string(names)] if isinstance(names, (str, bytes)) else (
                [_string(value) for value in names] if names is not None else [])
            if not devices or len(set(devices)) != len(devices):
                raise ValueError("缺少有效的设备名称或设备名称重复")
            if data.ndim != 2 or data.shape[0] != 2 * len(devices):
                raise ValueError("Data 行数与设备数不一致")
            if len(devices) > 1:
                for index, name in enumerate(devices):
                    for prefix, plane in [("Data[0:]_", "X"), ("Data[1:]_", "Y")]:
                        if _string(h5.attrs.get(prefix + str(index), "")) != name + "_" + plane:
                            raise ValueError("合并文件的 X/Y 行标签与设备列表不一致")
            x, y, mask = data[0::2], data[1::2], None
    yield from _waveform_series(ids, x, y, devices, str(path), rate, rate_source,
                                mask=mask, metadata=metadata)


def load_request(request: AnalysisRequest):
    if request.source_type == "alignment":
        rate, rate_source = _sampling_rate({}, request.sample_rate_hz)
        result = request.alignment
        yield from _waveform_series(result.bunch_ids, result.x, result.y,
                                    result.device_ids, "history-alignment", rate,
                                    rate_source, mask=result.valid_mask)
        return
    suffixes = {".h5", ".hdf5"} if request.source_type == "hdf5" else TEXT_SUFFIXES | {".zip"}
    files = []
    for value in request.paths:
        p = Path(value).expanduser().resolve(strict=True)
        files.extend(sorted(item for item in p.rglob("*")
                            if item.is_file() and item.suffix.lower() in suffixes
                            and not _skip_member(item.name)) if p.is_dir() else [p])
    files = list(dict.fromkeys(files))
    if len(files) > MAX_CHANNELS or sum(p.stat().st_size for p in files) > MAX_TOTAL_BYTES:
        raise ValueError("输入数量或总大小超过限制")
    if not files:
        raise ValueError("输入目录没有对应格式的数据文件")
    count = 0
    for path in files:
        try:
            if path.suffix.lower() not in suffixes or path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("输入格式不匹配或文件大小超过限制")
            loader = load_hdf5(path, request.sample_rate_hz) if request.source_type == "hdf5" else load_archiver(path)
            for series in loader:
                count += 1
                if count > MAX_CHANNELS:
                    raise ValueError("通道数量超过限制")
                yield series
        except Exception as exc:
            yield _failure(path.stem, str(path), exc)
