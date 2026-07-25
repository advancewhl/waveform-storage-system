from __future__ import annotations

import io
import json
import logging
import unittest

from waveform_storage.logging_config import configure_logging, log_context


class LoggingConfigTests(unittest.TestCase):
    def test_structured_context_is_present_and_credentials_are_omitted(self):
        stream = io.StringIO()
        logger = configure_logging(logging.INFO, stream)
        log_context(
            logger,
            request_id="request-1",
            task_id="task-1",
            stage="align",
            device="BPM_A",
            duration=1.25,
            status="succeeded",
            error_type=None,
            password="must-not-appear",
        ).info("completed password=message-secret")
        payload = json.loads(stream.getvalue())
        for key in (
            "request_id", "task_id", "stage", "device", "duration",
            "status", "error_type",
        ):
            self.assertIn(key, payload)
        self.assertNotIn("must-not-appear", stream.getvalue())
        self.assertNotIn("message-secret", stream.getvalue())
