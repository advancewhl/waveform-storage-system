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
if not getattr(sys, "frozen", False) and str(SOURCE_ROOT) not in sys.path:
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
    parser.add_argument("--page", choices=("capture", "history", "spectrum"), default="capture")
    parser.add_argument("--spectrum-input", type=Path, help="打开本地数据并执行频谱分析")
    parser.add_argument("--spectrum-source", choices=("archiver", "hdf5"), default="hdf5")
    parser.add_argument("--spectrum-sample-rate-hz", type=float)
    parser.add_argument("--spectrum-profile", choices=("low_v1", "low_v2", "high_band"))
    parser.add_argument("--history-smoke-report", type=Path,
                        default=os.environ.get("WAVEFORM_HISTORY_SMOKE_REPORT"),
                        help="Opt-in query/read/export acceptance report; never starts capture.")
    parser.add_argument("--history-smoke-date", default=os.environ.get("WAVEFORM_HISTORY_SMOKE_DATE"))
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
    window._show_page(("capture", "history", "spectrum").index(args.page))
    if args.spectrum_input is not None:
        panel = window.spectrum_panel
        panel.source_combo.setCurrentIndex(1 if args.spectrum_source == "archiver" else 0)
        panel.path_edit.setText(str(args.spectrum_input))
        if args.spectrum_profile is not None:
            panel.profile_combo.setCurrentIndex(panel.profile_combo.findData(args.spectrum_profile))
        if args.spectrum_sample_rate_hz is not None:
            panel.sample_rate.setValue(args.spectrum_sample_rate_hz)
        window._show_page(2)
        panel.start_analysis()
    if args.screenshot is not None:
        app.processEvents()
        deadline = time.monotonic() + 30
        while (window._active_workers or window.spectrum_panel.job_id) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        if window.spectrum_panel.job_id:
            window.spectrum_panel.cancel_analysis()
            raise RuntimeError("frequency analysis did not finish before the screenshot deadline")
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.screenshot)):
            raise RuntimeError(f"failed to save screenshot: {args.screenshot}")
        window.close()
        if not window.thread_pool.waitForDone(30000):
            raise RuntimeError("background workers did not stop after screenshot")
        app.processEvents()
        return 0
    if args.history_smoke_report is not None:
        if args.ui_only or not args.history_smoke_date:
            raise ValueError("history acceptance requires a connected backend and an explicit date")
        from datetime import date
        date.fromisoformat(args.history_smoke_date)
        from waveform_storage.gui.history_smoke import install_history_smoke
        install_history_smoke(window, app, args.history_smoke_report, args.history_smoke_date, PROJECT_ROOT)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
