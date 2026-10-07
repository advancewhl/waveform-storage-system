#!/usr/bin/env python3
"""Read the live waveform MongoDB through its local container, without exposing the URI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID


DEFAULT_ENV_FILE = Path("/home/storage-user/waveform-storage-v2/service.env")
DEFAULT_TASK_STATE_DIR = Path("/home/storage-user/waveform-storage-v2/state")
RESULT_PREFIX = "WAVEFORM_QUERY_RESULT:"
SENSITIVE_VALUE = re.compile(r"(?i)\b(password|secret|token|credential)\s*=\s*[^\s,;]+")


def _settings(path: Path) -> tuple[str, str, Path]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"WAVEFORM_MONGODB_URI", "WAVEFORM_MONGODB_DATABASE", "WAVEFORM_TASK_STATE_DIR"}:
            parts = shlex.split(value, comments=True)
            if parts:
                values[key] = parts[0]
    uri = os.environ.get("WAVEFORM_MONGODB_URI") or values.get("WAVEFORM_MONGODB_URI")
    database = os.environ.get("WAVEFORM_MONGODB_DATABASE") or values.get(
        "WAVEFORM_MONGODB_DATABASE", "waveform_storage"
    )
    if not uri:
        raise ValueError("MongoDB URI is unavailable in the server configuration")
    state_dir = Path(os.environ.get("WAVEFORM_TASK_STATE_DIR") or values.get(
        "WAVEFORM_TASK_STATE_DIR", str(DEFAULT_TASK_STATE_DIR)
    ))
    return uri, database, state_dir


def _failure_stage(error: str | None) -> tuple[str | None, str | None]:
    """Label only unambiguous legacy error text; old records lack a stage field."""
    if not error:
        return None, None
    lowered = error.lower()
    if "no completed matching file" in lowered or "not downloaded" in lowered:
        return "设备文件等待/下载", "inferred"
    if "not triggered" in lowered:
        return "EPICS 触发", "inferred"
    return None, None


def _attach_device_results(result: dict, state_dir: Path) -> None:
    for task in result.get("tasks", []):
        task_error = task.get("error")
        if isinstance(task_error, dict):
            task["error"] = {
                key: SENSITIVE_VALUE.sub(r"\1=<redacted>", value)[:600]
                if isinstance(value, str) else value
                for key, value in task_error.items()
            }
        try:
            request_id = str(UUID(task["_id"]))
            record = json.loads((state_dir / f"{request_id}.json").read_text(encoding="utf-8"))
            if record.get("request_id") != request_id:
                raise ValueError("request ID mismatch")
            raw_results = record.get("device_results", [])
            if not isinstance(raw_results, list):
                raise ValueError("invalid device results")
        except (OSError, ValueError, KeyError, TypeError):
            task["device_detail_state"] = "unavailable"
            task["device_results"] = []
            continue
        task["device_detail_state"] = "available"
        results = []
        for item in raw_results:
            if not isinstance(item, dict) or not isinstance(item.get("device_id"), str):
                continue
            raw_error = item.get("error")
            error = SENSITIVE_VALUE.sub(r"\1=<redacted>", raw_error)[:600] if isinstance(raw_error, str) else None
            stage = item.get("failure_stage")
            source = "recorded" if isinstance(stage, str) and stage else None
            if source is None:
                stage, source = _failure_stage(error)
            results.append({
                "device_id": item["device_id"],
                "success": bool(item.get("success")),
                "attempted_cycles": item.get("attempted_cycles"),
                "successful_cycles": item.get("successful_cycles"),
                "error": error,
                "failure_stage": stage,
                "stage_source": source,
            })
        task["device_results"] = results


def _query_script(command: str, value: str | None, limit: int, uri: str, database: str) -> str:
    setup = (
        f"const data = connect({json.dumps(uri)}).getSiblingDB({json.dumps(database)});\n"
        f"const limit = {limit};\n"
    )
    if command == "summary":
        body = """
const result = {
  database: data.getName(),
  task_count: data.capture_tasks.countDocuments({}),
  file_count: data.waveform_files.countDocuments({}),
  registry_count: data.device_registry_versions.countDocuments({}),
  latest_tasks: data.capture_tasks.find({}, {
    _id: 1, status: 1, created_at: 1, frequency_hz: 1,
    duration_seconds: 1, result_summary: 1, error: 1
  }).sort({created_at: -1}).limit(limit).toArray()
};
"""
    elif command == "task":
        body = f"""
