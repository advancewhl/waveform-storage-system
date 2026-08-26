from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
from uuid import uuid4

import h5py
import numpy as np

from waveform_storage.acquisition.protocol import (
    PROTOCOL_VERSION,
    CaptureRequest,
    CaptureResponse,
)
from waveform_storage.application.capture_service import DeviceCaptureResult
from waveform_storage.config.registry import load_registry
from waveform_storage.metadata.memory_repository import (
    InMemoryMetadataRepository,
)
from waveform_storage.metadata.backfill import (
    HistoricalImportError,
    import_historical_directory,
)
from waveform_storage.metadata.models import (
    CaptureTaskMetadata,
    MetadataValidationError,
    RegistrySnapshotMetadata,
    StatusEvent,
    WaveformFileMetadata,
)
from waveform_storage.metadata.mongo_repository import MongoMetadataRepository
from waveform_storage.metadata.repository import MetadataRecordNotFound
from waveform_storage.metadata.settings import MongoSettings, MongoSettingsError
from waveform_storage.metadata.service import CaptureMetadataService


NOW = datetime(2026, 8, 25, 1, 2, 3, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def task(status="accepted"):
    return CaptureTaskMetadata(
        request_id="request-1",
        frequency_hz=100,
        duration_seconds=600,
        area_ids=("AC_LA_INJ1",),
        device_ids=("BPM_A", "BPM_B"),
        registry_id="sha256:registry",
        registry_schema_version=1,
        status=status,
        status_history=(StatusEvent(status, NOW),),
        created_at=NOW,
    )


def waveform(device_id="BPM_A", *, minute=0, status="completed"):
    return WaveformFileMetadata(
        request_id="request-1",
        device_id=device_id,
        capture_pv=f"PV:{device_id}",
        captured_at=NOW + timedelta(minutes=minute),
        storage_date_local="2026-08-25",
        time_sec=f"010{minute}03",
        timezone_name="Asia/Shanghai",
        storage_id="shine_data_primary",
        path=f"AC/LA/20260825/raw/BIData/{device_id}.h5",
        sample_count=1024,
        bunch_id_shape=(1, 1024),
        data_shape=(2, 1024),
        size_bytes=24_576,
        status=status,
        created_at=NOW,
    )


class MetadataModelTests(unittest.TestCase):
    def test_task_document_uses_request_id_and_bson_datetimes(self):
        document = task().to_document()
        self.assertEqual(document["_id"], "request-1")
        self.assertEqual(document["registry"]["registry_id"], "sha256:registry")
        self.assertIs(document["created_at"], NOW)
        self.assertEqual(document["result_summary"]["requested"], 2)

    def test_waveform_record_contains_path_without_waveform_arrays(self):
        document = waveform().to_document()
        self.assertEqual(document["_id"], "request-1:BPM_A")
        self.assertEqual(document["capture_sequence"], 0)
        self.assertEqual(document["file"]["storage_id"], "shine_data_primary")
        self.assertEqual(document["datasets"]["Data"]["shape"], [2, 1024])
        self.assertNotIn("waveform", document)

    def test_naive_datetime_and_duplicate_devices_are_rejected(self):
        with self.assertRaises(MetadataValidationError):
            StatusEvent("accepted", datetime(2026, 8, 25))
        with self.assertRaises(MetadataValidationError):
            CaptureTaskMetadata(
                "request",
                100,
                60,
                ("AREA",),
                ("A", "A"),
                "registry",
                1,
                created_at=NOW,
            )

    def test_registry_snapshot_is_compact_and_credential_free_by_contract(self):
        snapshot = RegistrySnapshotMetadata(
            "sha256:registry",
            1,
            "original devices",
            ({"id": "BPM_A", "capture_pv": "PV:A"},),
            1,
            "devices.json",
            created_at=NOW,
        ).to_document()
        self.assertEqual(snapshot["device_count"], 1)
        self.assertNotIn("password", str(snapshot).lower())
        with self.assertRaises(MetadataValidationError):
            RegistrySnapshotMetadata(
                "sha256:unsafe",
                1,
                "unsafe",
                ({"id": "BPM_A", "ssh_password": "secret"},),
                1,
                "devices.json",
                created_at=NOW,
            )


class MemoryRepositoryTests(unittest.TestCase):
    def test_task_lifecycle_and_missing_update(self):
        repository = InMemoryMetadataRepository()
        repository.upsert_task(task())
        repository.update_task_status(
            "request-1",
            "running",
            timestamp=NOW + timedelta(seconds=1),
        )
        repository.update_task_status(
            "request-1",
            "succeeded",
            timestamp=NOW + timedelta(seconds=2),
            result_summary={"requested": 2, "succeeded": 2, "failed": 0},
        )
        record = repository.tasks["request-1"]
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(len(record["status_history"]), 3)
        with self.assertRaises(MetadataRecordNotFound):
            repository.update_task_status("missing", "failed", timestamp=NOW)

    def test_file_query_filters_orders_and_excludes_failures(self):
        repository = InMemoryMetadataRepository()
        repository.upsert_waveform_file(waveform("BPM_B", minute=1))
        repository.upsert_waveform_file(waveform("BPM_A"))
        repository.upsert_waveform_file(waveform("BPM_C", status="failed"))
        records = repository.find_waveform_files(
            storage_date_local="2026-08-25",
            device_ids=("BPM_A", "BPM_B", "BPM_C"),
        )
        self.assertEqual([item["device_id"] for item in records], ["BPM_A", "BPM_B"])
        self.assertEqual(repository.list_capture_times("2026-08-25"), ("010003", "010103"))

    def test_capture_metadata_service_records_hdf5_path_for_queries(self):
        registry_path = PROJECT_ROOT / "config" / "devices.json"
        registry = load_registry(registry_path)
        device = registry.devices[0]
        repository = InMemoryMetadataRepository()
        service = CaptureMetadataService(
            repository,
            registry,
            registry_path,
        )
        service.initialize()
        request = CaptureRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            "start_capture",
            100,
            1,
            (device.section,),
            (device.id,),
        )
        accepted = CaptureResponse(
            PROTOCOL_VERSION,
            request.request_id,
            True,
            "accepted",
            "accepted",
        )
        running = CaptureResponse(
            PROTOCOL_VERSION,
            request.request_id,
            True,
            "running",
            "running",
        )
        succeeded = CaptureResponse(
            PROTOCOL_VERSION,
            request.request_id,
            True,
            "succeeded",
            "succeeded",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"RAW-120721-{device.id}-S00000.h5"
            with h5py.File(path, "w") as h5:
                h5.create_dataset(
                    "BunchIDData",
                    data=np.asarray([[1, 2]], dtype=np.uint64),
                )
                h5.create_dataset(
                    "Data",
                    data=np.asarray([[1, 2], [3, 4]], dtype=np.int64),
                )
            service.record_created(request, accepted)
            service.record_status(request, running)
            service.record_status(
                request,
                succeeded,
                device_results=(DeviceCaptureResult(device.id, True, path),),
            )

        stored = next(iter(repository.files.values()))
        self.assertEqual(stored["time_sec"], "120721")
        self.assertEqual(stored["sample_count"], 2)
        self.assertEqual(stored["file"]["path"], str(path))
        self.assertEqual(
            service.list_capture_times(stored["storage_date_local"]),
            ("120721",),
        )
        self.assertIn(service.registry_id, repository.registries)

    def test_capture_metadata_service_records_every_capture_cycle(self):
        registry_path = PROJECT_ROOT / "config" / "devices.json"
        registry = load_registry(registry_path)
        device = registry.devices[0]
        repository = InMemoryMetadataRepository()
        service = CaptureMetadataService(repository, registry, registry_path)
        request = CaptureRequest(
            PROTOCOL_VERSION,
            str(uuid4()),
            "start_capture",
            100,
            30,
            (device.section,),
            (device.id,),
        )
        accepted = CaptureResponse(
            PROTOCOL_VERSION,
            request.request_id,
            True,
            "accepted",
            "accepted",
        )
        succeeded = CaptureResponse(
            PROTOCOL_VERSION,
            request.request_id,
            True,
            "succeeded",
            "succeeded",
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for sequence, time_sec in enumerate(("120721", "120733")):
                path = Path(directory) / (
                    f"RAW-{time_sec}-{device.id}-S{sequence:05d}.h5"
                )
                with h5py.File(path, "w") as h5:
                    h5.create_dataset("BunchIDData", data=np.asarray([[1, 2]]))
                    h5.create_dataset("Data", data=np.asarray([[1, 2], [3, 4]]))
                paths.append(path)
            service.record_created(request, accepted)
            service.record_status(
                request,
                succeeded,
                device_results=(
                    DeviceCaptureResult(
                        device.id,
                        True,
                        paths[-1],
                        output_paths=tuple(paths),
                        attempted_cycles=2,
                        successful_cycles=2,
                    ),
                ),
            )

        self.assertEqual(len(repository.files), 2)
        self.assertEqual(
            sorted(item["capture_sequence"] for item in repository.files.values()),
            [0, 1],
        )
        local_date = next(iter(repository.files.values()))["storage_date_local"]
        self.assertEqual(
            service.list_capture_times(local_date),
            ("120721", "120733"),
        )

    def test_historical_directory_import_is_validated_and_idempotent(self):
        registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        device = registry.devices[0]
        repository = InMemoryMetadataRepository()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "20251112"
            source.mkdir()
            filename = f"RAW-110023-{device.id}-S00000.h5"
            path = source / filename
            with h5py.File(path, "w") as h5:
                h5.attrs["Device"] = device.id
                h5.create_dataset(
                    "BunchIDData", data=np.asarray([[1, 2]], dtype=np.uint64)
                )
                h5.create_dataset(
                    "Data", data=np.asarray([[3, 4], [5, 6]], dtype=np.int64)
                )
            summary = import_historical_directory(
                repository,
                registry,
                source,
                catalog_directory="/SHINE_Data/AC/LA/20251112",
            )
            import_historical_directory(
                repository,
                registry,
                source,
                catalog_directory="/SHINE_Data/AC/LA/20251112",
            )

        self.assertEqual(summary.storage_date_local, "2025-11-12")
        self.assertEqual(summary.capture_times, ("110023",))
        self.assertEqual(len(repository.files), 1)
        stored = next(iter(repository.files.values()))
        self.assertEqual(stored["file"]["path"], f"/SHINE_Data/AC/LA/20251112/{filename}")
        self.assertEqual(len(stored["file"]["sha256"]), 64)

    def test_historical_import_rejects_device_attribute_mismatch(self):
        registry = load_registry(PROJECT_ROOT / "config" / "devices.json")
        device = registry.devices[0]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "20251112"
            source.mkdir()
            path = source / f"RAW-110023-{device.id}-S00000.h5"
            with h5py.File(path, "w") as h5:
                h5.attrs["Device"] = "WRONG_DEVICE"
                h5.create_dataset("BunchIDData", data=np.asarray([[1]]))
                h5.create_dataset("Data", data=np.asarray([[2], [3]]))
            with self.assertRaises(HistoricalImportError):
                import_historical_directory(
                    InMemoryMetadataRepository(), registry, source
                )


class FakeResult:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


class FakeCursor(list):
    def sort(self, _specification):
        return self


class FakeCollection:
    def __init__(self):
        self.indexes = []
        self.existing_indexes = {}
        self.dropped_indexes = []
        self.replacements = []
        self.updates = []
        self.update_many_calls = []
        self.query_result = FakeCursor()
        self.distinct_result = []

    def create_index(self, keys, **options):
        self.indexes.append((keys, options))

    def index_information(self):
        return self.existing_indexes

    def drop_index(self, name):
        self.dropped_indexes.append(name)

    def replace_one(self, query, document, *, upsert):
        self.replacements.append((query, document, upsert))

    def update_one(self, query, update):
        self.updates.append((query, update))
        return FakeResult()

    def update_many(self, query, update):
        self.update_many_calls.append((query, update))
        return FakeResult()

    def find(self, _query):
        return self.query_result

    def distinct(self, _field, _query):
        return self.distinct_result


class FakeDatabase:
    def __init__(self):
        self.collections = {}
        self.commands = []

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def command(self, name):
        self.commands.append(name)


class MongoRepositoryContractTests(unittest.TestCase):
    def test_indexes_are_explicit_and_writes_are_idempotent_upserts(self):
        database = FakeDatabase()
        repository = MongoMetadataRepository(database)
        repository.ensure_indexes()
        repository.upsert_task(task())
        repository.upsert_waveform_file(waveform())
        self.assertEqual(len(repository.files.indexes), 5)
        self.assertTrue(repository.tasks.replacements[0][2])
        self.assertTrue(repository.files.replacements[0][2])
        unique_names = {
            options["name"]
            for _keys, options in repository.files.indexes
            if options.get("unique")
        }
        self.assertEqual(
            unique_names,
            {"file_request_device_sequence_unique", "file_storage_path_unique"},
        )

    def test_legacy_file_uniqueness_index_is_replaced(self):
        database = FakeDatabase()
        repository = MongoMetadataRepository(database)
        repository.files.existing_indexes["file_request_device_unique"] = {
            "key": [("request_id", 1), ("device_id", 1)],
            "unique": True,
        }

        repository.ensure_indexes()

        self.assertEqual(
            repository.files.dropped_indexes,
            ["file_request_device_unique"],
        )
        self.assertEqual(
            repository.files.update_many_calls,
            [
                (
                    {"capture_sequence": {"$exists": False}},
                    {"$set": {"capture_sequence": 0}},
                )
            ],
        )

    def test_status_update_pushes_history_and_ping_uses_admin_command(self):
        database = FakeDatabase()
        repository = MongoMetadataRepository(database)
        repository.update_task_status("request-1", "running", timestamp=NOW)
        query, update = repository.tasks.updates[0]
        self.assertEqual(query, {"_id": "request-1"})
        self.assertEqual(update["$set"]["started_at"], NOW)
        self.assertEqual(update["$push"]["status_history"]["status"], "running")
        repository.ping()
        self.assertEqual(database.commands, ["ping"])


class MongoSettingsTests(unittest.TestCase):
    def test_disabled_by_default_and_uri_is_not_public(self):
        settings = MongoSettings.from_env({})
        self.assertFalse(settings.enabled)
        configured = MongoSettings.from_env(
            {
                "WAVEFORM_MONGODB_ENABLED": "true",
                "WAVEFORM_MONGODB_URI": "mongodb://secret@example/db",
            }
        )
        self.assertNotIn("secret", repr(configured))
        self.assertNotIn("uri", configured.public_values())
        self.assertTrue(configured.public_values()["uri_configured"])

    def test_enabled_requires_uri_and_required_requires_enabled(self):
        with self.assertRaises(MongoSettingsError):
            MongoSettings.from_env({"WAVEFORM_MONGODB_ENABLED": "true"})
        with self.assertRaises(MongoSettingsError):
            MongoSettings.from_env({"WAVEFORM_MONGODB_REQUIRED": "true"})


if __name__ == "__main__":
    unittest.main()
