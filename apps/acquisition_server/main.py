"""Run the persistent waveform acquisition protocol-v2 service."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path


PROJECT_ROOT = (
    Path(sys._MEIPASS)
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[2]
)
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from waveform_storage.acquisition.runtime import (
    AcquisitionServerSettings,
    ProductionCaptureRunner,
    load_server_registry,
    read_timing_frequency,
)
from waveform_storage.acquisition.server import (
    CandidateAcquisitionServer,
    JsonTaskStore,
    PersistentTaskExecutor,
    RegistryPolicy,
)
from waveform_storage.logging_config import configure_logging, log_context
from waveform_storage.metadata.mongo_repository import MongoMetadataRepository
from waveform_storage.metadata.service import CaptureMetadataService
from waveform_storage.metadata.settings import MongoSettings


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
    mongo_settings = MongoSettings.from_env(os.environ)
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
        "waveform_mongodb": mongo_settings.public_values(),
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

    metadata_repository = None
    metadata = None
    if mongo_settings.enabled:
        try:
            metadata_repository = MongoMetadataRepository.connect(mongo_settings)
            metadata_repository.ping()
            metadata = CaptureMetadataService(
                metadata_repository,
                registry,
                settings.registry_path,
            )
            metadata.initialize()
            log_context(
                logger,
                status="connected",
                **context,
            ).info("MongoDB metadata catalog is ready")
        except Exception:
            if metadata_repository is not None:
                metadata_repository.close()
                metadata_repository = None
            if mongo_settings.required:
                raise
            log_context(
                logger,
                status="degraded",
                **context,
            ).exception("MongoDB metadata catalog is unavailable")

    store = JsonTaskStore(settings.task_state_dir)
    runner = ProductionCaptureRunner(settings, registry, stop_event)
    tasks = PersistentTaskExecutor(
        store,
        runner,
        canceller=runner.cancel,
        prepare=runner.prepare,
        metadata=metadata,
    )
    server = CandidateAcquisitionServer(
        settings.endpoint,
        RegistryPolicy(
            registry,
            max_duration_seconds=settings.max_capture_seconds,
            max_devices=settings.max_capture_devices,
        ),
        tasks,
        metadata=metadata,
        frequency_reader=lambda: read_timing_frequency(
            timeout_seconds=settings.epics_connect_timeout_seconds
        ),
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
        if metadata_repository is not None:
            metadata_repository.close()
        log_context(
            logger,
            status="stopped",
            **context,
        ).info("acquisition service stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
