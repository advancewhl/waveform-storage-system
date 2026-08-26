#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_image="${WAVEFORM_BUILD_IMAGE:-python:3.10-buster}"
pip_index_url="${WAVEFORM_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
host_uid="$(id -u)"
host_gid="$(id -g)"

docker run --rm \
  --env HOST_UID="${host_uid}" \
  --env HOST_GID="${host_gid}" \
  --env PIP_DEFAULT_TIMEOUT=300 \
  --env PIP_INDEX_URL="${pip_index_url}" \
  --env PIP_RETRIES=10 \
  --volume "${project_root}:/workspace" \
  --workdir /workspace \
  "${build_image}" \
  bash -lc '
    set -euo pipefail
    python -m pip install \
      --requirement packaging/requirements-acquisition-v2-build.txt
    python -m PyInstaller \
      --clean \
      --noconfirm \
      --distpath dist/deepin20.9 \
      --workpath build/deepin20.9 \
      packaging/acquisition-v2.spec
    chown -R "${HOST_UID}:${HOST_GID}" \
      dist/deepin20.9 build/deepin20.9
  '

artifact_root="${project_root}/dist/deepin20.9"
source_revision="${WAVEFORM_SOURCE_REVISION:-$(git -C "${project_root}" rev-parse --short HEAD)}"
if [[ -n "$(git -C "${project_root}" status --porcelain --untracked-files=no)" ]]; then
  source_revision="${source_revision}-dirty"
fi
artifact_name="waveform-acquisition-v2-${source_revision}-deepin20.9-x86_64"
printf '%s\n' \
  "source_revision=${source_revision}" \
  "build_image=${build_image}" \
  "compatibility_baseline=Debian 10 / glibc 2.28 / x86_64" \
  > "${artifact_root}/waveform-acquisition-v2/BUILD-INFO"
tar --create --gzip \
  --file "${artifact_root}/${artifact_name}.tar.gz" \
  --directory "${artifact_root}" \
  waveform-acquisition-v2
(
  cd "${artifact_root}"
  sha256sum "${artifact_name}.tar.gz" \
    > "${artifact_name}.tar.gz.sha256"
)

echo "built ${artifact_root}/${artifact_name}.tar.gz"
