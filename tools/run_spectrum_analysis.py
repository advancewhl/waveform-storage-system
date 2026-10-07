"""Run the same spectrum service used by the GUI against local input files."""

import argparse
from dataclasses import fields
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from waveform_storage.analysis.models import AnalysisConfig, AnalysisRequest
from waveform_storage.analysis.service import run_analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("archiver", "hdf5"), required=True)
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "logs/spectrum-runs")
    parser.add_argument("--profile", choices=("low_v1", "low_v2", "high_band"), default="high_band")
    parser.add_argument("--sample-rate-hz", type=float)
    parser.add_argument("--config", type=Path, help="JSON 参数覆盖文件")
    args = parser.parse_args()
    overrides = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    allowed = {f.name for f in fields(AnalysisConfig)} - {"profile"}
    if not isinstance(overrides, dict) or set(overrides) - allowed:
        parser.error("配置中有未知字段")
    request = AnalysisRequest(args.source, tuple(args.input),
                              AnalysisConfig.for_profile(args.profile, **overrides), args.sample_rate_hz)
    run = run_analysis(request, args.output)
    invalid = sum(c.classification == "invalid" for c in run.channels)
    print(json.dumps({"job_id": run.job_id, "directory": str(run.directory),
                      "channels": len(run.channels), "invalid": invalid}, ensure_ascii=False))
    return 2 if invalid == len(run.channels) else 0


if __name__ == "__main__":
    raise SystemExit(main())
