#!/usr/bin/env sh
set -eu

bundle_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${WAVEFORM_SERVER_PYTHON:-python3}" "$bundle_root/apps/acquisition_server/main.py" "$@"
