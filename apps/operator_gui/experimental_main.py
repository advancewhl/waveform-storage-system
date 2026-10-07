"""Shared operator window launcher; standalone defaults to offline demo."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt5.QtWidgets import QApplication

from waveform_storage.config.registry import load_registry
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.error_dialogs import (
    OperationError,
    OperationErrorDialog,
)
from waveform_storage.gui.experimental_window import ExperimentalMainWindow
from waveform_storage.gui.operator_backend import (
    DemoOperatorBackend,
    OperatorMode,
)
from waveform_storage.gui.plotting import plot_device_waveform

from experimental_backend import ConnectedOperatorBackend


def parse_args(
    *,
    default_mode: str = OperatorMode.DEMO.value,
    default_registry: Path | None = None,
    default_capture_registry: Path | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    default_registry_path = (
        PROJECT_ROOT / "config" / "devices.candidate.json"
        if (PROJECT_ROOT / "config" / "devices.candidate.json").exists()
        else PROJECT_ROOT / "config" / "devices.json"
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=default_registry or default_registry_path,
        help=(
            "Device registry to load. The integrated branch defaults to the "
            "383-device candidate when present."
        ),
    )
    parser.add_argument(
        "--capture-registry",
        type=Path,
        default=default_capture_registry,
        help="Server-matched registry used only to validate real capture requests.",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Render one screenshot and exit (works with QT_QPA_PLATFORM=offscreen).",
    )
    parser.add_argument(
        "--screenshot-view",
        choices=("capture", "distribution", "device"),
        default="capture",
        help="Select the demo page prepared before --screenshot is captured.",
    )
    parser.add_argument(
        "--screenshot-error",
        type=Path,
        help="Render the structured error dialog and exit.",
    )
    parser.add_argument(
        "--mode",
        choices=tuple(item.value for item in OperatorMode),
        default=default_mode,
        help=(
            "demo is offline and safe; legacy and candidate_v2 are explicit "
            "connected modes"
        ),
    )
    return parser.parse_args()


def main(
    *,
    default_mode: str = OperatorMode.DEMO.value,
    default_registry: Path | None = None,
    default_capture_registry: Path | None = None,
) -> int:
    args = parse_args(
        default_mode=default_mode,
        default_registry=default_registry,
        default_capture_registry=default_capture_registry,
    )
    app = QApplication(sys.argv)
    registry_path = args.registry
    if not registry_path.is_absolute():
        registry_path = PROJECT_ROOT / registry_path
    registry = load_registry(registry_path)
    capture_registry = None
    if args.capture_registry is not None:
        capture_registry_path = args.capture_registry
        if not capture_registry_path.is_absolute():
            capture_registry_path = PROJECT_ROOT / capture_registry_path
        capture_registry = load_registry(capture_registry_path)
    mode = OperatorMode(args.mode)
    if mode is OperatorMode.DEMO:
        backend = DemoOperatorBackend(registry)
    else:
        backend = ConnectedOperatorBackend(
            AppSettings.from_env(project_root=PROJECT_ROOT),
            mode=mode,
        )
    window = ExperimentalMainWindow(
        registry,
        backend=backend,
        capture_registry=capture_registry,
        journal_path=PROJECT_ROOT / "logs" / "operator-ui.jsonl",
    )
    window.setProperty("registryPath", str(registry_path))
    window.show()
    if args.screenshot_error:
        dialog = OperationErrorDialog(
            OperationError(
                title="数据检索失败",
                summary="无法连接波形存储服务。",
                stage="历史数据检索 / SSH",
                code="STORAGE_CONNECTION_FAILED",
                suggestion="检查存储服务地址、网络和凭据配置后重试。",
                details=(
                    "host: configured storage host\n"
                    "operation: list remote capture times\n"
                    "timeout: 10 s\n"
                    "credentials: [redacted]"
                ),
            ),
            window,
        )
        dialog.show()
        app.processEvents()
        args.screenshot_error.parent.mkdir(parents=True, exist_ok=True)
        if not dialog.grab().save(str(args.screenshot_error)):
            raise RuntimeError(
                f"failed to save screenshot: {args.screenshot_error}"
            )
        return 0
    if args.screenshot:
        if args.screenshot_view != "capture":
            if mode is not OperatorMode.DEMO:
                raise ValueError("history screenshots require --mode demo")
            selected_date = window._selected_date()
            time_sec = backend.retrieve_times(selected_date)[-1]
            window.history_time.clear()
            window.history_time.addItem(
                f"{time_sec[0:2]}:{time_sec[2:4]}:{time_sec[4:6]}",
                time_sec,
            )
            result = backend.align(
                selected_date,
                time_sec,
                window._query_devices(),
            )
            window.load_alignment_result(result, window._query_devices())
            window._show_page(1)
            window.history_nav.setChecked(True)
            if args.screenshot_view == "device":
                window.plot_mode.setCurrentIndex(1)
                device_id = window.plot_device.currentData()
                device_result = backend.align(
                    selected_date,
                    time_sec,
                    (device_id,),
                )
                window.device_waveform_result = device_result
                window.device_waveform_key = (
                    selected_date.isoformat(),
                    time_sec,
                    device_id,
                )
                window._install_figure(
                    plot_device_waveform(device_result, device_id)
                )
        app.processEvents()
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.screenshot)):
            raise RuntimeError(f"failed to save screenshot: {args.screenshot}")
        return 0
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
