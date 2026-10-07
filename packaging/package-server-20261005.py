"""Update yesterday's verified service bundle with the repaired server binary."""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/deployment-pair-20261004/waveform-server-pv-20261004-5c9401ccfad2.tar.gz"
BINARY = ROOT / "dist/deepin20.9/waveform-acquisition-v2"
NAME = "waveform-server-retry-20261005"
OUTPUT = ROOT / "outputs/deployment-pair-20261005"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="waveform-server-retry-") as temporary:
        work = Path(temporary)
        with tarfile.open(BASE, "r:gz") as source:
            source.extractall(work, filter="data")
        old = work / "waveform-server-pv-20261004"
        stage = work / NAME
        old.rename(stage)
        shutil.rmtree(stage / "waveform-acquisition-v2")
        shutil.copytree(BINARY, stage / "waveform-acquisition-v2")
        (stage / "README.md").write_text(
            "# 波形采集服务 2026-10-05\n\n"
            "支持 500 Hz、受审区域内的部分 BPM 选择；新文件持续轮询至稳定，"
            "部分轮次成功时报告已保存轮次数。部署沿用现场 service.env、主机密钥、"
            "任务状态和数据目录。\n", encoding="utf-8"
        )
        (stage / "BUILD-INFO").write_text(
            "kind=formal-server-retry-20261005\n"
            "registry_devices=47\nexpected_points=20000\n"
            "server_frequency_source=timing_pv\n", encoding="utf-8"
        )
        files = sorted(path for path in stage.rglob("*") if path.is_file()
                       and path.name != "MANIFEST.sha256")
        manifest = "".join(
            f"{digest(path)}  {path.relative_to(stage)}\n" for path in files
        )
        (stage / "MANIFEST.sha256").write_text(manifest, encoding="utf-8")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        archive = OUTPUT / f"{NAME}-{hashlib.sha256(manifest.encode()).hexdigest()[:12]}.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(stage, arcname=NAME)
        archive.with_name(archive.name + ".sha256").write_text(
            f"{digest(archive)}  {archive.name}\n", encoding="utf-8"
        )
        print(archive)


if __name__ == "__main__":
    main()
