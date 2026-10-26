#!/usr/bin/env bash
set -euo pipefail
base=/home/storage-user/waveform-storage-v2
exec 9>"$base/incoming/release.lock"
flock -n 9
old=$(readlink -f "$base/current")
next="$base/current.next-20261026-timing-10hz-r1"
backup="$base/current.rollback-20261026-timing-10hz-r1"
test ! -e "$next" && test ! -L "$next"
test ! -e "$backup" && test ! -L "$backup"
test "$old" = /home/storage-user/waveform-storage-v2/releases/20261008-history-release-r4
python3 - <<'GUARD'
import json
from pathlib import Path
for p in Path("/home/storage-user/waveform-storage-v2/state").rglob("*.json"):
    item=json.loads(p.read_text())
    if item.get("response", item).get("status") in ("accepted", "running"):
        raise SystemExit("Unfinished capture task; wait before deployment")
GUARD
rollback() {
    trap - ERR
    mv -Tf "$backup" "$base/current"
    systemctl restart waveform-acquisition-v2
}
ln -s "$old" "$backup"
trap rollback ERR
systemctl stop waveform-acquisition-v2
ln -s "$base/releases/20261026-timing-10hz-r1" "$next"
mv -Tf "$next" "$base/current"
chown -h storage-user:storage-user "$base/current"
systemctl start waveform-acquisition-v2
for i in {1..30}; do
    if systemctl is-active --quiet waveform-acquisition-v2 && ss -ltn | grep -q ':5001 '; then
        printf 'READY
'
        read -r -t 120 decision
        test "$decision" = COMMIT
        trap - ERR
        rm -- "$backup"
        printf 'COMMITTED
'
        exit 0
    fi
    sleep 1
done
false
