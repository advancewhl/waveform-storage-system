"""Package the current server binary with the approved live configuration."""

from __future__ import annotations

import gzip
import hashlib
import shutil
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/deployment-pair-20261003/waveform-server-live-20261002-7f3dd6632857.tar.gz"
BINARY = ROOT / "dist/deepin20.9/waveform-acquisition-v2"
OUTPUT = ROOT / "outputs/deployment-pair-20261004"
NAME = "waveform-server-pv-20261004"
SKIP_DIRS = {"__pycache__", "tests", "test", "examples", "ioc_examples", "demos", "demo", "benchmarks"}


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if not BASE.is_file() or not (BINARY / "waveform-acquisition-v2").is_file():
        raise FileNotFoundError("approved baseline or new binary is missing")
    with tempfile.TemporaryDirectory(prefix="server-pv-package-") as temporary:
        work = Path(temporary)
        with tarfile.open(BASE, "r:gz") as archive:
            archive.extractall(work, filter="data")
        original = work / "waveform-server-live-20261002"
        stage = work / NAME
        original.rename(stage)
        shutil.rmtree(stage / "waveform-acquisition-v2")
        destination = stage / "waveform-acquisition-v2"
        shutil.copytree(
            BINARY, destination,
            ignore=lambda _folder, names: [name for name in names if name in SKIP_DIRS],
        )
        shutil.copy2(ROOT / "config/devices.probe47.json", stage / "config/devices.probe47.json")
        (stage / "README.md").write_text(
            "# 正式 waveform 服务：服务器读取频率 PV\n\n"
            "本包包含当前正式 47 台 / 每台 20000 点注册表、已核对的 SSH 主机公钥和新版服务二进制。"
            "前端调用 get_timing_frequency 时，服务在 waveform 服务器读取定时系统 PV，"
            "再返回当次实际频率；服务受理采集请求前会再次读取 PV，频率不符或读取失败则拒绝请求。"
            "部署时沿用现场已有的 service.env、任务状态目录和数据目录。"
            "切换 current 符号链接并重启 waveform-acquisition-v2.service 前先运行 --check。\n",
            encoding="utf-8",
        )
        (stage / "BUILD-INFO").write_text(
            "kind=formal-server-live-pv-frequency\n"
            "source_revision=b3f18a2-working-tree\n"
            "registry_devices=47\nexpected_points=20000\n"
            "timing_frequency_source=server-pv-query-and-capture-validation\n",
            encoding="utf-8",
        )
        manifest = "".join(
            f"{checksum(path)}  {path.relative_to(stage)}\n"
            for path in sorted(stage.rglob("*"))
            if path.is_file() and path.name != "MANIFEST.sha256"
        )
        (stage / "MANIFEST.sha256").write_text(manifest, encoding="utf-8")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        archive_path = OUTPUT / f"{NAME}-{hashlib.sha256(manifest.encode()).hexdigest()[:12]}.tar.gz"
        with archive_path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for path in sorted(stage.rglob("*")):
                        info = archive.gettarinfo(str(path), arcname=str(path.relative_to(stage.parent)))
                        info.uid = info.gid = info.mtime = 0
                        info.uname = info.gname = ""
                        if path.is_file():
                            with path.open("rb") as stream:
                                archive.addfile(info, stream)
                        else:
                            archive.addfile(info)
        archive_path.with_name(archive_path.name + ".sha256").write_text(
            f"{checksum(archive_path)}  {archive_path.name}\n", encoding="utf-8"
        )
        print(archive_path)


if __name__ == "__main__":
    main()
