"""Launch the scalable experimental operator UI without touching production."""

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
from waveform_storage.gui.error_dialogs import (
    OperationError,
    OperationErrorDialog,
)
from waveform_storage.gui.experimental_window import ExperimentalMainWindow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Render one screenshot and exit (works with QT_QPA_PLATFORM=offscreen).",
    )
    parser.add_argument(
        "--screenshot-error",
        type=Path,
        help="Render the structured error dialog and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = QApplication(sys.argv)
    registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
    window = ExperimentalMainWindow(registry)
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
        app.processEvents()
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.screenshot)):
            raise RuntimeError(f"failed to save screenshot: {args.screenshot}")
        return 0
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
