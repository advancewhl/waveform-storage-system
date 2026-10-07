"""Build and smoke-test an allowlisted Candidate V2 server source archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "packaging" / "server-formal-files.txt"
EXTRAS = {
    # The packaged server registry must match the formal GUI capture registry.
    "config/devices.json": ROOT / "config" / "devices.probe47.json",
    "run-server.sh": ROOT / "packaging" / "run-server-formal.sh",
    "requirements-server-formal.txt": ROOT / "packaging" / "requirements-server-formal.txt",
    "README.md": ROOT / "packaging" / "SERVER_FORMAL_PACKAGE.md",
}
FORBIDDEN_PARTS = {"legacy", "tests", "test", "demo", "archive", "build", "dist"}
FORBIDDEN_NAMES = {
    "experimental_main.py", "experimental_backend.py", "experimental_window.py",
    "legacy_main.py", "mainWindow.py", "mainWindow.ui", "operator_backend.py",
    "operator_window.py", "candidate_backend.py", "overlap.py",
    "zmq_client_dealer.py",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_to_copy() -> dict[str, Path]:
    paths = {}
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        relative = Path(name)
        if (
            relative.is_absolute() or ".." in relative.parts
            or FORBIDDEN_PARTS.intersection(relative.parts)
            or relative.name in FORBIDDEN_NAMES
        ):
            raise ValueError(f"forbidden package path: {name}")
        source = ROOT / relative
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(source)
        if name in paths:
            raise ValueError(f"duplicate package path: {name}")
        paths[name] = source
    paths.update(EXTRAS)
    return paths


def build(output_dir: Path) -> Path:
    sources = files_to_copy()
    with tempfile.TemporaryDirectory(prefix="server-formal-build-") as temporary:
        stage = Path(temporary) / "waveform-server-formal"
        stage.mkdir()
        for relative, source in sources.items():
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        (stage / "run-server.sh").chmod(0o755)

        manifest = "".join(
            f"{digest(stage / relative)}  {relative}\n"
            for relative in sorted(sources)
        )
        (stage / "MANIFEST.sha256").write_text(manifest, encoding="utf-8")
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.strip()
        (stage / "BUILD-INFO").write_text(
            f"source_revision={revision}\n"
            "source_state=working-tree\n"
            f"manifest_sha256={hashlib.sha256(manifest.encode()).hexdigest()}\n"
            "format=curated-formal-source\n",
            encoding="utf-8",
        )
        artifact_id = hashlib.sha256(manifest.encode()).hexdigest()[:12]
        output_dir.mkdir(parents=True, exist_ok=True)
        archive = output_dir / f"waveform-server-formal-{artifact_id}.tar.gz"
        with archive.open("wb") as raw_stream:
            with gzip.GzipFile(
                fileobj=raw_stream, filename="", mode="wb", mtime=0
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as bundle:
                    for path in sorted(stage.rglob("*")):
                        info = bundle.gettarinfo(
                            str(path), arcname=str(path.relative_to(stage.parent))
                        )
                        info.mtime = 0
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        if path.is_file():
                            with path.open("rb") as source_stream:
                                bundle.addfile(info, source_stream)
                        else:
                            bundle.addfile(info)

        extracted_root = Path(temporary) / "verify"
        extracted_root.mkdir()
        with tarfile.open(archive, "r:gz") as bundle:
            if sys.version_info >= (3, 12):
                bundle.extractall(extracted_root, filter="data")
            else:
                bundle.extractall(extracted_root)
        extracted = extracted_root / stage.name
        actual = {
            str(path.relative_to(extracted))
            for path in extracted.rglob("*") if path.is_file()
        }
        expected = set(sources) | {"MANIFEST.sha256", "BUILD-INFO"}
        if actual != expected:
            raise RuntimeError(f"package file mismatch: {actual ^ expected}")
        for relative in sources:
            if digest(extracted / relative) != digest(sources[relative]):
                raise RuntimeError(f"package checksum mismatch: {relative}")
        if any(part in FORBIDDEN_PARTS for name in actual for part in Path(name).parts):
            raise RuntimeError("forbidden directory entered archive")
        if any(Path(name).name in FORBIDDEN_NAMES for name in actual):
            raise RuntimeError("old, GUI, or demo file entered archive")

        environment = os.environ.copy()
        environment.update({
            "WAVEFORM_SERVER_PYTHON": sys.executable,
            "PYTHONPATH": str(extracted / "src"),
            "WAVEFORM_MONGODB_ENABLED": "false",
            "WAVEFORM_SERVER_ENDPOINT": "tcp://127.0.0.1:5001",
            "WAVEFORM_SERVER_REGISTRY": str(extracted / "config/devices.json"),
            "WAVEFORM_LOCAL_DATA_ROOT": str(Path(temporary) / "data"),
        })
        subprocess.run(
            [
                sys.executable, "-c",
                "import sys; import waveform_storage.acquisition.runtime; "
                "import waveform_storage.acquisition.server; "
                "assert not any(n.startswith('PyQt5') or "
                "n.startswith('matplotlib') or 'legacy' in n or 'demo' in n "
                "for n in sys.modules), 'GUI/old/demo module imported'",
            ],
            cwd=extracted, env=environment, check=True,
        )
        import paramiko
        host_key = paramiko.RSAKey.generate(1024)
        known_hosts = Path(temporary) / "known_hosts"
        registry_data = json.loads((extracted / "config/devices.json").read_text())
        approved = [
            device for device in registry_data["devices"]
            if device.get("enabled") and device.get("acquisition_enabled")
        ]
        if len(registry_data["devices"]) != 47 or len(approved) != 47:
            raise RuntimeError("server package must contain the approved 47-device registry")
        hosts = sorted({device["ssh_host"] for device in approved})
        known_hosts.write_text(
            "".join(
                f"{host} {host_key.get_name()} {host_key.get_base64()}\n"
                for host in hosts
            ),
            encoding="utf-8",
        )
        environment["WAVEFORM_DEVICE_SSH_KNOWN_HOSTS"] = str(known_hosts)
        subprocess.run(
            [str(extracted / "run-server.sh"), "--check"],
            cwd=extracted, env=environment, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        archive.with_name(archive.name + ".sha256").write_text(
            f"{digest(archive)}  {archive.name}\n", encoding="utf-8"
        )
        print(f"verified {len(actual)} files: {archive}")
        return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "outputs" / "server-formal"
    )
    arguments = parser.parse_args()
    build(arguments.output_dir)


if __name__ == "__main__":
    main()
