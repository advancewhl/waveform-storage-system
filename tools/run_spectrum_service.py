"""Serve the unified spectrum job API on loopback by default."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from waveform_storage.analysis.http_api import make_server
from waveform_storage.analysis.service import AnalysisJobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-root", action="append", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "logs/spectrum-api")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    jobs = AnalysisJobs(args.output)
    server = make_server(jobs, args.allow_root, (args.bind, args.port))
    print(f"频谱分析服务：http://{args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        jobs.shutdown()


if __name__ == "__main__":
    main()
