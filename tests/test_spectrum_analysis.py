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
from waveform_storage.analysis.status import display_status_for


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
            self.assertEqual(result.display_status, "clear_peak")
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
            self.assertEqual(result.display_status, "clear_peak")

    def test_display_status_policy_covers_all_five_states_and_keeps_invalid_separate(self):
        cases = [
            ("stable_candidate", False, "clear_peak"),
            ("observation", True, "probable_peak"),
            ("observation", False, "watch"),
            ("reject", False, "no_significant_peak"),
            ("invalid", True, "unanalysable"),
        ]
        for classification, overall_hit, expected in cases:
            with self.subTest(classification=classification, overall_hit=overall_hit):
                self.assertEqual(
                    display_status_for(classification, overall_hit=overall_hit), expected
                )

    def test_low_frequency_display_status_separates_peak_evidence_from_v2_stability(self):
        from unittest.mock import patch
        from waveform_storage.analysis.pv_engine.models import SpectrumAnalysisResult

        t = np.arange(1800, dtype=float)
        x = np.sin(2 * np.pi * .152 * t)
        cases = [
            ("low_v1", ["stable_block_count"], "probable_peak"),
            ("low_v2", ["stable_block_count", "stability_ratio"], "probable_peak"),
            ("low_v2", ["real_local_maximum", "stable_block_count"], "watch"),
        ]
        for profile, failed_rules, expected in cases:
            with self.subTest(profile=profile, failed_rules=failed_rules):
                analysis = SpectrumAnalysisResult(
                    pv_name="reference", classification="observation",
                    clean_peak_to_background_db=12.0, stable_block_count=2,
                    total_block_count=6, stability_ratio=1 / 3,
                    failed_rules=failed_rules, decision_reason="deterministic test evidence",
                )
                with patch(
                    "waveform_storage.analysis.pv_engine.spectrum.analyze_single_pv",
                    return_value=analysis,
                ):
                    result = analyze_channel(
                        ChannelSeries("reference", t, x, "memory", 1),
                        AnalysisConfig.for_profile(profile),
                    )
                self.assertEqual(result.classification, "observation")
                self.assertEqual(result.display_status, expected)

    def test_high_frequency_evidence_flows_into_display_status_deterministically(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        rate = 1000
        times = np.arange(20000, dtype=float) / rate
        values = np.ones(len(times), dtype=float)
        spectrum = SimpleNamespace(
            frequency=np.arange(61, dtype=float),
            psd=np.ones(61, dtype=float),
            delta_f_hz=1.0,
            actual_window_seconds=5.0,
        )
        hit = {"peak_hz": 47.6, "peak_to_background_db": 8.0,
               "is_local_maximum": True, "prominence_db": 4.0}
        miss = {"peak_hz": 47.6, "peak_to_background_db": 5.0,
                "is_local_maximum": True, "prominence_db": 4.0}
        cases = [
            ("整体谱峰明显但重复性不足", hit, [hit, hit, miss, miss],
             "observation", True, 2, "probable_peak"),
            ("整体不命中但有分块支持", miss, [hit, miss, miss, miss],
             "observation", False, 1, "watch"),
            ("没有显著谱峰或分块命中", miss, [miss, miss, miss, miss],
             "reject", False, 0, "no_significant_peak"),
        ]
        for label, overall, blocks, classification, overall_hit, block_hits, display in cases:
            with self.subTest(case=label):
                metrics = [dict(overall), *(dict(block) for block in blocks)]
                with patch(
                    "waveform_storage.analysis.engine._prepare",
                    return_value=(times, values, rate),
                ), patch(
                    "waveform_storage.analysis.engine.welch_density",
                    return_value=spectrum,
                ), patch(
                    "waveform_storage.analysis.engine._band_metrics",
                    side_effect=metrics,
                ):
                    result = analyze_channel(
                        ChannelSeries(label, times, values, "memory", rate),
                        AnalysisConfig(),
                    )

                self.assertEqual(result.classification, classification)
                self.assertEqual(result.block_hits, block_hits)
                evidence = result.details["status_evidence"]
                self.assertEqual(evidence["overall_hit"], overall_hit)
                self.assertFalse(evidence["repeated"])
                self.assertEqual(result.display_status, display)

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
                self.assertEqual(state["counts"]["stable_candidate"], 4)
                self.assertEqual(state["display_counts"]["clear_peak"], 4)
                result = jobs.result(job_id)
                self.assertEqual(len(result.channels), 4)
                manifest = json.loads((result.directory / "run-manifest.json").read_text())
                self.assertEqual(manifest["config"]["block_seconds"], 5)
                self.assertEqual(manifest["result_schema_version"], 2)
                self.assertEqual(manifest["display_status_schema_version"], 1)
                self.assertEqual(manifest["display_status_labels"]["clear_peak"], "谱峰明显")
                self.assertTrue(manifest["source_files"][0]["sha256"])
                with ZipFile(result.directory / "results.zip") as archive:
                    self.assertEqual(
                        set(archive.namelist()),
                        {"run-manifest.json", "results.json", "spectrum-summary.csv",
                         "data-quality.csv", "spectrum-blocks.csv", "spectra.npz", "report.md"},
                    )
                    records = json.loads(archive.read("results.json"))
                    self.assertEqual(records[0]["classification"], "stable_candidate")
                    self.assertEqual(records[0]["display_status"], "clear_peak")
                    report = archive.read("report.md").decode("utf-8")
                    self.assertIn("谱峰明显", report)
                    self.assertIn("谱峰（Hz）", report)
                    summary_csv = archive.read("spectrum-summary.csv").decode("utf-8-sig")
                    quality_csv = archive.read("data-quality.csv").decode("utf-8-sig")
                    self.assertIn("display_status", summary_csv.splitlines()[0])
                    self.assertIn("clear_peak", summary_csv)
                    self.assertIn("stable_candidate,clear_peak", quality_csv)
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

    def test_pre_v2_results_reload_with_conservative_legacy_observation_status(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "input.h5"
            hdf_fixture(p)
            output = Path(d) / "results"
            jobs = AnalysisJobs(output)
            try:
                job_id = jobs.submit(AnalysisRequest("hdf5", (str(p),)))
                deadline = time.monotonic() + 10
                while jobs.get(job_id)["status"] in {"accepted", "running"} and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(jobs.get(job_id)["status"], "succeeded")
            finally:
                jobs.shutdown()

            result_directory = output / job_id
            manifest_path = result_directory / "run-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key in ("result_schema_version", "display_status_schema_version",
                        "display_status_labels", "classification_compatibility"):
                manifest.pop(key, None)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            results_path = result_directory / "results.json"
            rows = json.loads(results_path.read_text(encoding="utf-8"))
            rows[0]["classification"] = "observation"
            rows[0].pop("display_status", None)
            rows[0]["details"].pop("status_evidence", None)
            results_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

            reopened = AnalysisJobs(output)
            try:
                restored = reopened.result(job_id)
                self.assertEqual(restored.channels[0].classification, "observation")
                self.assertEqual(restored.channels[0].display_status, "watch")
            finally:
                reopened.shutdown()


if __name__ == "__main__":
    unittest.main()
