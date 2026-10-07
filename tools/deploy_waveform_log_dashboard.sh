#!/usr/bin/env bash
# Deploy the read-only dashboard to the existing waveform host over its SSH tunnel.
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
socket=${XDG_RUNTIME_DIR:-/tmp}/waveform-candidate-5001-${UID}.sock
target=waveform-candidate-server
remote_dir=/home/storage-user/waveform-storage-v2/dashboard

"$project_root/tools/run_candidate_gui_47.sh" --check-only
ssh -S "$socket" -o BatchMode=yes "$target" \
  "mkdir -p '$remote_dir/tools' '$remote_dir/logs'"
scp -q -o ControlPath="$socket" -o BatchMode=yes \
  "$project_root/tools/waveform_log_dashboard.py" \
  "$project_root/tools/waveform_log_dashboard.html" \
  "$project_root/tools/query_waveform_mongodb.py" \
  "$project_root/tools/run_waveform_log_dashboard_server.sh" \
  "$target:$remote_dir/tools/"

ssh -S "$socket" -o BatchMode=yes "$target" 'bash -s' <<'REMOTE'
set -euo pipefail
dashboard=/home/storage-user/waveform-storage-v2/dashboard
chmod 700 "$dashboard/tools/run_waveform_log_dashboard_server.sh"
command -v flock >/dev/null
python3 "$dashboard/tools/query_waveform_mongodb.py" tasks-date "$(TZ=Asia/Shanghai date +%F)" >/dev/null
cron_line="@reboot WAVEFORM_DASHBOARD_BIND=192.0.2.2 $dashboard/tools/run_waveform_log_dashboard_server.sh"
if ! crontab -l 2>/dev/null | grep -Fqx "$cron_line"; then
  { crontab -l 2>/dev/null || true; printf '%s\n' "$cron_line"; } | crontab -
fi
nohup setsid env WAVEFORM_DASHBOARD_BIND=192.0.2.2 \
  "$dashboard/tools/run_waveform_log_dashboard_server.sh" \
  >/dev/null 2>&1 </dev/null &
for _ in {1..30}; do
  if curl -fsS --max-time 2 http://192.0.2.2:8765/ >/dev/null; then
    curl -fsS --max-time 45 \
      "http://192.0.2.2:8765/api/logs?date=$(TZ=Asia/Shanghai date +%F)" \
      | python3 -c 'import json,sys; data=json.load(sys.stdin); assert "capture" in data and not data.get("operator_events_available"); print("Waveform 共享看板已就绪：http://192.0.2.2:8765/")'
    exit 0
  fi
  sleep 1
done
echo "看板启动失败；请查看 $dashboard/logs/dashboard.log" >&2
exit 1
REMOTE
