#!/usr/bin/env bash
set -euo pipefail

expected_user=desktop-user-b
release=/home/site-user/work/StreamDataPro_new/waveform-operator-r6-20261009
expected_sha=c305b9f804e3cef40eb7d2a90fc36cc60a28d715d4fb8bac17cdc87145024b93
expected_fingerprint=SHA256:7ntUrhAwEqqDzQg+S1JftSLZ0mnOL7yqryJZT7uZbeM
uid=$(id -u)

if [[ $(id -un) != "$expected_user" ]]; then
  printf '请在 desktop-user-b 会话中运行；当前用户为 %s。\n' "$(id -un)" >&2
  exit 2
fi

desktop_entry=$(grep -IlF '/home/site-user/bin/startWaveform.sh' \
  "$HOME"/Desktop/*.desktop "$HOME"/桌面/*.desktop 2>/dev/null | head -n 1 || true)
if [[ -z "$desktop_entry" ]]; then
  printf '未找到调用公共 launcher 的 desktop-user-b 桌面项。\n' >&2
  exit 2
fi
printf 'desktop_entry=%s\n' "$desktop_entry"
grep -F 'Exec=/home/site-user/bin/startWaveform.sh' "$desktop_entry"
grep -F "$release/run-operator.sh" /home/site-user/bin/startWaveform.sh

actual_sha=$(sha256sum "$release/bin/waveform-operator/waveform-operator" | awk '{print $1}')
printf 'r6_binary_sha256=%s\n' "$actual_sha"
[[ "$actual_sha" == "$expected_sha" ]]

fingerprint_found=false
for public_key in \
  "$HOME/.local/state/waveform-operator/ssh/id_ed25519.pub" \
  "$HOME/.ssh/id_ed25519_waveform_operator.pub"; do
  if [[ -r "$public_key" ]]; then
    fingerprint=$(ssh-keygen -lf "$public_key" | awk '{print $2}')
    printf 'public_key=%s fingerprint=%s\n' "$public_key" "$fingerprint"
    if [[ "$fingerprint" == "$expected_fingerprint" ]]; then
      fingerprint_found=true
    fi
  fi
done
[[ "$fingerprint_found" == true ]]

source "$release/connection.env"
state_dir="${WAVEFORM_OPERATOR_STATE_DIR}/uid-${uid}"
test -d "$state_dir" && test -w "$state_dir"
printf 'state_dir=%s\n' "$state_dir"
log="$state_dir/operator-launcher.log"
test -r "$log"
grep -F 'direct SSH ready; starting connected UI' "$log" | tail -n 1

port=$((20000 + uid % 30000))
internal="$release/bin/waveform-operator/_internal"
PYTHONPATH="$internal" LD_LIBRARY_PATH="$internal:${LD_LIBRARY_PATH:-}" \
  /opt/miniforge3/envs/pydm-environment/bin/python3.10 - "$port" <<'PY'
import json
import sys
import uuid
import zmq

endpoint = f"tcp://127.0.0.1:{sys.argv[1]}"
context = zmq.Context.instance()

def call(payload):
    socket = context.socket(zmq.REQ)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVTIMEO, 5000)
    try:
        socket.connect(endpoint)
        socket.send_string(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return json.loads(socket.recv().decode("utf-8"))
    finally:
        socket.close()

health = call({"version": 2, "request_id": str(uuid.uuid4()), "command": "health"})
print(f"health accepted={health.get('accepted')} status={health.get('status')}")
assert health.get("accepted") is True and health.get("status") == "succeeded"
runs = call({"version": 2, "request_id": str(uuid.uuid4()), "command": "list_capture_runs", "storage_date_local": "2026-10-06"})
count = len((runs.get("payload") or {}).get("runs", []))
print(f"history accepted={runs.get('accepted')} date=2026-10-06 runs={count}")
assert runs.get("accepted") is True and count > 0
PY

pid=$(pgrep -u "$uid" -f "^$release/bin/waveform-operator/waveform-operator$" | head -n 1)
test -n "$pid"
printf 'r6_gui_pid=%s\n' "$pid"
sha256sum "/proc/$pid/exe"
printf '请在窗口中确认“频谱分析”导航页可见；本脚本只发送 health 和历史查询，不发送采集请求。\n'
