"""Run the persistent waveform acquisition protocol-v2 service."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from waveform_storage.acquisition.runtime import (
    AcquisitionServerSettings,
    ProductionCaptureRunner,
    load_server_registry,
)
from waveform_storage.acquisition.server import (
    CandidateAcquisitionServer,
    JsonTaskStore,
    PersistentTaskExecutor,
    RegistryPolicy,
)
from waveform_storage.logging_config import configure_logging, log_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Waveform acquisition protocol-v2 service"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration and exit without opening a socket",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.umask(0o077)
    logger = configure_logging(logging.INFO)
    settings = AcquisitionServerSettings.from_env(
        project_root=PROJECT_ROOT
    )
    registry = load_server_registry(settings)
    settings.task_state_dir.mkdir(
        parents=True, exist_ok=True, mode=0o700
    )
    settings.staging_root.mkdir(
        parents=True, exist_ok=True, mode=0o700
    )
    settings.local_data_root.mkdir(parents=True, exist_ok=True)
    context = {
        "request_id": None,
        "task_id": None,
        "stage": "startup",
        "device": None,
        "duration": None,
        "error_type": None,
        "waveform_settings": settings.public_values(),
        "waveform_registry_devices": len(registry.devices),
        "waveform_acquisition_devices": sum(
            item.acquisition_enabled for item in registry.devices
        ),
    }
    log_context(
        logger,
        status="checked" if args.check else "starting",
        **context,
    ).info("acquisition service configuration is valid")
    if args.check:
        return 0

    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    store = JsonTaskStore(settings.task_state_dir)
    runner = ProductionCaptureRunner(settings, registry, stop_event)
    tasks = PersistentTaskExecutor(
        store,
        runner,
        canceller=runner.cancel,
        prepare=runner.prepare,
    )
    server = CandidateAcquisitionServer(
        settings.endpoint,
        RegistryPolicy(
            registry,
            max_duration_seconds=settings.max_capture_seconds,
            max_devices=settings.max_capture_devices,
        ),
        tasks,
    )
    log_context(
        logger,
        status="running",
        **context,
    ).info("acquisition service is listening")
    try:
        server.serve_forever(stop_event)
    finally:
        stop_event.set()
        server.close()
        log_context(
            logger,
            status="stopped",
            **context,
        ).info("acquisition service stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
