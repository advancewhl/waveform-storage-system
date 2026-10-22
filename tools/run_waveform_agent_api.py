"""Serve Candidate-backed Agent APIs; WAVEFORM_AGENT_V2_ONLY=true disables v1."""
import argparse
from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'apps/operator_gui'))

from candidate_backend import CandidateOperatorBackend
from waveform_storage.analysis.service import AnalysisJobs
from waveform_storage.application.agent_service import WaveformAgentService
from waveform_storage.application.agent_http_api import make_server
from waveform_storage.config.settings import AppSettings
from waveform_storage.config.registry import load_registry
from waveform_storage.application.agent_v2_contract import AccessPolicy
from waveform_storage.application.agent_v2_service import WaveformAgentV2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8771)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/agent-analysis')
    parser.add_argument('--registry', type=Path, default=ROOT / 'config/devices.probe47.json')
    args = parser.parse_args()
    backend = CandidateOperatorBackend(AppSettings.from_env(project_root=ROOT))
    jobs = AnalysisJobs(args.output)
    service = None
    v2 = None
    try:
        service = WaveformAgentService(backend, jobs)
        # Existing v1-only deployments can start unchanged. Setting READ_TOKEN opts
        # into v2; write scopes require their distinct token, enable flag and secret.
        if os.environ.get('WAVEFORM_AGENT_READ_TOKEN'):
            v2 = WaveformAgentV2(service, load_registry(args.registry), AccessPolicy.from_env(os.environ),
                roots=[backend.settings.local_data_root] if backend.candidate_local_files else [],
                max_bytes=int(os.environ.get('WAVEFORM_AGENT_MAX_DOWNLOAD_BYTES', 64 * 1024 * 1024)),
                timeout=float(os.environ.get('WAVEFORM_AGENT_DOWNLOAD_TIMEOUT', 30)),
                max_duration=int(os.environ.get('WAVEFORM_AGENT_MAX_CAPTURE_SECONDS', 3600)),
                max_devices=int(os.environ.get('WAVEFORM_AGENT_MAX_CAPTURE_DEVICES', 32)))
        elif any(os.environ.get(k, '').lower() == 'true' for k in
                 ('WAVEFORM_AGENT_CONTROL_ENABLED', 'WAVEFORM_AGENT_DATA_ENABLED')):
            raise ValueError('Configure WAVEFORM_AGENT_READ_TOKEN to enable v2')
        server = make_server(service, ('127.0.0.1', args.port), v2=v2)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        if v2 is not None:
            v2.close()
        if service is not None:
            service.shutdown()
        jobs.shutdown()


if __name__ == '__main__':
    main()
