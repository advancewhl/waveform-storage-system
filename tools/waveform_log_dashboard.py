#!/usr/bin/env python3
"""Serve a read-only local dashboard for waveform capture and operator logs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
HTML = Path(__file__).with_suffix(".html")
QUERY = Path(__file__).with_name("query_waveform_mongodb.py")
JOURNAL = ROOT / "logs" / "operator-ui.jsonl"
SSH_TARGET = os.environ.get("WAVEFORM_LOG_SSH_TARGET", "waveform-candidate-server")
CONTROL_SOCKET = os.environ.get("WAVEFORM_LOG_SSH_CONTROL_SOCKET")
if CONTROL_SOCKET is None and SSH_TARGET == "waveform-candidate-server":
    CONTROL_SOCKET = str(Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"waveform-candidate-5001-{os.getuid()}.sock")
SSH_RECONNECT_LOCK = threading.Lock()


def reconnect_candidate_ssh() -> None:
    """Recreate the local control connection used by the desktop candidate GUI."""
    if SSH_TARGET != "waveform-candidate-server":
        return
    with SSH_RECONNECT_LOCK:
        print("dashboard: restoring waveform SSH connection", file=sys.stderr, flush=True)
        completed = subprocess.run(
            [str(ROOT / "tools" / "run_candidate_gui_47.sh"), "--check-only"],
            text=True, capture_output=True, timeout=40, check=False,
        )
    if completed.returncode:
        print(f"dashboard: SSH reconnect failed (exit {completed.returncode})", file=sys.stderr, flush=True)
        raise RuntimeError("waveform SSH 连接暂不可用；页面会在下次刷新时自动重试")
    print("dashboard: waveform SSH connection restored", file=sys.stderr, flush=True)


def capture_tasks(selected_date: str, source: str = "ssh") -> dict:
    if source == "local":
        command = [sys.executable, str(QUERY), "tasks-date", selected_date]
        script_input = None
    else:
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if CONTROL_SOCKET:
            command.extend(["-S", CONTROL_SOCKET])
        command.extend([SSH_TARGET, f"python3 - tasks-date {selected_date}"])
        script_input = QUERY.read_text(encoding="utf-8")
    if source == "ssh" and CONTROL_SOCKET and not Path(CONTROL_SOCKET).exists():
        reconnect_candidate_ssh()

    def query() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            input=script_input,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )

    completed = query()
    if completed.returncode and source == "ssh" and SSH_TARGET == "waveform-candidate-server":
        reconnect_candidate_ssh()
        completed = query()
    if completed.returncode:
        raise RuntimeError("服务端查询不可用，请检查 MongoDB 和看板启动配置" if source == "local"
                           else "服务端查询不可用，请检查 waveform SSH 连接和看板启动配置")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("服务端返回了无法解析的任务数据") from exc


def operator_events(selected_date: str) -> list[dict]:
    if not JOURNAL.exists():
        return []
    events = []
    with JOURNAL.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(item.get("timestamp", "")).startswith(selected_date):
                events.append({
                    key: item.get(key)
                    for key in ("timestamp", "kind", "stage", "state", "detail", "code", "summary", "suggestion", "mode")
                    if key in item
                })
    return events


class Handler(BaseHTTPRequestHandler):
    query_source = "ssh"

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/":
            self.respond(200, "text/html; charset=utf-8", HTML.read_bytes())
            return
        if request.path != "/api/logs":
            self.respond(404, "text/plain; charset=utf-8", b"Not found")
            return
        selected_date = parse_qs(request.query).get("date", [""])[0]
        try:
            if date.fromisoformat(selected_date).isoformat() != selected_date:
                raise ValueError("invalid date")
        except ValueError:
            self.json_response(400, {"error": "日期应为 YYYY-MM-DD"})
            return
        # Keep local events visible even when the SSH connection is down.
        result = {
            "date": selected_date,
            "operator_events_available": self.query_source == "ssh",
            "operator_events": operator_events(selected_date) if self.query_source == "ssh" else [],
        }
        try:
            result["capture"] = capture_tasks(selected_date, self.query_source)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            result["capture_error"] = str(exc)
        self.json_response(200, result)

    def respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def json_response(self, status: int, data: dict) -> None:
        self.respond(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt: str, *args: object) -> None:
        print("dashboard:", fmt % args, file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1", help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--source", choices=("ssh", "local"), default="ssh")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    Handler.query_source = args.source
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    url = f"http://{args.bind}:{args.port}/"
    print(f"Waveform 日志看板：{url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
