"""Structured, credential-safe logging shared by new application modules."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone


CONTEXT_FIELDS = (
    "request_id",
    "task_id",
    "stage",
    "device",
    "duration",
    "status",
    "error_type",
)
SENSITIVE_PARTS = ("password", "secret", "token", "credential")
SENSITIVE_VALUE = re.compile(
    r"(?i)\b(password|secret|token|credential)\s*=\s*[^\s,;]+"
)


class StructuredFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": SENSITIVE_VALUE.sub(r"\1=<redacted>", record.getMessage()),
        }
        for field in CONTEXT_FIELDS:
            payload[field] = getattr(record, field, None)
        for key, value in record.__dict__.items():
            if any(part in key.lower() for part in SENSITIVE_PARTS):
                continue
            if key.startswith("waveform_"):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level=logging.INFO, stream=None):
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    logger = logging.getLogger("waveform_storage")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def log_context(logger, **context):
    safe = {
        key: value
        for key, value in context.items()
        if not any(part in key.lower() for part in SENSITIVE_PARTS)
    }
    return logging.LoggerAdapter(logger, safe)
