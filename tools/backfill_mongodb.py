#!/usr/bin/env python3
"""Validate and import historical waveform HDF5 metadata into MongoDB."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from waveform_storage.config.registry import load_registry
from waveform_storage.metadata.backfill import import_historical_directory
from waveform_storage.metadata.mongo_repository import MongoMetadataRepository
from waveform_storage.metadata.settings import MongoSettings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import one YYYYMMDD HDF5 directory into MongoDB"
    )
    parser.add_argument("source", type=Path, help="local directory inspected for HDF5")
    parser.add_argument(
        "--registry",
        type=Path,
        default=PROJECT_ROOT / "config" / "devices.json",
    )
    parser.add_argument(
        "--catalog-directory",
        type=Path,
        help="directory path stored in MongoDB; defaults to resolved source",
    )
    parser.add_argument("--storage-id", default="shine_data_primary")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry = load_registry(args.registry)
    if args.dry_run:
        repository = _DryRunRepository()
    else:
        settings = MongoSettings.from_env(os.environ)
        repository = MongoMetadataRepository.connect(settings)
        repository.ping()
        repository.ensure_indexes()
    try:
        summary = import_historical_directory(
            repository,
            registry,
            args.source,
            catalog_directory=args.catalog_directory,
            storage_id=args.storage_id,
            dry_run=args.dry_run,
        )
    finally:
        close = getattr(repository, "close", None)
        if close is not None:
            close()
    print(f"date={summary.storage_date_local}")
    print(f"scanned_files={summary.scanned_files}")
    print(f"imported_files={summary.imported_files}")
    print(f"capture_times={','.join(summary.capture_times)}")
    print(f"devices={len(summary.device_ids)}")
    return 0


class _DryRunRepository:
    def upsert_waveform_file(self, _record) -> None:
        raise AssertionError("dry-run must not write metadata")


if __name__ == "__main__":
    raise SystemExit(main())
