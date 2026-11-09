#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${AGENT_API_RELEASE_OUTPUT:-}" ]]; then
  echo 'Set AGENT_API_RELEASE_OUTPUT to the release parent directory.' >&2
  exit 1
fi

python -m pip install --disable-pip-version-check \
  --requirement packaging/requirements-agent-api-build.txt
python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$AGENT_API_RELEASE_OUTPUT" \
  --workpath /tmp/waveform-agent-api-build \
  packaging/agent-api.spec
python -c 'import importlib.metadata as m, json, sys; from pathlib import Path; import os; packages=("PyInstaller","caproto","fastapi","uvicorn","cryptography","h5py","numpy","pandas","paramiko","pymongo","pyzmq","scipy"); Path(os.environ["AGENT_API_RELEASE_OUTPUT"], "runtime-versions.json").write_text(json.dumps({"python": sys.version, "build_image": "python:3.10-buster", "dependencies": {p: m.version(p) for p in packages}}, indent=2) + "\n")'
