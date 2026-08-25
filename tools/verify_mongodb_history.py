#!/usr/bin/env python3
"""Run a real MongoDB -> candidate-v2 -> HDF5 alignment smoke test."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from waveform_storage.acquisition.server import (
    CandidateAcquisitionServer,
    JsonTaskStore,
    PersistentTaskExecutor,
    RegistryPolicy,
)
from waveform_storage.config.registry import load_registry
from waveform_storage.io.hdf5_io import read_single_device_hdf5
from waveform_storage.metadata.mongo_repository import MongoMetadataRepository
from waveform_storage.metadata.service import CaptureMetadataService
from waveform_storage.metadata.settings import MongoSettings
from waveform_storage.processing.alignment import align_devices
from waveform_storage.transport.zmq_transport import ZmqTransport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("storage_date_local", help="date in YYYY-MM-DD form")
    parser.add_argument("time_sec", help="capture time in HHMMSS form")
    parser.add_argument(
        "--registry",
        type=Path,
        default=PROJECT_ROOT / "config" / "devices.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry = load_registry(args.registry)
    repository = MongoMetadataRepository.connect(
        MongoSettings.from_env(os.environ)
    )
    repository.ping()
    metadata = CaptureMetadataService(repository, registry, args.registry)
    endpoint = f"tcp://127.0.0.1:{_available_port()}"
    stop_event = threading.Event()
    with tempfile.TemporaryDirectory() as task_directory:
        tasks = PersistentTaskExecutor(
            JsonTaskStore(Path(task_directory)),
            lambda _request: (),
        )
        server = CandidateAcquisitionServer(
            endpoint,
            RegistryPolicy(registry),
            tasks,
            metadata=metadata,
        )
        thread = threading.Thread(
            target=server.serve_forever,
            args=(stop_event,),
            daemon=True,
        )
        thread.start()
        try:
            transport = ZmqTransport(endpoint, timeout_ms=3000)
            times_reply = transport.list_capture_times(
                str(uuid4()), args.storage_date_local
            )
            _accepted(times_reply)
            files_reply = transport.find_waveform_files(
                str(uuid4()),
                args.storage_date_local,
                args.time_sec,
                tuple(device.id for device in registry.devices),
            )
            _accepted(files_reply)
            files = files_reply.payload.get("files", [])
            paths = [Path(item["path"]) for item in files]
            missing = [path for path in paths if not path.is_file()]
            if missing:
                raise RuntimeError(
                    f"catalog contains {len(missing)} path(s) unavailable locally"
                )
            result = align_devices(
                [read_single_device_hdf5(path) for path in paths]
            )
            print(f"candidate_endpoint={endpoint}")
            print(f"capture_times={','.join(times_reply.payload['times'])}")
            print(f"catalog_files={len(files)}")
            print(f"aligned_devices={len(result.device_ids)}")
            print(f"common_bunch_ids={len(result.bunch_ids)}")
            print(f"skipped_devices={len(result.skipped_devices)}")
        finally:
            stop_event.set()
            thread.join(timeout=3)
            server.close()
            tasks.close()
            repository.close()
    return 0


def _accepted(reply) -> None:
    if not reply.accepted:
        raise RuntimeError(f"{reply.error_code}: {reply.message}")


def _available_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


if __name__ == "__main__":
    raise SystemExit(main())
