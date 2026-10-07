#!/usr/bin/env bash
set -u

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
umask 027
config="$root/connection.env"
if [[ -f "$config" ]]; then
  source "$config"
fi
state_base=${WAVEFORM_OPERATOR_STATE_DIR:-$root/logs}
state_dir="$state_base/uid-${UID}"
if ! mkdir -p "$state_dir"; then
  printf 'Cannot write operator state directory: %s\n' "$state_dir" >&2
  exit 2
fi
if [[ $(stat -c %u "$state_dir") != "$UID" ]]; then
  printf 'Operator state directory is owned by another account: %s\n' "$state_dir" >&2
  exit 2
fi
export WAVEFORM_OPERATOR_STATE_DIR="$state_dir"
log="$state_dir/operator-launcher.log"

binary="$root/bin/waveform-operator/waveform-operator"
if [[ ! -x "$binary" ]]; then
  printf 'Waveform operator executable is missing: %s\n' "$binary" | tee -a "$log" >&2
  exit 2
fi
export WAVEFORM_OPERATOR_ROOT="$root"
export MPLCONFIGDIR=${MPLCONFIGDIR:-$HOME/.cache/waveform-operator/matplotlib}
mkdir -p "$MPLCONFIGDIR"

shared_key="$root/credentials/id_ed25519_waveform_operator"
private_key_dir="$HOME/.local/state/waveform-operator/ssh"
if [[ ! -r "${WAVEFORM_SSH_IDENTITY_FILE:-}" ]]; then
  if [[ -r "$shared_key" ]]; then
    mkdir -p "$private_key_dir"
    chmod 700 "$private_key_dir"
    install -m 600 "$shared_key" "$private_key_dir/id_ed25519"
    export WAVEFORM_SSH_IDENTITY_FILE="$private_key_dir/id_ed25519"
  elif [[ -r "$private_key_dir/id_ed25519" ]]; then
    export WAVEFORM_SSH_IDENTITY_FILE="$private_key_dir/id_ed25519"
  else
    printf 'No SSH key for this account. Run %s/register-account.sh first.\n' "$root" >>"$log"
  fi
fi
known_hosts="$root/security/known_hosts"
known_host_options=()
if [[ -f "$known_hosts" ]]; then
  known_host_options=(-o "UserKnownHostsFile=$known_hosts" -o GlobalKnownHostsFile=/dev/null)
  export WAVEFORM_OPERATOR_KNOWN_HOSTS_FILE="$known_hosts"
fi

ui_only=false
for arg in "$@"; do
  if [[ "$arg" == --ui-only ]]; then ui_only=true; fi
done

if [[ "$ui_only" == false ]]; then
  host=${WAVEFORM_STORAGE_HOST:-192.0.2.2}
  user=${WAVEFORM_STORAGE_USER:-storage-user}
  port=${WAVEFORM_STORAGE_PORT:-22}
  local_port=$((20000 + UID % 30000))
  runtime=${XDG_RUNTIME_DIR:-/tmp}
  socket="$runtime/waveform-direct-${UID}-${local_port}.sock"
  target="$user@$host"
  identity=()
  if [[ -n "${WAVEFORM_SSH_IDENTITY_FILE:-}" ]]; then
    identity=(-i "$WAVEFORM_SSH_IDENTITY_FILE" -o IdentitiesOnly=yes)
  fi
  direct=(-o ProxyJump=none -o ProxyCommand=none)
  if ! ssh -S "$socket" -O check -p "$port" "${direct[@]}" "${known_host_options[@]}" "${identity[@]}" "$target" >>"$log" 2>&1; then
    rm -f -- "$socket"
    ssh -M -S "$socket" -fN -p "$port" \
      -o BatchMode=yes -o StrictHostKeyChecking=yes \
      "${direct[@]}" "${known_host_options[@]}" \
      -o ConnectTimeout=8 -o ExitOnForwardFailure=yes \
      -o ControlPersist=8h -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -L "127.0.0.1:${local_port}:127.0.0.1:5001" \
      "${identity[@]}" "$target" >>"$log" 2>&1 || true
  fi
  if ssh -S "$socket" -O check -p "$port" "${direct[@]}" "${known_host_options[@]}" "${identity[@]}" "$target" >>"$log" 2>&1 &&
     ssh -S "$socket" -p "$port" "${direct[@]}" "${known_host_options[@]}" "${identity[@]}" "$target" 'systemctl is-active --quiet waveform-acquisition-v2.service' >>"$log" 2>&1; then
    export WAVEFORM_CANDIDATE_ZMQ_ENDPOINT="tcp://127.0.0.1:${local_port}"
    export WAVEFORM_STORAGE_SSH_CONTROL_SOCKET="$socket"
    export WAVEFORM_STORAGE_HOST="$host"
    export WAVEFORM_STORAGE_USER="$user"
    export WAVEFORM_STORAGE_PORT="$port"
  else
    ui_only=true
  fi
fi

if [[ "$ui_only" == true ]]; then
  printf '[%s] direct SSH unavailable; starting UI-only\n' "$(date '+%F %T')" >>"$log"
  command=("$binary" --ui-only "$@")
else
  printf '[%s] direct SSH ready; starting connected UI\n' "$(date '+%F %T')" >>"$log"
  command=("$binary" "$@")
fi

"${command[@]}" >>"$log" 2>&1
status=$?
if [[ "$status" -ne 0 ]] && command -v zenity >/dev/null 2>&1; then
  zenity --error --title='波形存储' --text="启动失败，请查看 $log" 2>/dev/null || true
fi
exit "$status"
