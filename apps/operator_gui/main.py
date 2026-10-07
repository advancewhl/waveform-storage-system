"""Formal Candidate V2 operator GUI entry point."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(
    os.environ.get("WAVEFORM_OPERATOR_ROOT", "")
).resolve() if os.environ.get("WAVEFORM_OPERATOR_ROOT") else Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from PyQt5.QtWidgets import QApplication

from waveform_storage.config.registry import load_registry
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.operator_contract import OperatorMode
from waveform_storage.gui.operator_window import OperatorMainWindow

from candidate_backend import CandidateOperatorBackend


class UiOnlyBackend:
    """No-network backend for inspecting the formal interface offline."""

    mode = OperatorMode.CANDIDATE_V2

    def __getattr__(self, name: str):
        raise RuntimeError(f"{name} is unavailable in UI-only mode")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Formal Candidate V2 operator GUI")
    parser.add_argument(
        "--mode", choices=("candidate_v2",), default="candidate_v2"
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=PROJECT_ROOT / "config" / "devices.operator.json",
    )
    parser.add_argument(
        "--capture-registry",
        type=Path,
        default=PROJECT_ROOT / "config" / "devices.probe47.json",
    )
    parser.add_argument(
        "--ui-only", action="store_true",
        help="Open the formal interface offline with service actions disabled.",
    )
    parser.add_argument(
        "--screenshot", type=Path,
        help="Save a UI preview and exit without sending any capture request.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = QApplication(sys.argv)
    registry = load_registry(args.registry)
    capture_registry = load_registry(args.capture_registry)
    backend = (
        UiOnlyBackend()
        if args.ui_only
        else CandidateOperatorBackend(AppSettings.from_env(project_root=PROJECT_ROOT))
    )
    window = OperatorMainWindow(
        registry,
        backend=backend,
        capture_registry=capture_registry,
        ui_only=args.ui_only,
        journal_path=(
            Path(os.environ["WAVEFORM_OPERATOR_STATE_DIR"]) / "operator-ui.jsonl"
            if os.environ.get("WAVEFORM_OPERATOR_STATE_DIR")
            else PROJECT_ROOT / "logs" / "operator-ui.jsonl"
        ),
    )
    window.show()
    if args.screenshot is not None:
        app.processEvents()
        deadline = time.monotonic() + 30
        while window._active_workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.screenshot)):
            raise RuntimeError(f"failed to save screenshot: {args.screenshot}")
        window.close()
        if not window.thread_pool.waitForDone(30000):
            raise RuntimeError("background workers did not stop after screenshot")
        app.processEvents()
        return 0
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
