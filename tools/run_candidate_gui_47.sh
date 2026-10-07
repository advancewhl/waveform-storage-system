#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
env_file="$project_root/.env"
registry="$project_root/config/devices.operator.json"
capture_registry="$project_root/config/devices.probe47.json"
python_bin=${WAVEFORM_GUI_PYTHON:-/home/whloong/miniconda3/envs/waveform-storage/bin/python}
operator_binary=${WAVEFORM_OPERATOR_EXECUTABLE:-"$project_root/dist/operator-debian10/waveform-operator/waveform-operator"}
tunnel_socket=${XDG_RUNTIME_DIR:-/tmp}/waveform-candidate-5001-${UID}.sock

if [ "${1:-}" = "--stop-tunnel" ]; then
  if ssh -S "$tunnel_socket" -O check waveform-candidate-server >/dev/null 2>&1; then
    ssh -S "$tunnel_socket" -O exit waveform-candidate-server >/dev/null
    echo "Candidate SSH tunnel stopped"
  else
    rm -f -- "$tunnel_socket"
    echo "Candidate SSH tunnel is not running"
  fi
  exit 0
fi

if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != "--check-only" ]; }; then
  echo "Usage: $0 [--check-only|--stop-tunnel]" >&2
  exit 2
fi

if [ ! -f "$env_file" ]; then
  echo "Missing local configuration: $env_file" >&2
  exit 1
fi
if [ ! -f "$registry" ]; then
  echo "Missing operator display registry: $registry" >&2
  exit 1
fi
if [ ! -f "$capture_registry" ]; then
  echo "Missing server-matched capture registry: $capture_registry" >&2
  exit 1
fi
if [ ! -x "$operator_binary" ]; then
  echo "GUI executable is not installed: $operator_binary" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

server_host=${WAVEFORM_STORAGE_HOST:-192.0.2.2}
server_user=${WAVEFORM_STORAGE_USER:-storage-user}
server_port=${WAVEFORM_STORAGE_PORT:-22}
secret_helper="$project_root/tools/waveform_ssh_secret.py"
jump_target=$("$secret_helper" --jump-target)
waveform_target=$("$secret_helper" --waveform-target)
if [ "$waveform_target" != "${server_user}@${server_host}" ]; then
  echo "Waveform SSH target differs from local configuration" >&2
  exit 1
fi
export SSH_ASKPASS="$secret_helper"
export SSH_ASKPASS_REQUIRE=force
export DISPLAY=${DISPLAY:-:0}
proxy_command="env WAVEFORM_SSH_ROLE=jump ssh -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=1 -o ConnectTimeout=5 -o StrictHostKeyChecking=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -W %h:%p $jump_target"
endpoint=tcp://127.0.0.1:5001
export WAVEFORM_CANDIDATE_ZMQ_ENDPOINT="$endpoint"
export WAVEFORM_STORAGE_SSH_CONTROL_SOCKET="$tunnel_socket"
export MPLCONFIGDIR=${MPLCONFIGDIR:-/tmp/waveform-matplotlib-${UID}}
mkdir -p -- "$MPLCONFIGDIR"

health() {
  "$python_bin" "$project_root/tools/acquisition_v2_client.py" \
    --endpoint "$endpoint" --timeout-ms 5000 health >/dev/null 2>&1
}

if ! health; then
  if ! ssh -S "$tunnel_socket" -O check waveform-candidate-server >/dev/null 2>&1; then
    rm -f -- "$tunnel_socket"
    WAVEFORM_SSH_ROLE=waveform ssh \
      -M -S "$tunnel_socket" \
      -fN \
      -p "$server_port" \
      -o ProxyCommand="$proxy_command" \
      -o PreferredAuthentications=password,keyboard-interactive \
      -o PubkeyAuthentication=no \
      -o NumberOfPasswordPrompts=1 \
      -o ConnectTimeout=8 \
      -o StrictHostKeyChecking=yes \
      -o ControlPersist=8h \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -L 5001:127.0.0.1:5001 \
      "${server_user}@${server_host}"
  fi

  for _ in 1 2 3 4 5; do
    health && break
    sleep 1
  done
fi

if ! health; then
  echo "Candidate V2 health check failed at $endpoint" >&2
  echo "Check the server service: systemctl status waveform-acquisition-v2" >&2
  exit 1
fi

if [ "${1:-}" = "--check-only" ]; then
  echo "Candidate V2 is healthy; the expanded operator GUI is ready"
  exit 0
fi

echo "Candidate V2 is healthy; opening the expanded operator GUI"
cd -- "$project_root"
export WAVEFORM_OPERATOR_ROOT="$project_root"
exec "$operator_binary" \
  --mode candidate_v2 \
  --registry "$registry" \
  --capture-registry "$capture_registry" "$@"
