#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$HOME/.local/share/applications"
desktop="$HOME/.local/share/applications/waveform-operator.desktop"
cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=波形存储
Comment=正式版波形采集与历史查看
Exec=$root/run-operator.sh
Path=$root
Icon=$root/assets/waveform-operator.svg
Terminal=false
StartupNotify=true
Categories=Science;
EOF
chmod +x "$desktop" "$root/run-operator.sh"
desktop_dir=""
if command -v xdg-user-dir >/dev/null 2>&1; then
  desktop_dir=$(xdg-user-dir DESKTOP 2>/dev/null || true)
fi
if [[ ! -d "$desktop_dir" || "$desktop_dir" == "$HOME" ]]; then
  for candidate in "$HOME/Desktop" "$HOME/桌面"; do
    if [[ -d "$candidate" ]]; then
      desktop_dir="$candidate"
      break
    fi
  done
fi
if [[ -d "$desktop_dir" && "$desktop_dir" != "$HOME" ]]; then
  cp "$desktop" "$desktop_dir/waveform-operator.desktop"
  chmod +x "$desktop_dir/waveform-operator.desktop"
  printf 'Desktop icon: %s\n' "$desktop_dir/waveform-operator.desktop"
fi
printf 'Installed %s\n' "$desktop"
