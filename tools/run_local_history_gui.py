#!/usr/bin/env python3
"""Launch the operator GUI against local MongoDB and local history files."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
OPERATOR_DIR = PROJECT_ROOT / "apps" / "operator_gui"
for entry in (SRC_DIR, OPERATOR_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from PyQt5.QtCore import QDate, QTimer
from PyQt5.QtWidgets import QApplication

from experimental_backend import ConnectedOperatorBackend
from waveform_storage.acquisition.server import (
    CandidateAcquisitionServer,
    JsonTaskStore,
    PersistentTaskExecutor,
    RegistryPolicy,
)
from waveform_storage.config.registry import load_registry
from waveform_storage.config.settings import AppSettings
from waveform_storage.gui.experimental_window import ExperimentalMainWindow
from waveform_storage.gui.operator_backend import OperatorMode
from waveform_storage.metadata.mongo_repository import MongoMetadataRepository
from waveform_storage.metadata.service import CaptureMetadataService
from waveform_storage.metadata.settings import MongoSettings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2025-11-12")
    parser.add_argument("--time", default="110023")
    parser.add_argument(
        "--registry",
        type=Path,
        default=PROJECT_ROOT / "config" / "devices.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected_date = QDate.fromString(args.date, "yyyy-MM-dd")
    if not selected_date.isValid():
        raise ValueError("--date must use YYYY-MM-DD")
    registry = load_registry(args.registry)
    repository = MongoMetadataRepository.connect(
        MongoSettings.from_env(os.environ)
    )
    repository.ping()
    metadata = CaptureMetadataService(repository, registry, args.registry)
    endpoint = f"tcp://127.0.0.1:{_available_port()}"
    os.environ["WAVEFORM_CANDIDATE_ZMQ_ENDPOINT"] = endpoint
    os.environ["WAVEFORM_CANDIDATE_LOCAL_FILES"] = "true"
    stop_event = threading.Event()

    with tempfile.TemporaryDirectory() as task_directory:
        tasks = PersistentTaskExecutor(
            JsonTaskStore(Path(task_directory)), lambda _request: ()
        )
        server = CandidateAcquisitionServer(
            endpoint,
            RegistryPolicy(registry),
            tasks,
            metadata=metadata,
        )
        server_thread = threading.Thread(
            target=server.serve_forever,
            args=(stop_event,),
            daemon=True,
        )
        server_thread.start()
        app = QApplication(sys.argv)
        backend = ConnectedOperatorBackend(
            AppSettings.from_env(project_root=PROJECT_ROOT),
            mode=OperatorMode.CANDIDATE_V2,
        )
        window = ExperimentalMainWindow(
            registry,
            backend=backend,
            journal_path=PROJECT_ROOT / "logs" / "operator-ui.jsonl",
        )
        window.history_date.setDate(selected_date)
        window._show_page(1)
        window.history_nav.setChecked(True)
        window.show()

        def select_and_align() -> None:
            index = window.history_time.findData(args.time)
            if index >= 0:
                window.history_time.setCurrentIndex(index)
                window.align_button.click()

        QTimer.singleShot(250, window.retrieve_button.click)
        QTimer.singleShot(1500, select_and_align)
        try:
            return app.exec()
        finally:
            stop_event.set()
            server_thread.join(timeout=3)
            server.close()
            tasks.close()
            repository.close()


def _available_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


if __name__ == "__main__":
    raise SystemExit(main())
