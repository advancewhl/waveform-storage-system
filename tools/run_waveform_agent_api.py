"""Serve the read-only Waveform Agent API using the production Candidate backend."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'apps/operator_gui'))

from candidate_backend import CandidateOperatorBackend
from waveform_storage.analysis.service import AnalysisJobs
from waveform_storage.application.agent_service import WaveformAgentService
from waveform_storage.application.agent_http_api import make_server
from waveform_storage.config.settings import AppSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8771)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/agent-analysis')
    args = parser.parse_args()
    backend = CandidateOperatorBackend(AppSettings.from_env(project_root=ROOT))
    jobs = AnalysisJobs(args.output)
    service = None
    try:
        service = WaveformAgentService(backend, jobs)
        server = make_server(service, ('127.0.0.1', args.port))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        if service is not None:
            service.shutdown()
        jobs.shutdown()


if __name__ == '__main__':
    main()
