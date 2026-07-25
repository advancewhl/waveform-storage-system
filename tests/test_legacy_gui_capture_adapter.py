from __future__ import annotations

import unittest

from waveform_storage.legacy.gui_capture_adapter import (
    read_legacy_frequency,
    send_legacy_message,
)


class LegacyGuiCaptureAdapterTests(unittest.TestCase):
    def test_frequency_and_message_delegate_preserve_values(self):
        class Frequency:
            data = [100]

        class Pv:
            def read(self):
                return Frequency()

        class Context:
            def get_pvs(self, name):
                self.name = name
                return [Pv()]

        self.assertEqual(read_legacy_frequency(Context), 100)
        calls = []

        def sender(**kwargs):
            calls.append(kwargs)
            return "accepted"

        response = send_legacy_message(
            sender,
            server_host="host",
            server_port=5000,
            message="100,10,['BPM_A']",
        )
        self.assertEqual(response, "accepted")
        self.assertEqual(
            calls,
            [{
                "server_host": "host",
                "server_port": 5000,
                "message": "100,10,['BPM_A']",
            }],
        )


if __name__ == "__main__":
    unittest.main()
