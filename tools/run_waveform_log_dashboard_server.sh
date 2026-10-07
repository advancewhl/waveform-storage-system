#!/usr/bin/env bash
# Run the read-only dashboard on the waveform host; cron can start it at boot.
set -u

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
dashboard_dir=$(dirname -- "$script_dir")
bind_address=${WAVEFORM_DASHBOARD_BIND:-127.0.0.1}
port=${WAVEFORM_DASHBOARD_PORT:-8765}
mkdir -p "$dashboard_dir/logs"

# A long-lived lock keeps manual and boot starts from creating duplicate servers.
exec 9>"$dashboard_dir/logs/dashboard.lock"
flock -n 9 || exit 0
while true; do
  /usr/bin/python3 "$script_dir/waveform_log_dashboard.py" \
    --source local --bind "$bind_address" --port "$port" --no-browser \
    >>"$dashboard_dir/logs/dashboard.log" 2>&1
  sleep 5
done
