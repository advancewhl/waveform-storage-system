#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=${WAVEFORM_GUI_PYTHON:-/home/whloong/miniconda3/envs/waveform-storage/bin/python}
preview_binary="$project_root/outputs/operator-local/waveform-operator/waveform-operator"
if [ ! -x "$preview_binary" ]; then
  preview_binary="$project_root/outputs/spectrum-preview/pyinstaller-dist/waveform-operator/waveform-operator"
fi
export MPLCONFIGDIR=${MPLCONFIGDIR:-/tmp/waveform-spectrum-mpl}
export WAVEFORM_OPERATOR_ROOT="$project_root"
if [ "${1:-}" = "--connected" ]; then
  shift
  if [ ! -x "$preview_binary" ]; then
    echo "请先构建本机频谱预览可执行文件。" >&2
    exit 1
  fi
  export WAVEFORM_OPERATOR_EXECUTABLE="$preview_binary"
  exec bash "$project_root/tools/run_candidate_gui_47.sh" --page spectrum "$@"
fi
if [ -x "$preview_binary" ]; then
  exec "$preview_binary" --ui-only --page spectrum "$@"
fi
exec "$python_bin" "$project_root/apps/operator_gui/main.py" --ui-only --page spectrum "$@"
