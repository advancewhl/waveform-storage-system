#!/usr/bin/env sh
set -eu

bundle_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ui_only=false
for argument do
    if [ "$argument" = "--ui-only" ]; then
        ui_only=true
    fi
done
if [ "$ui_only" = false ]; then
    : "${WAVEFORM_CANDIDATE_ZMQ_ENDPOINT:?Set WAVEFORM_CANDIDATE_ZMQ_ENDPOINT before starting the operator GUI}"
fi
exec "${WAVEFORM_GUI_PYTHON:-python3}" "$bundle_root/apps/operator_gui/main.py" "$@"
