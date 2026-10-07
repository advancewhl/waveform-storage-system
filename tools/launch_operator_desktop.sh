#!/usr/bin/env bash
set -u

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
log_dir="$project_root/logs"
log_file="$log_dir/operator-launcher.log"
python_bin=${WAVEFORM_GUI_PYTHON:-/home/whloong/miniconda3/envs/waveform-storage/bin/python}
local_binary="$project_root/outputs/operator-local/waveform-operator/waveform-operator"
operator_binary=${WAVEFORM_OPERATOR_EXECUTABLE:-"$local_binary"}
if [ -z "${WAVEFORM_OPERATOR_EXECUTABLE:-}" ] && [ ! -x "$operator_binary" ]; then
  operator_binary="$project_root/dist/operator-debian10/waveform-operator/waveform-operator"
fi
export WAVEFORM_OPERATOR_EXECUTABLE="$operator_binary"
mkdir -p -- "$log_dir"

printf '\n[%s] Starting formal operator GUI\n' "$(date '+%Y-%m-%d %H:%M:%S')" >>"$log_file"
connected=false
if "$python_bin" "$project_root/tools/acquisition_v2_client.py" \
  --endpoint tcp://127.0.0.1:5001 --timeout-ms 1000 health >>"$log_file" 2>&1; then
  connected=true
else
  if timeout 40s bash "$project_root/tools/run_candidate_gui_47.sh" --check-only >>"$log_file" 2>&1; then
    connected=true
  fi
fi

if [ "$connected" = true ]; then
  printf '[%s] Service available; opening connected UI\n' "$(date '+%Y-%m-%d %H:%M:%S')" >>"$log_file"
  command=(bash "$project_root/tools/run_candidate_gui_47.sh" "$@")
else
  printf '[%s] Service unavailable; opening UI-only mode\n' "$(date '+%Y-%m-%d %H:%M:%S')" >>"$log_file"
  export WAVEFORM_OPERATOR_ROOT="$project_root"
  command=("$operator_binary" --ui-only "$@")
fi

if "${command[@]}" >>"$log_file" 2>&1; then
  exit 0
else
  status=$?
fi

printf '[%s] Operator GUI exited with status %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$status" >>"$log_file"
if command -v zenity >/dev/null 2>&1; then
  zenity --error --title='波形存储' \
    --text="正式版启动失败。请查看：$log_file" 2>/dev/null || true
fi
exit "$status"
