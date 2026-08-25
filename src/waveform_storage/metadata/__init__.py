"""Optional MongoDB-backed metadata catalog for waveform files."""

from waveform_storage.metadata.models import (
    CaptureTaskMetadata,
    RegistrySnapshotMetadata,
    StatusEvent,
    WaveformFileMetadata,
)
from waveform_storage.metadata.memory_repository import InMemoryMetadataRepository
from waveform_storage.metadata.repository import MetadataRepository
from waveform_storage.metadata.service import CaptureMetadataService
from waveform_storage.metadata.settings import MongoSettings

__all__ = [
    "CaptureTaskMetadata",
    "CaptureMetadataService",
    "InMemoryMetadataRepository",
    "MetadataRepository",
    "MongoSettings",
    "RegistrySnapshotMetadata",
    "StatusEvent",
    "WaveformFileMetadata",
]
