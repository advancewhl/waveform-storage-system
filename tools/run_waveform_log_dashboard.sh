#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [ "${1:-}" = "--background" ]; then
  shift
  if [ "$#" -ne 0 ]; then
    echo "--background 不接受其他参数" >&2
    exit 2
  fi
  if curl --silent --fail --max-time 2 http://127.0.0.1:8765/ >/dev/null; then
    echo "Waveform 日志看板已运行：http://127.0.0.1:8765/"
    exit 0
  fi
  mkdir -p "$project_root/logs"
  nohup setsid python3 "$project_root/tools/waveform_log_dashboard.py" --no-browser \
    >> "$project_root/logs/waveform-dashboard.log" 2>&1 < /dev/null &
  dashboard_pid=$!
  for _ in {1..30}; do
    if curl --silent --fail --max-time 2 http://127.0.0.1:8765/ >/dev/null; then
      echo "Waveform 日志看板已启动：http://127.0.0.1:8765/（PID $dashboard_pid）"
      exit 0
    fi
    if ! kill -0 "$dashboard_pid" 2>/dev/null; then
      break
    fi
    sleep 0.2
  done
  echo "看板启动失败，请查看 $project_root/logs/waveform-dashboard.log" >&2
  exit 1
fi
if [ -z "${WAVEFORM_LOG_SSH_TARGET:-}" ]; then
  "$project_root/tools/run_candidate_gui_47.sh" --check-only
fi
exec python3 "$project_root/tools/waveform_log_dashboard.py" "$@"
