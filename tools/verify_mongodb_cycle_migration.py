#!/usr/bin/env python3
"""Verify the legacy-to-multicycle index migration against real MongoDB."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from waveform_storage.metadata.models import WaveformFileMetadata
from waveform_storage.metadata.mongo_repository import MongoMetadataRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify MongoDB multicycle index migration in a temporary database"
    )
    parser.add_argument(
        "--uri",
        default=os.environ.get("WAVEFORM_MONGODB_URI"),
        help="MongoDB URI; defaults to WAVEFORM_MONGODB_URI",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.uri:
        raise SystemExit("--uri or WAVEFORM_MONGODB_URI is required")
    from pymongo import MongoClient
    from pymongo.errors import DuplicateKeyError

    database_name = f"wf_cycle_verify_{uuid4().hex}"
    client = MongoClient(args.uri, serverSelectionTimeoutMS=3000)
    database = client[database_name]
    files = database[MongoMetadataRepository.FILES]
    request_id = str(uuid4())
    device_id = "AC_LA_TEST_BI_SBPM01"
    database_created = False
    try:
        client.admin.command("ping")
        files.create_index(
            [("request_id", 1), ("device_id", 1)],
            unique=True,
            name="file_request_device_unique",
        )
        database_created = True
        files.insert_one(
            {
                "_id": f"{request_id}:{device_id}",
                "request_id": request_id,
                "device_id": device_id,
            }
        )

        repository = MongoMetadataRepository(database)
        repository.ensure_indexes()
        indexes = files.index_information()
        if "file_request_device_unique" in indexes:
            raise AssertionError("legacy uniqueness index was not removed")
        sequence_index = indexes.get("file_request_device_sequence_unique")
        expected_key = [
            ("request_id", 1),
            ("device_id", 1),
            ("capture_sequence", 1),
        ]
        if sequence_index is None or sequence_index.get("key") != expected_key:
            raise AssertionError("multicycle uniqueness index is missing or incorrect")
        migrated = files.find_one({"_id": f"{request_id}:{device_id}"})
        if migrated is None or migrated.get("capture_sequence") != 0:
            raise AssertionError("legacy document was not migrated to sequence zero")

        for sequence, time_sec in enumerate(("120721", "120733")):
            repository.upsert_waveform_file(
                _waveform_record(request_id, device_id, sequence, time_sec)
            )
        records = repository.find_waveform_files(
            storage_date_local="2026-08-26",
            device_ids=(device_id,),
        )
        if [item["capture_sequence"] for item in records] != [0, 1]:
            raise AssertionError("multicycle records were not both queryable")
        if [item["_id"] for item in records] != [
            f"{request_id}:{device_id}",
            f"{request_id}:{device_id}:S00001",
        ]:
            raise AssertionError("sequence record IDs are not backward-compatible")

        duplicate = dict(records[1])
        duplicate["_id"] = "forced-duplicate-id"
        try:
            files.insert_one(duplicate)
        except DuplicateKeyError:
            pass
        else:
            raise AssertionError("multicycle uniqueness index did not reject a duplicate")

        print("mongo_ping=ok")
        print("legacy_index_removed=true")
        print("legacy_document_sequence=0")
        print("multicycle_unique_index=ok")
        print("multicycle_records=2")
        print("duplicate_rejected=true")
    finally:
        if database_created:
            client.drop_database(database_name)
        client.close()
    return 0


def _waveform_record(
    request_id: str,
    device_id: str,
    sequence: int,
    time_sec: str,
) -> WaveformFileMetadata:
    captured_at = datetime(
        2026,
        8,
        26,
        12,
        7,
        21 + sequence * 12,
        tzinfo=timezone.utc,
    )
    return WaveformFileMetadata(
        request_id=request_id,
        device_id=device_id,
        capture_pv="PV:TEST",
        captured_at=captured_at,
        storage_date_local="2026-08-26",
        time_sec=time_sec,
        timezone_name="Asia/Shanghai",
        storage_id="migration_verify",
        path=f"/tmp/RAW-{time_sec}-{device_id}-S{sequence:05d}.h5",
        sample_count=1024,
        bunch_id_shape=(1, 1024),
        data_shape=(2, 1024),
        size_bytes=24_576,
        capture_sequence=sequence,
    )


if __name__ == "__main__":
    raise SystemExit(main())
