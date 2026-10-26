#!/bin/bash
set -euo pipefail
umask 022
base=/home/site-user/work/StreamDataPro_new
release="$base/waveform-operator-pv-frequency-r2-20261026"
previous="$base/waveform-operator-r6-20261009"
launcher=/home/site-user/bin/startWaveform.sh
backup="$launcher.backup-20261026-pv-frequency-r2"
exec 9>"$base/.pv-frequency-r2-deploy.lock"
flock -n 9
test ! -e "$release"
test ! -e "$backup"
grep -Fq "$previous/run-operator.sh" "$launcher"
# The server was separately checked for accepted/running tasks before switching.
tar -xzf "$base/incoming/20261026-pv-frequency-r2.tar.gz" -C "$base"
ln -s "$(readlink -f "$previous/connection.env")" "$release/connection.env"
ln -s "$(readlink -f "$previous/credentials")" "$release/credentials"
test -x "$release/bin/waveform-operator/waveform-operator"
test -r "$release/connection.env"
chmod -R a+rX "$release/bin" "$release/config" "$release/assets" "$release/security"
mkdir "$release/evidence"
export QT_QPA_PLATFORM=offscreen
export WAVEFORM_OPERATOR_STATE_DIR=/tmp/waveform-pv-r2-preview-1001
export MPLCONFIGDIR=/tmp/waveform-pv-r2-matplotlib-1001
# Connected offscreen preview only: no real desktop smoke and no capture request.
"$release/run-operator.sh" --screenshot "$release/evidence/remote-pv-page.png" </dev/null
test -s "$release/evidence/remote-pv-page.png"
cp -p "$launcher" "$backup"
trap 'cp -p "$backup" "$launcher.rollback-r2"; mv -Tf "$launcher.rollback-r2" "$launcher"' ERR
printf '#!/usr/bin/env bash\nset -e\nexec %s/run-operator.sh "$@"\n' "$release" > "$launcher.next-r2"
chmod 755 "$launcher.next-r2"
mv -Tf "$launcher.next-r2" "$launcher"
trap - ERR
printf 'ACTIVATED=%s\nBACKUP=%s\n' "$release" "$backup"
namei -l "$release/bin/waveform-operator/waveform-operator"
cat "$launcher"
