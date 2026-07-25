from __future__ import annotations

import unittest
from pathlib import Path


class AppBoundaryTests(unittest.TestCase):
    def test_legacy_support_files_are_thin_facades(self):
        root = Path(__file__).resolve().parents[1] / "apps" / "operator_gui"
        zmq_source = (root / "zmq_client_dealer.py").read_text(encoding="utf-8")
        utils_source = (root / "utils.py").read_text(encoding="utf-8")
        self.assertNotIn("socket(", zmq_source)
        self.assertNotIn("zmq.Context", zmq_source)
        self.assertNotIn("h5py.File", utils_source)
        self.assertNotIn("Context()", utils_source)
        self.assertIn("legacy.zmq_client_dealer_adapter", zmq_source)
        self.assertIn("legacy.operator_utils_adapter", utils_source)


if __name__ == "__main__":
    unittest.main()