const requestId = {json.dumps(value)};
const result = {{
  task: data.capture_tasks.findOne({{_id: requestId}}),
  file_count: data.waveform_files.countDocuments({{request_id: requestId}}),
  files: data.waveform_files.find({{request_id: requestId}}, {{
    _id: 0, device_id: 1, capture_sequence: 1, status: 1,
    captured_at: 1, storage_date_local: 1, time_sec: 1,
    sample_count: 1, "file.path": 1
  }}).sort({{device_id: 1, capture_sequence: 1}}).limit(limit).toArray()
}};
"""
    elif command == "date":
        body = f"""
const selectedDate = {json.dumps(value)};
const filter = {{storage_date_local: selectedDate, status: "completed"}};
const times = data.waveform_files.distinct("time_sec", filter).sort();
const result = {{
  date: selectedDate,
  completed_file_count: data.waveform_files.countDocuments(filter),
  times: times.map(time => ({{
    time: time,
    file_count: data.waveform_files.countDocuments({{
      storage_date_local: selectedDate, time_sec: time, status: "completed"
    }})
  }}))
}};
"""
    elif command == "tasks-date":
        start = datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone(timedelta(hours=8)))
        end = start + timedelta(days=1)
        body = f"""
const start = ISODate({json.dumps(start.astimezone(timezone.utc).isoformat())});
const end = ISODate({json.dumps(end.astimezone(timezone.utc).isoformat())});
const filter = {{created_at: {{$gte: start, $lt: end}}}};
const tasks = data.capture_tasks.find(filter, {{
  _id: 1, created_at: 1, started_at: 1, completed_at: 1,
  duration_seconds: 1, frequency_hz: 1, status: 1,
  area_ids: 1, device_ids: 1, result_summary: 1, error: 1
}}).sort({{created_at: 1}}).toArray();
const result = {{date: {json.dumps(value)}, timezone: "Asia/Shanghai",
  total: tasks.length, tasks: tasks}};
"""
    else:
        body = f"""
const deviceId = {json.dumps(value)};
const filter = {{device_id: deviceId, status: "completed"}};
const result = {{
  device_id: deviceId,
  completed_file_count: data.waveform_files.countDocuments(filter),
  latest_files: data.waveform_files.find(filter, {{
    _id: 0, request_id: 1, capture_sequence: 1,
    captured_at: 1, storage_date_local: 1, time_sec: 1,
    sample_count: 1, "file.path": 1
  }}).sort({{captured_at: -1}}).limit(limit).toArray()
}};
"""
    return setup + body + f"print({json.dumps(RESULT_PREFIX)} + JSON.stringify(result));\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only queries for the live waveform MongoDB on its server"
    )
    parser.add_argument("command", choices=("summary", "task", "date", "device", "tasks-date"))
    parser.add_argument("value", nargs="?", help="request ID, YYYY-MM-DD, or device ID")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--container", default="waveform-mongodb")
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")
    if args.command == "summary" and args.value is not None:
        parser.error("summary does not accept a value")
    if args.command != "summary" and args.value is None:
        parser.error(f"{args.command} requires a value")
    if args.command == "task":
        try:
            args.value = str(UUID(args.value))
        except ValueError:
            parser.error("task requires a UUID request ID")
    if args.command in {"date", "tasks-date"}:
        try:
            args.value = date.fromisoformat(args.value).isoformat()
        except ValueError:
            parser.error("date requires YYYY-MM-DD")
    try:
        uri, database, state_dir = _settings(args.env_file)
        completed = subprocess.run(
            ["docker", "exec", "-i", args.container, "mongosh", "--quiet", "--nodb"],
            input=_query_script(args.command, args.value, args.limit, uri, database),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Query unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1
    result_line = next(
        (line.split(RESULT_PREFIX, 1)[1].strip()
         for line in completed.stdout.splitlines() if RESULT_PREFIX in line),
        None,
    )
    if completed.returncode != 0 or result_line is None:
        print("MongoDB query failed; check container and configured credentials", file=sys.stderr)
        return 1
    result = json.loads(result_line)
    if args.command == "tasks-date":
        _attach_device_results(result, state_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
