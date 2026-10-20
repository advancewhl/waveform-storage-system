"""Serve a disposable 1000 Hz/20000-point Agent fixture, without Mongo or SSH."""
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))

from test_waveform_agent_api import agent_fixture
from waveform_storage.application.agent_http_api import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8771)
    args = parser.parse_args()
    with TemporaryDirectory() as directory, agent_fixture(Path(directory)) as (service, _repo):
        server = make_server(service, ('127.0.0.1', args.port))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
