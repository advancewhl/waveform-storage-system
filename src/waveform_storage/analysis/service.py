"""One bounded async job interface for GUI, command-line, and HTTP clients."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import uuid4
import csv
import hashlib
import json
import os
import shutil
import traceback
import platform
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np

from .models import AnalysisRequest, AnalysisRun, ChannelResult
from .status import DISPLAY_STATUS_LABELS


class AnalysisCancelled(Exception):
    """A cancellation request reached a safe data/channel boundary."""


def _owner_lock(stream, acquire):
    if os.name == "nt":
        import msvcrt
        if acquire and os.fstat(stream.fileno()).st_size == 0:
            stream.write("\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return str(value) if isinstance(value, Path) else value


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_json_value(value), ensure_ascii=False, indent=2,
                                     allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_csv(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False)
                             if isinstance(value, (dict, list)) else value
                             for key, value in _json_value(row).items()})


def _digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run_analysis(request: AnalysisRequest, output_root, *, job_id=None,
                 cancel=None, progress=None) -> AnalysisRun:
    from .engine import ALGORITHM_VERSION, analyze_channel
    from .loaders import MAX_CHANNELS, load_request
    import pandas as pd
    import scipy

    cancel = cancel or Event()
    job_id = job_id or str(uuid4())
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stage = root / (job_id + ".working")
    directory = root / job_id
    stage.mkdir()
    try:
        channels = []
        for item in load_request(request):
            if cancel.is_set():
                raise AnalysisCancelled()
            if len(channels) >= MAX_CHANNELS:
                raise ValueError("通道数量超过限制")
            channels.append(analyze_channel(item, request.config))
            if progress:
                progress(len(channels), 0, item.channel)
        if not channels:
            raise ValueError("没有可读取的通道")
        if progress:
            progress(len(channels), len(channels), "保存结果")
        if cancel.is_set():
            raise AnalysisCancelled()
        sources = []
        for value in sorted({c.source.split("!/")[0] for c in channels}):
            path = Path(value)
            if path.is_file():
                sources.append({"path": str(path), "bytes": path.stat().st_size,
                                "sha256": _digest(path)})
            else:
                sources.append({"path": value, "kind": "in_memory_alignment"})
        manifest = {"job_id": job_id, "created_at": _now(), "algorithm_version": ALGORITHM_VERSION,
                    "result_schema_version": 2, "display_status_schema_version": 1,
                    "display_status_labels": DISPLAY_STATUS_LABELS,
                    "classification_compatibility": (
                        "classification retains its legacy values; display_status carries the "
                        "current five-state presentation code"
                    ),
                    "source_type": request.source_type, "source_files": sources,
                    "sample_rate_hz_supplied": request.sample_rate_hz,
                    "config": asdict(request.config), "beam_status": "unknown",
                    "interpretation": "statistical_spectrum_candidate",
                    "duration_definition": "last_sample_minus_first_sample",
                    "runtime_versions": {"python": platform.python_version(),
                                         "numpy": np.__version__, "pandas": pd.__version__,
                                         "scipy": scipy.__version__},
                    "channel_count": len(channels)}
        write_json(stage / "run-manifest.json", manifest)
        write_json(stage / "results.json", [
            {**c.summary(), "blocks": c.blocks, "details": c.details} for c in channels])
        flat = []
        for c in channels:
            flat.append({key: value for key, value in c.summary().items()
                         if key not in {"quality", "metadata", "warnings"}})
        _write_csv(stage / "spectrum-summary.csv", flat)
        _write_csv(stage / "data-quality.csv", [
            {"channel": c.channel, "source": c.source, "status": c.classification,
             "display_status": c.display_status,
             "reason": c.reason, **c.quality, "warnings": c.warnings} for c in channels])
        _write_csv(stage / "spectrum-blocks.csv", [
            {"channel": c.channel, "source": c.source, **block} for c in channels for block in c.blocks])
        arrays = {}
        for i, c in enumerate(channels):
            arrays[f"frequency_{i}"] = c.frequency
            arrays[f"psd_{i}"] = c.psd
        np.savez_compressed(stage / "spectra.npz", **arrays)
        lines = ["# 频谱分析结果", "", f"运行 ID：`{job_id}`。",
                 f"分析模板：`{request.config.profile}`；通道数：{len(channels):,}。", "",
                 "结果为统计筛选。高频重复性仅适用于本次记录，束流状态和物理原因需要另行核对。", "",
                 "| 通道 | 状态 | 谱峰（Hz） | 谱峰背景比（dB） | 分块命中 |",
                 "| --- | --- | ---: | ---: | ---: |"]
        for c in channels:
            name = c.channel.replace("|", "\\|").replace("\n", " ")
            peak = "—" if c.peak_hz is None else f"{c.peak_hz:.6g}"
            snr = "—" if c.peak_to_background_db is None else f"{c.peak_to_background_db:.2f}"
            lines.append(
                f"| {name} | {DISPLAY_STATUS_LABELS[c.display_status]} | {peak} | "
                f"{snr} | {c.block_hits}/{c.block_count} |"
            )
        lines.extend(["", "配置与来源见 `run-manifest.json`；无效原因见 `data-quality.csv`。",
                      "HDF5 的相对时间与填入的采样率均记录在来源字段中。"])
        (stage / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with ZipFile(stage / "results.zip", "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(stage.iterdir()):
                if path.name != "results.zip":
                    archive.write(path, path.name)
        if cancel.is_set():
            raise AnalysisCancelled()
        os.replace(stage, directory)
        return AnalysisRun(job_id, directory, channels, manifest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


class AnalysisJobs:
    """Bounded jobs; state survives restart, unfinished jobs become explicit failures."""

    def __init__(self, output_root):
        self.output_root = Path(output_root).expanduser().resolve()
        self.states = self.output_root / "jobs"
        self.states.mkdir(parents=True, exist_ok=True)
        self._owner_file = (self.states / "manager.lock").open("a+")
        try:
            _owner_lock(self._owner_file, True)
        except OSError as exc:
            self._owner_file.close()
            raise RuntimeError("该结果目录已有分析服务使用，请使用原窗口或选择另一个输出目录") from exc
        self._lock = Lock()
        self._jobs = {}
        self._results = {}
        self._events = {}
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spectrum-job")
        for path in self.states.glob("*.json"):
            state = json.loads(path.read_text(encoding="utf-8"))
            if state["status"] in {"accepted", "running"}:
                state.update(status="failed", error="分析服务已重启，未完成任务未自动重跑")
                write_json(path, state)
            self._jobs[state["job_id"]] = state

    def submit(self, request):
        with self._lock:
            if self._closed:
                raise RuntimeError("分析服务已关闭")
            pending = sum(s["status"] in {"accepted", "running"} for s in self._jobs.values())
            if pending >= 8:
                raise ValueError("分析任务队列已满")
            job_id = str(uuid4())
            state = {"job_id": job_id, "status": "accepted", "completed": 0, "total": 0,
                     "channel": "", "created_at": _now(), "error": None,
                     "source_type": request.source_type, "profile": request.config.profile}
            self._jobs[job_id] = state
            self._events[job_id] = Event()
            write_json(self.states / (job_id + ".json"), state)
            self._executor.submit(self._execute, job_id, request)
        return job_id

    def _update(self, job_id, **fields):
        with self._lock:
            self._jobs[job_id].update(fields, updated_at=_now())
            write_json(self.states / (job_id + ".json"), self._jobs[job_id])

    def _execute(self, job_id, request):
        try:
            if self._events[job_id].is_set():
                raise AnalysisCancelled()
            self._update(job_id, status="running")
            def progress(completed, total, channel):
                self._update(job_id, completed=completed, total=total, channel=channel)
            result = run_analysis(request, self.output_root, job_id=job_id,
                                  cancel=self._events[job_id], progress=progress)
            with self._lock:
                self._results[job_id] = result
                while len(self._results) > 4:
                    del self._results[next(iter(self._results))]
            counts = {name: sum(c.classification == name for c in result.channels)
                      for name in ["invalid", "stable_candidate", "observation", "reject"]}
            display_counts = {
                name: sum(c.display_status == name for c in result.channels)
                for name in DISPLAY_STATUS_LABELS
            }
            self._update(job_id, status="succeeded", result_directory=str(result.directory),
                         counts=counts, display_counts=display_counts)
        except AnalysisCancelled:
            self._update(job_id, status="cancelled")
        except Exception as exc:
            self._update(job_id, status="failed", error=f"{type(exc).__name__}：{exc}",
                         traceback=traceback.format_exc())

    def get(self, job_id):
        with self._lock:
            return json.loads(json.dumps(self._jobs[job_id]))

    def result(self, job_id):
        with self._lock:
            if job_id in self._results:
                return self._results[job_id]
            if self._jobs[job_id]["status"] != "succeeded":
                raise ValueError("该任务没有成功完成的结果")
        directory = self.output_root / job_id
        manifest = json.loads((directory / "run-manifest.json").read_text(encoding="utf-8"))
        records = json.loads((directory / "results.json").read_text(encoding="utf-8"))
        channels = []
        with np.load(directory / "spectra.npz", allow_pickle=False) as arrays:
            for index, row in enumerate(records):
                channels.append(ChannelResult(**row, frequency=arrays[f"frequency_{index}"],
                                              psd=arrays[f"psd_{index}"]))
        return AnalysisRun(job_id, directory, channels, manifest)

    def cancel(self, job_id):
        with self._lock:
            if self._jobs[job_id]["status"] in {"accepted", "running"}:
                self._events[job_id].set()

    def shutdown(self, wait=True):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for event in self._events.values():
                event.set()
        # Queued jobs run only far enough to persist their cancellation status.
        def finish():
            self._executor.shutdown(wait=True)
            _owner_lock(self._owner_file, False)
            self._owner_file.close()
        if wait:
            finish()
        else:
            Thread(target=finish, name="spectrum-shutdown", daemon=True).start()
