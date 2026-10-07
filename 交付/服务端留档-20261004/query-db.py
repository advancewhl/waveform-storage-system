#!/usr/bin/env python3
"""Read the live waveform MongoDB through its local container, without exposing the URI."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path
from uuid import UUID


DEFAULT_ENV_FILE = Path("/home/storage-user/waveform-storage-v2/service.env")
RESULT_PREFIX = "WAVEFORM_QUERY_RESULT:"


def _settings(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"WAVEFORM_MONGODB_URI", "WAVEFORM_MONGODB_DATABASE"}:
            parts = shlex.split(value, comments=True)
            if parts:
                values[key] = parts[0]
    uri = os.environ.get("WAVEFORM_MONGODB_URI") or values.get("WAVEFORM_MONGODB_URI")
    database = os.environ.get("WAVEFORM_MONGODB_DATABASE") or values.get(
        "WAVEFORM_MONGODB_DATABASE", "waveform_storage"
    )
    if not uri:
        raise ValueError("MongoDB URI is unavailable in the server configuration")
    return uri, database


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
    parser.add_argument("command", choices=("summary", "task", "date", "device"))
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
    if args.command == "date":
        try:
            args.value = date.fromisoformat(args.value).isoformat()
        except ValueError:
            parser.error("date requires YYYY-MM-DD")
    try:
        uri, database = _settings(args.env_file)
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
    print(json.dumps(json.loads(result_line), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
