"""Package the verified onedir executable for an installed desktop client."""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAME = "waveform-operator-direct-offline-20261004"
OUTPUT = ROOT / "outputs/deployment-pair-20261005"
BINARY = ROOT / "dist/operator-debian10/waveform-operator"
FILES = {
    "config/devices.operator.json": ROOT / "config/devices.operator.json",
    "config/devices.probe47.json": ROOT / "config/devices.probe47.json",
    "assets/waveform-operator.svg": ROOT / "assets/waveform-operator.svg",
    "security/known_hosts": ROOT / "packaging/direct-operator/known_hosts",
    "run-operator.sh": ROOT / "packaging/direct-operator/run-operator.sh",
    "register-account.sh": ROOT / "packaging/direct-operator/register-account.sh",
    "install-desktop.sh": ROOT / "packaging/direct-operator/install-desktop.sh",
    "connection.env.example": ROOT / "packaging/direct-operator/connection.env.example",
    "README.md": ROOT / "packaging/direct-operator/README.md",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if not (BINARY / "waveform-operator").is_file():
        raise FileNotFoundError("build the Debian 10 executable first")
    with tempfile.TemporaryDirectory(prefix="waveform-executable-package-") as work:
        stage = Path(work) / NAME
        stage.mkdir()
        shutil.copytree(BINARY, stage / "bin/waveform-operator")
        for name, source in FILES.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for name in ("run-operator.sh", "register-account.sh", "install-desktop.sh"):
            (stage / name).chmod(0o755)
        files = sorted(path for path in stage.rglob("*") if path.is_file())
        manifest = "".join(
            f"{digest(path)}  {path.relative_to(stage)}\n" for path in files
        )
        (stage / "MANIFEST.sha256").write_text(manifest, encoding="utf-8")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        archive = OUTPUT / f"{NAME}-executable-{hashlib.sha256(manifest.encode()).hexdigest()[:12]}.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(stage, arcname=NAME)
        archive.with_name(archive.name + ".sha256").write_text(
            f"{digest(archive)}  {archive.name}\n", encoding="utf-8"
        )
        print(archive)


if __name__ == "__main__":
    main()
