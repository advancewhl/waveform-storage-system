"""Build and smoke-test an allowlisted formal operator source archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "packaging" / "operator-formal-files.txt"
EXTRAS = {
    "run-operator.sh": ROOT / "packaging" / "run-operator-formal.sh",
    "requirements-operator-formal.txt": ROOT / "packaging" / "requirements-operator-formal.txt",
    "README.md": ROOT / "packaging" / "OPERATOR_FORMAL_PACKAGE.md",
}
FORBIDDEN_PARTS = {"legacy", "tests", "test", "demo", "archive", "build", "dist"}
FORBIDDEN_NAMES = {
    "experimental_main.py", "experimental_backend.py",
    "experimental_window.py", "legacy_main.py", "mainWindow.py",
    "mainWindow.ui", "operator_backend.py", "overlap.py",
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
            relative.is_absolute()
            or ".." in relative.parts
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
    with tempfile.TemporaryDirectory(prefix="operator-formal-build-") as temporary:
        stage = Path(temporary) / "waveform-operator-formal"
        stage.mkdir()
        for relative, source in sources.items():
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        (stage / "run-operator.sh").chmod(0o755)

        manifest = "".join(
            f"{digest(stage / relative)}  {relative}\n"
            for relative in sorted(sources)
        )
        (stage / "MANIFEST.sha256").write_text(manifest, encoding="utf-8")
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
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
        archive = output_dir / f"waveform-operator-formal-{artifact_id}.tar.gz"
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
            raise RuntimeError("old or demo file entered archive")

        environment = os.environ.copy()
        environment.update({
            "WAVEFORM_CANDIDATE_ZMQ_ENDPOINT": "tcp://127.0.0.1:5001",
            "QT_QPA_PLATFORM": "offscreen",
            "MPLCONFIGDIR": str(Path(temporary) / "matplotlib"),
            "WAVEFORM_GUI_PYTHON": sys.executable,
            "PYTHONPATH": os.pathsep.join(
                (str(extracted / "src"), str(extracted / "apps/operator_gui"))
            ),
        })
        subprocess.run(
            [
                sys.executable, "-c",
                "import sys; import candidate_backend; "
                "import waveform_storage.gui.operator_window; "
                "assert not any('legacy' in n or 'demo' in n or "
                "n.endswith('operator_backend') or 'experimental' in n "
                "for n in sys.modules), 'old/demo module imported'",
            ],
            cwd=extracted,
            env=environment,
            check=True,
        )
        screenshot = Path(temporary) / "formal.png"
        subprocess.run(
            [
                str(extracted / "run-operator.sh"),
                "--screenshot", str(screenshot),
            ],
            cwd=extracted,
            env=environment,
            check=True,
        )
        if not screenshot.is_file() or screenshot.stat().st_size == 0:
            raise RuntimeError("formal UI screenshot was not created")
        archive.with_name(archive.name + ".sha256").write_text(
            f"{digest(archive)}  {archive.name}\n", encoding="utf-8"
        )
        print(f"verified {len(actual)} files: {archive}")
        return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "outputs" / "operator-formal"
    )
    arguments = parser.parse_args()
    build(arguments.output_dir)


if __name__ == "__main__":
    main()
