"""Numerical and source-contract regressions for a shared spectral service."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import time
import unittest
from zipfile import ZipFile

import h5py
import numpy as np

from waveform_storage.analysis.engine import analyze_channel
from waveform_storage.analysis.loaders import load_request
from waveform_storage.analysis.models import AnalysisConfig, AnalysisRequest, ChannelSeries
from waveform_storage.analysis.service import AnalysisJobs, run_analysis


def hdf_fixture(path, fs=1000, points=20000, *, kind="merged", missing_rate=False):
    times = np.arange(points) / fs
    signal = 2 * np.sin(2 * np.pi * 47.6 * times)
    ids = np.arange(points, dtype=np.uint64) * int(1_000_000 / fs) + 100000007
    with h5py.File(path, "w") as h:
        if not missing_rate:
            h.attrs["frequency_hz"] = fs
        if kind == "v2":
            h.attrs["schema_name"] = "site-user-waveform-alignment"
            h.attrs["schema_version"] = 2
            h.create_dataset("bunch_ids", data=ids)
            h.create_dataset("x", data=[signal])
            h.create_dataset("y", data=[signal / 2])
            h.create_dataset("valid_mask", data=np.ones((1, points), dtype=bool))
            h.create_dataset("device_ids", data=np.array(["BPM1"], dtype=object),
                             dtype=h5py.string_dtype())
        else:
            h.create_dataset("BunchIDData", data=[ids])
            h.create_dataset("Data", data=[signal, signal / 2] if kind == "single"
                             else [signal, signal / 2, signal / 3, signal / 4])
            if kind == "single":
                h.attrs["Device"] = "BPM1"
            else:
                h.attrs["Device"] = np.array(["BPM1", "BPM2"], dtype=object)
                for index, name in enumerate(["BPM1", "BPM2"]):
                    h.attrs[f"Data[0:]_{index}"] = name + "_X"
                    h.attrs[f"Data[1:]_{index}"] = name + "_Y"


class SpectrumNumericsTests(unittest.TestCase):
    def test_same_peak_and_physical_windows_at_500_and_1000_hz(self):
        for rate, blocks in [(500, 8), (1000, 4)]:
            t = np.arange(20000) / rate
            x = np.sin(2 * np.pi * 47.6 * t) + np.random.default_rng(12).normal(0, .1, len(t))
            result = analyze_channel(ChannelSeries("BPM_X", t, x, "memory", rate), AnalysisConfig())
            self.assertEqual(result.classification, "stable_candidate", result.reason)
            self.assertAlmostEqual(result.peak_hz, 47.6, places=5)
            self.assertAlmostEqual(result.resolution_hz, .2)
            self.assertEqual(result.block_count, blocks)

    def test_low_frequency_matches_original_algorithm_golden(self):
        golden = json.loads((Path(__file__).parent / "fixtures/spectrum-low-frequency-golden.json").read_text())
        t = np.arange(1800)
        x = 2 * np.sin(2 * np.pi * .152 * t) + np.random.default_rng(42).normal(0, .2, len(t))
        x[700] += 20
        for profile in ["low_v1", "low_v2"]:
            result = analyze_channel(ChannelSeries("reference", t, x, "memory", 1),
                                     AnalysisConfig.for_profile(profile))
            original = result.details["original_metrics"]
            for key, value in golden[profile].items():
                if isinstance(value, float):
                    self.assertAlmostEqual(original[key], value, places=7, msg=key)
                else:
                    self.assertEqual(original[key], value, key)

    def test_long_gap_and_nyquist_and_constant_are_invalid(self):
        t = np.arange(20000) / 1000
        x = np.sin(2 * np.pi * 47.6 * t)
        cases = [ChannelSeries("gap", np.r_[t[:5000], t[6000:]], np.r_[x[:5000], x[6000:]], "m", 1000),
                 ChannelSeries("alias", np.arange(1000) / 80, np.sin(np.arange(1000)), "m", 80),
                 ChannelSeries("constant", t, np.ones(len(t)), "m", 1000)]
        for series in cases:
            r = analyze_channel(series, AnalysisConfig())
            self.assertEqual(r.classification, "invalid")
            self.assertTrue(r.reason)

    def test_drifting_block_peaks_do_not_become_repeated_candidate(self):
        rate = 500
        t = np.arange(2500) / rate
        x = np.concatenate([np.sin(2 * np.pi * frequency * t) for frequency in [41, 44, 47, 49]])
        result = analyze_channel(ChannelSeries("drift", np.arange(len(x)) / rate, x, "m", rate), AnalysisConfig())
        self.assertNotEqual(result.classification, "stable_candidate")
        self.assertLess(result.block_hits, 3)

    def test_explicit_double_zero_policy_preserves_time_axis(self):
        t = np.arange(20000) / 1000
        x = np.sin(2 * np.pi * 47.6 * t)
        zeros = np.zeros(len(t), dtype=bool)
        zeros[1000] = True
        x[zeros] = 0
        series = ChannelSeries("zero", t, x, "m", 1000, metadata={"double_zero_mask": zeros})
        kept = analyze_channel(series, AnalysisConfig())
        masked = analyze_channel(series, replace(AnalysisConfig(), zero_policy="mask_double_zero"))
        self.assertEqual(kept.quality["removed_points"], 0)
        self.assertEqual(masked.quality["removed_points"], 1)
        self.assertEqual(kept.quality["resampled_points"], masked.quality["resampled_points"])


class SpectrumLoadersTests(unittest.TestCase):
    def test_three_hdf_layouts_keep_all_channels_and_rate(self):
        with TemporaryDirectory() as d:
            for kind, count in [("single", 2), ("merged", 4), ("v2", 2)]:
                p = Path(d) / (kind + ".h5")
                hdf_fixture(p, fs=500, kind=kind)
                channels = list(load_request(AnalysisRequest("hdf5", (str(p),))))
                self.assertEqual(len(channels), count)
                self.assertEqual(channels[0].sample_rate_hz, 500)
                self.assertAlmostEqual(channels[0].times[-1], 39.998)
                self.assertEqual(channels[0].time_basis, "relative")
                self.assertIsNone(channels[0].utc_origin)

    def test_old_hdf_requires_rate_and_conflicting_rate_fails(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "old.h5"
            hdf_fixture(p, missing_rate=True)
            channels = list(load_request(AnalysisRequest("hdf5", (str(p),))))
            self.assertIn("采样率", channels[0].error)
            channels = list(load_request(AnalysisRequest("hdf5", (str(p),), sample_rate_hz=1000)))
            self.assertEqual(len(channels), 4)
            hdf_fixture(p, fs=500)
            channels = list(load_request(AnalysisRequest("hdf5", (str(p),), sample_rate_hz=1000)))
            self.assertIn("不一致", channels[0].error)

    def test_bom_value_fallback_and_zip_use_real_utc_times(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "values.csv"
            p.write_text("\ufeffsmpl_time,float_val,num_val\n2026-10-01T08:00:00+08:00,1.5,99\n2026-10-01T08:00:01+08:00,,2.5\n", encoding="utf-8")
            channels = list(load_request(AnalysisRequest("archiver", (str(p),))))
            np.testing.assert_equal(channels[0].values, [1.5, 2.5])
            self.assertEqual(channels[0].utc_origin, "2026-10-01T00:00:00+00:00")
            z = Path(d) / "data.zip"
            with ZipFile(z, "w") as archive:
                archive.write(p, "BPM_X.csv")
            loaded = list(load_request(AnalysisRequest("archiver", (str(z),))))
            self.assertEqual(loaded[0].time_basis, "utc")
            self.assertEqual(loaded[0].channel, "BPM_X")

    def test_id_gap_is_not_compressed_and_bad_row_labels_fail(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "gap.h5"
            hdf_fixture(p)
            with h5py.File(p, "r+") as h:
                ids = h["BunchIDData"][()]
                ids[:, 100:] += 1000
                h["BunchIDData"][:] = ids
            channels = list(load_request(AnalysisRequest("hdf5", (str(p),))))
            self.assertAlmostEqual(channels[0].times[100] - channels[0].times[99], .002)
            with h5py.File(p, "r+") as h:
                h.attrs["Data[0:]_0"] = "BPM2_X"
            self.assertIn("行标签", list(load_request(AnalysisRequest("hdf5", (str(p),))))[0].error)


class SpectrumTaskTests(unittest.TestCase):
    def test_live_manager_cannot_be_mistaken_for_a_restarted_service(self):
        with TemporaryDirectory() as d:
            first = AnalysisJobs(d)
            try:
                with self.assertRaises(RuntimeError):
                    AnalysisJobs(d)
            finally:
                first.shutdown()
            second = AnalysisJobs(d)
            second.shutdown()

    def test_result_manifest_and_files_and_async_status(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "input.h5"
            hdf_fixture(p)
            request = AnalysisRequest("hdf5", (str(p),))
            jobs = AnalysisJobs(Path(d) / "results")
            try:
                job_id = jobs.submit(request)
                deadline = time.monotonic() + 10
                while jobs.get(job_id)["status"] in {"accepted", "running"} and time.monotonic() < deadline:
                    time.sleep(.01)
                state = jobs.get(job_id)
                self.assertEqual(state["status"], "succeeded", state)
                result = jobs.result(job_id)
                self.assertEqual(len(result.channels), 4)
                manifest = json.loads((result.directory / "run-manifest.json").read_text())
                self.assertEqual(manifest["config"]["block_seconds"], 5)
                self.assertTrue(manifest["source_files"][0]["sha256"])
                with ZipFile(result.directory / "results.zip") as archive:
                    self.assertIn("data-quality.csv", archive.namelist())
                    self.assertIn("spectra.npz", archive.namelist())
                with np.load(result.directory / "spectra.npz", allow_pickle=False) as arrays:
                    self.assertEqual(len(arrays), 8)
            finally:
                jobs.shutdown()

    def test_empty_input_and_cancelled_run_leave_no_partial_result(self):
        from threading import Event
        with TemporaryDirectory() as d:
            p = Path(d) / "input.h5"
            hdf_fixture(p)
            cancellation = Event()
            cancellation.set()
            from waveform_storage.analysis.service import AnalysisCancelled
            with self.assertRaises(AnalysisCancelled):
                run_analysis(AnalysisRequest("hdf5", (str(p),)), Path(d) / "results", cancel=cancellation)
            self.assertFalse(list((Path(d) / "results").iterdir()))

    def test_restart_retains_completed_results_and_marks_interrupted_jobs(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "input.h5"
            hdf_fixture(p)
            output = Path(d) / "results"
            jobs = AnalysisJobs(output)
            job_id = jobs.submit(AnalysisRequest("hdf5", (str(p),)))
            deadline = time.monotonic() + 10
            while jobs.get(job_id)["status"] in {"accepted", "running"} and time.monotonic() < deadline:
                time.sleep(.01)
            jobs.shutdown()
            state = jobs.get(job_id)
            interrupted = "11111111-1111-4111-8111-111111111111"
            state.update(job_id=interrupted, status="running")
            (output / "jobs" / (interrupted + ".json")).write_text(json.dumps(state))
            reopened = AnalysisJobs(output)
            try:
                self.assertEqual(reopened.get(interrupted)["status"], "failed")
                self.assertEqual(reopened.get(job_id)["status"], "succeeded")
                restored = reopened.result(job_id)
                self.assertEqual(len(restored.channels), 4)
                self.assertAlmostEqual(restored.channels[0].peak_hz, 47.6)
                self.assertTrue(len(restored.channels[0].frequency))
            finally:
                reopened.shutdown()


if __name__ == "__main__":
    unittest.main()
