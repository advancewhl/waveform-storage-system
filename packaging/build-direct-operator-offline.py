"""Build the formal direct-SSH GUI with local CPython 3.10 dependencies."""

from __future__ import annotations

import gzip
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "waveform-operator-direct-offline-20261004"
OUTPUT = ROOT / "outputs" / "deployment-pair-20261004"
REQUIREMENTS = ("PyQt5", "caproto", "h5py", "matplotlib", "numpy", "pandas", "scipy", "paramiko", "pyzmq")
COMPAT_CRYPTO = ROOT / "offline-packages/glibc228-crypto-compat-20261004.tar.gz"
COMPAT_CRYPTO_VERSION = "50.0.2"
SKIP_PARTS = {"__pycache__", "tests", "test", "examples", "ioc_examples", "demos", "demo"}
DIRECT_FILES = {
    "assets/waveform-operator.svg": ROOT / "assets/waveform-operator.svg",
    "README.md": ROOT / "packaging/direct-operator/README.md",
    "connection.env.example": ROOT / "packaging/direct-operator/connection.env.example",
    "install-desktop.sh": ROOT / "packaging/direct-operator/install-desktop.sh",
    "run-operator.sh": ROOT / "packaging/direct-operator/run-operator.sh",
    "requirements-operator-formal.txt": ROOT / "packaging/requirements-operator-formal.txt",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def formal_sources() -> dict[str, Path]:
    result = {}
    for line in (ROOT / "packaging/operator-formal-files.txt").read_text().splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe path: {name}")
        result[name] = ROOT / relative
    result.update(DIRECT_FILES)
    for name, path in result.items():
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"{name}: {path}")
    return result


def dependency_closure() -> list[metadata.Distribution]:
    pending = list(REQUIREMENTS)
    seen = {}
    while pending:
        name = pending.pop()
        key = canonicalize_name(name)
        if key in seen:
            continue
        distribution = metadata.distribution(name)
        seen[key] = distribution
        for requirement_text in distribution.requires or ():
            requirement = Requirement(requirement_text)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return [seen[key] for key in sorted(seen)]


def copy_dependencies(stage: Path, distributions: list[metadata.Distribution]) -> int:
    copied = 0
    vendor = stage / "vendor"
    for distribution in distributions:
        if distribution.files is None:
            raise RuntimeError(f"missing installed file list: {distribution.metadata['Name']}")
        for entry in distribution.files:
            relative = Path(str(entry))
            if ".." in relative.parts or SKIP_PARTS.intersection(relative.parts):
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            target = vendor / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if digest(target) != digest(source):
                    raise RuntimeError(f"conflicting dependency file: {relative}")
                continue
            shutil.copy2(source, target)
            copied += 1
    return copied


def apply_glibc228_crypto(stage: Path) -> None:
    """Use the locally verified crypto extensions compatible with GLIBC 2.28."""
    vendor = stage / "vendor"
    shutil.rmtree(vendor / "cryptography")
    for old_metadata in vendor.glob("cryptography-*.dist-info"):
        shutil.rmtree(old_metadata)
    allowed = {"cryptography", "bcrypt", "nacl", f"cryptography-{COMPAT_CRYPTO_VERSION}.dist-info"}
    with tarfile.open(COMPAT_CRYPTO, "r:gz") as archive:
        for member in archive.getmembers():
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] not in allowed:
                raise ValueError(f"unsafe compatibility archive member: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f"unexpected compatibility archive member: {member.name}")
            target = vendor / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def archive_stage(stage: Path, output: Path) -> None:
    with output.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in sorted(stage.rglob("*")):
                    info = archive.gettarinfo(str(path), arcname=str(path.relative_to(stage.parent)))
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    if path.is_file():
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)


def smoke_test(stage: Path, work: Path) -> None:
    screenshot = work / "formal-ui.png"
    environment = os.environ.copy()
    environment.update({
        "QT_QPA_PLATFORM": "offscreen",
        "MPLCONFIGDIR": str(work / "mpl"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    subprocess.run(
        [str(stage / "run-operator.sh"), "--ui-only", "--screenshot", str(screenshot)],
        cwd=stage, env=environment, check=True, timeout=90,
    )
    if screenshot.stat().st_size < 1000:
        raise RuntimeError("offline GUI screenshot was not created")
    subprocess.run(
        ["python3.10", "-S", "-c", "import PyQt5.QtCore, caproto, h5py, matplotlib, numpy, paramiko, zmq"],
        cwd=stage,
        env={**environment, "PYTHONPATH": str(stage / "vendor")},
        check=True, timeout=30,
    )


def main() -> None:
    if sys.version_info[:2] != (3, 10) or platform.machine() != "x86_64" or sys.platform != "linux":
        raise RuntimeError("build with the installed Linux x86_64 CPython 3.10 environment")
    distributions = dependency_closure()
    with tempfile.TemporaryDirectory(prefix="operator-offline-build-") as temporary:
        work = Path(temporary)
        stage = work / PACKAGE_NAME
        stage.mkdir()
        for relative, source in formal_sources().items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        count = copy_dependencies(stage, distributions)
        apply_glibc228_crypto(stage)
        for name in ("run-operator.sh", "install-desktop.sh"):
            (stage / name).chmod(0o755)
        (stage / "DEPENDENCIES.txt").write_text(
            "Bundled for Linux x86_64 / CPython 3.10 from local installed distributions:\n"
            + "".join(
                f"{d.metadata['Name']}=="
                f"{COMPAT_CRYPTO_VERSION if canonicalize_name(d.metadata['Name']) == 'cryptography' else d.version}\n"
                for d in distributions
            ),
            encoding="utf-8",
        )
        smoke_test(stage, work)
        shutil.rmtree(stage / "logs", ignore_errors=True)
        for cache in stage.rglob("__pycache__"):
            shutil.rmtree(cache)
        files = sorted(path for path in stage.rglob("*") if path.is_file())
        manifest = "".join(f"{digest(path)}  {path.relative_to(stage)}\n" for path in files)
        (stage / "MANIFEST.sha256").write_text(manifest, encoding="utf-8")
        (stage / "BUILD-INFO").write_text(
            f"kind=formal-operator-direct-ssh-offline\npython_abi=cp310\n"
            f"platform=linux-x86_64\nthird_party_files={count}\n"
            f"manifest_sha256={hashlib.sha256(manifest.encode()).hexdigest()}\n",
            encoding="utf-8",
        )
        OUTPUT.mkdir(parents=True, exist_ok=True)
        output = OUTPUT / f"{PACKAGE_NAME}-{hashlib.sha256(manifest.encode()).hexdigest()[:12]}.tar.gz"
        archive_stage(stage, output)
        with tarfile.open(output, "r:gz") as archive:
            actual = {member.name for member in archive.getmembers() if member.isfile()}
        expected = {f"{PACKAGE_NAME}/{path.relative_to(stage)}" for path in stage.rglob("*") if path.is_file()}
        if actual != expected:
            raise RuntimeError("archive file set does not match staging directory")
        output.with_name(output.name + ".sha256").write_text(
            f"{digest(output)}  {output.name}\n", encoding="utf-8",
        )
        print(f"verified offline GUI; {len(distributions)} distributions, {count} third-party files: {output}")


if __name__ == "__main__":
    main()
