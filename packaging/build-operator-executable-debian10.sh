#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
docker run --rm \
  --network host \
  --env HTTP_PROXY --env HTTPS_PROXY --env http_proxy --env https_proxy --env NO_PROXY --env no_proxy \
  --volume "$root:/workspace" \
  --workdir /workspace \
  python:3.10-buster \
  bash -lc '
    set -euo pipefail
    python -m pip install --disable-pip-version-check --no-cache-dir \
      --requirement packaging/requirements-operator-formal.txt \
      PyInstaller==6.11.1
    python -m PyInstaller --clean --noconfirm \
      --distpath dist/operator-debian10 \
      --workpath build/operator-debian10 \
      packaging/operator-executable.spec
    chown -R "'"$(id -u):$(id -g)"'" dist/operator-debian10 build/operator-debian10
  '
test -x "$root/dist/operator-debian10/waveform-operator/waveform-operator"
echo "Built $root/dist/operator-debian10/waveform-operator/waveform-operator"
