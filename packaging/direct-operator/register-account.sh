#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
request_dir=${WAVEFORM_OPERATOR_KEY_REQUEST_DIR:-$(dirname "$root")/.waveform-operator-key-requests}
key_dir="$HOME/.local/state/waveform-operator/ssh"
key="$key_dir/id_ed25519"
account=$(id -un)
uid=$(id -u)

umask 077
mkdir -p "$key_dir"
chmod 700 "$key_dir"
if [[ ! -e "$key" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C "waveform-operator-$account@$(hostname)" -f "$key"
fi
test -r "$key.pub"
test -d "$request_dir"
request="$request_dir/uid-$uid-$account.pub"
if [[ -e "$request" && $(stat -c %u "$request") != "$uid" ]]; then
  printf 'Public-key request is owned by another account: %s\n' "$request" >&2
  exit 2
fi
install -m 644 "$key.pub" "$request"
for desktop_dir in "$HOME/Desktop" "$HOME/桌面"; do
  if [[ -d "$desktop_dir" ]]; then
    desktop="$desktop_dir/Waveform Data.desktop"
    cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Waveform Data
Comment=正式版波形采集与历史查看
Exec=$root/run-operator.sh
Path=$root
Icon=$root/assets/waveform-operator.svg
Terminal=false
StartupNotify=true
Categories=Science;
EOF
    chmod 755 "$desktop"
    printf 'Desktop icon installed: %s\n' "$desktop"
    break
  fi
done
printf 'Public key submitted: %s\n' "$request"
known_hosts="$root/security/known_hosts"
if ssh -i "$key" -o IdentitiesOnly=yes -o BatchMode=yes \
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" \
  -o GlobalKnownHostsFile=/dev/null -o ProxyJump=none -o ProxyCommand=none \
  -o ConnectTimeout=8 -p 22 storage-user@192.0.2.2 \
  'systemctl is-active --quiet waveform-acquisition-v2.service' \
  >/dev/null 2>&1; then
  printf 'Waveform server connection verified. Open Waveform Data from your desktop.\n'
else
  printf 'Ask the waveform server administrator to authorize this account key, then run %s/run-operator.sh.\n' "$root"
fi
