#!/bin/bash
set -euo pipefail
base=/home/site-user/work/StreamDataPro_new
release="$base/waveform-operator-pv-frequency-r2-20261026"
previous="$base/waveform-operator-r6-20261009"
launcher=/home/site-user/bin/startWaveform.sh
backup="$launcher.backup-20261026-pv-frequency-r2"
exec 9>"$base/.pv-frequency-r2-deploy.lock"
flock -n 9
test ! -e "$backup"
grep -Fq "$previous/run-operator.sh" "$launcher"
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
