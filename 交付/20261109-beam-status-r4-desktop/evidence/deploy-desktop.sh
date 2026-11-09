#!/bin/bash
set -euo pipefail
umask 022
base=/home/site-user/work/StreamDataPro_new
release="$base/waveform-operator-beam-status-r4-20261109"
previous="$base/waveform-operator-pv-frequency-r2-20261026"
launcher=/home/site-user/bin/startWaveform.sh
backup="$launcher.backup-20261109-beam-status-r4"
exec 9>"$base/.beam-status-r4-deploy.lock"
flock -n 9
test ! -e "$release"
test ! -e "$backup"
grep -Fq "$previous/run-operator.sh" "$launcher"
tar -xzf "$base/incoming/20261109-beam-status-r4.tar.gz" -C "$base"
ln -s "$(readlink -f "$previous/connection.env")" "$release/connection.env"
ln -s "$(readlink -f "$previous/credentials")" "$release/credentials"
test -x "$release/bin/waveform-operator/waveform-operator"
test -r "$release/connection.env"
chmod -R a+rX "$release/bin" "$release/config" "$release/assets" "$release/security"
mkdir "$release/evidence"
export QT_QPA_PLATFORM=offscreen
export WAVEFORM_OPERATOR_STATE_DIR=/tmp/waveform-r4-preview-${UID}
export MPLCONFIGDIR=/tmp/waveform-r4-matplotlib-${UID}
"$release/run-operator.sh" --screenshot "$release/evidence/remote-beam-page.png" </dev/null
test -s "$release/evidence/remote-beam-page.png"
cp -p "$launcher" "$backup"
trap 'cp -p "$backup" "$launcher.rollback-r4"; mv -Tf "$launcher.rollback-r4" "$launcher"' ERR
printf '#!/usr/bin/env bash\nset -e\nexec %s/run-operator.sh "$@"\n' "$release" > "$launcher.next-r4"
chmod 755 "$launcher.next-r4"
mv -Tf "$launcher.next-r4" "$launcher"
trap - ERR
printf 'ACTIVATED=%s\nBACKUP=%s\n' "$release" "$backup"
namei -l "$release/bin/waveform-operator/waveform-operator"
cat "$launcher"
