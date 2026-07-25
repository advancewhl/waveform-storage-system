"""Run the locally available offline quality gate."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True, env=_environment())


def _environment():
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    return environment


def main():
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
    run(sys.executable, "-m", "compileall", "-q", "apps", "src", "tests", "tools")
    run(sys.executable, "tools/device_registry.py", "validate",
        "--registry", "config/devices.json")
    run(sys.executable, "tools/device_registry.py", "check-legacy",
        "--registry", "config/devices.json",
        "--all", "config/pv_list_ALL.txt",
        "--acquisition", "config/acquisition_pv_list.txt")
    run(sys.executable, "tools/check_source_quality.py")
    run(sys.executable, "tools/secret_scan.py")
    run("git", "diff", "--check")
    run(sys.executable, "-c", "import waveform_storage")
    print("local quality gate: OK")


if __name__ == "__main__":
    main()
