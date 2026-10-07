"""Unified REST jobs retain source limits and return downloadable results."""

from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import json
import time
import unittest
from zipfile import ZipFile

from waveform_storage.analysis.http_api import make_server
from waveform_storage.analysis.service import AnalysisJobs
from test_spectrum_analysis import hdf_fixture


class SpectrumApiTests(unittest.TestCase):
    def test_shared_job_and_download_and_allowlisted_input(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "input"
            root.mkdir()
            hdf_fixture(root / "capture.h5")
            outside = Path(d) / "outside.h5"
            hdf_fixture(outside)
            jobs = AnalysisJobs(Path(d) / "results")
            server = make_server(jobs, [root], ("127.0.0.1", 0))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(method, path, payload=None):
                connection = HTTPConnection(*server.server_address, timeout=10)
                body = json.dumps(payload).encode() if payload is not None else None
                connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                status, data = response.status, response.read()
                connection.close()
                return status, data
            try:
                status, data = request("POST", "/api/analysis/jobs", {
                    "source_type": "hdf5", "paths": [str(root / "capture.h5")], "profile": "high_band"})
                self.assertEqual(status, 202, data)
                job_id = json.loads(data)["job_id"]
                path = "/api/analysis/jobs/" + job_id
                deadline = time.monotonic() + 10
                state = {}
                while time.monotonic() < deadline:
                    status, data = request("GET", path)
                    state = json.loads(data)
                    if state["status"] not in {"accepted", "running"}:
                        break
                    time.sleep(.01)
                self.assertEqual(state["status"], "succeeded", state)
                status, data = request("GET", path + "/artifacts/results.zip")
                self.assertEqual(status, 200)
                with ZipFile(BytesIO(data)) as archive:
                    self.assertIn("spectrum-summary.csv", archive.namelist())
                status, _ = request("POST", "/api/analysis/jobs", {
                    "source_type": "hdf5", "paths": [str(outside)]})
                self.assertEqual(status, 403)
                status, _ = request("GET", path + "/artifacts/secret.json")
                self.assertEqual(status, 404)
                (root / "escape.h5").symlink_to(outside)
                status, _ = request("POST", "/api/analysis/jobs", {
                    "source_type": "hdf5", "paths": [str(root)]})
                self.assertEqual(status, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                jobs.shutdown()


if __name__ == "__main__":
    unittest.main()
