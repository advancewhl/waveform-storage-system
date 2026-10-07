"""Application services coordinating transports, I/O, and processing."""

__all__ = [
    "CaptureCommunicationService",
    "CaptureDispatchResult",
    "CapturePlan",
    "CaptureProtocolMode",
    "CaptureService",
    "RetrievalService",
    "StorageQuery",
]


def __getattr__(name):
    if name in {"CapturePlan", "CaptureService"}:
        from .capture_service import CapturePlan, CaptureService

        return {"CapturePlan": CapturePlan, "CaptureService": CaptureService}[name]
    if name in {
        "CaptureCommunicationService",
        "CaptureDispatchResult",
        "CaptureProtocolMode",
    }:
        from .communication_service import (
            CaptureCommunicationService,
            CaptureDispatchResult,
            CaptureProtocolMode,
        )

        return {
            "CaptureCommunicationService": CaptureCommunicationService,
            "CaptureDispatchResult": CaptureDispatchResult,
            "CaptureProtocolMode": CaptureProtocolMode,
        }[name]
    if name in {"RetrievalService", "StorageQuery"}:
        from .retrieval_service import RetrievalService, StorageQuery

        return {"RetrievalService": RetrievalService, "StorageQuery": StorageQuery}[name]
    raise AttributeError(name)
